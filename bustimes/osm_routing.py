"""Route geometry from a trip's stop sequence and OpenStreetMap data, after pfaedle.

Unlike snap.py, which map matches a vehicle's GPS trace, this needs only the stops -
"sparse map matching", as described in Bast & Brosi, "Sparse Map-Matching in Public
Transit Networks with Turn Restrictions" (SIGSPATIAL 2018) and implemented in pfaedle
(https://github.com/ad-freiburg/pfaedle):

1. build a directed routing graph from OSM ways buses can use, honouring one-way
   streets (and bus contraflow exemptions) and turn restrictions
2. find candidate positions for each stop on nearby edges
3. choose one candidate per stop by minimising the total cost over the whole trip
   (Viterbi over a hidden Markov model) - an "emission" cost for each candidate's
   distance from its stop, plus a "transition" cost for the cheapest path between
   consecutive candidates
4. path costs are length weighted by road type, plus turn penalties, with a discount
   on ways in an OSM bus route relation for the same line

Pure Python with no dependencies. Input is an Overpass API JSON response (see
OVERPASS_QUERY) and a list of (longitude, latitude) stop positions.
"""

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import pairwise

EARTH_RADIUS = 6_371_000
INFINITY = math.inf

OVERPASS_QUERY = """[out:json][timeout:300][bbox:{south},{west},{north},{east}];
(way[highway~"^({highways})$"];>;);
out body qt;
rel[type=route][route~"^(bus|trolleybus|coach)$"];
out body qt;
rel[type=restriction];
out body qt;"""


def overpass_query(west, south, east, north, settings=None):
    settings = settings or Settings()
    return OVERPASS_QUERY.format(
        south=south,
        west=west,
        north=north,
        east=east,
        highways="|".join(settings.road_factors),
    )


@dataclass
class Settings:
    # cost per metre by highway=* (pfaedle has similar per-"level" penalties)
    road_factors: dict = field(
        default_factory=lambda: {
            "motorway": 1.0,
            "motorway_link": 1.0,
            "trunk": 1.0,
            "trunk_link": 1.0,
            "primary": 1.0,
            "primary_link": 1.0,
            "secondary": 1.0,
            "secondary_link": 1.0,
            "tertiary": 1.1,
            "tertiary_link": 1.1,
            "busway": 1.0,
            "bus_guideway": 1.0,
            "unclassified": 1.2,
            "road": 1.2,
            "residential": 1.3,
            "living_street": 1.6,
            "service": 1.6,
            "pedestrian": 2.0,  # only if bus=yes etc
            "track": 2.5,  # only if bus=yes etc
        }
    )
    service_factors: dict = field(
        default_factory=lambda: {
            "bus": 1.0,
            "alley": 2.5,
            "driveway": 3.0,
            "parking_aisle": 3.0,
            "drive-through": 5.0,
        }
    )
    line_factor: float = 0.6  # on a route relation with the same ref as the line
    route_factor: float = 1.0  # on some other bus route relation
    turn_cost: float = 10.0  # per 90° of turn
    u_turn_cost: float = 1000.0
    emission_weight: float = 2.0  # cost per metre of stop from candidate
    wrong_side_cost: float = 25.0  # stop on the right of a two-way road (UK)
    search_radius: float = 100.0
    fallback_radius: float = 300.0  # e.g. bus stations off the road network
    max_candidates: int = 8
    connector_threshold: float = 30.0  # add straight lines to stops further away
    # don't search further than this (in cost units, roughly metres) between stops
    min_search_limit: float = 3000.0
    search_limit_factor: float = 5.0  # x straight line distance


NEEDS_EXPLICIT_ACCESS = {"pedestrian", "track"}
YES = {"yes", "designated", "permissive", "destination", "true", "1"}
NO = {"no", "private", "agricultural", "forestry", "delivery", "discouraged"}


def refs(value):
    return {ref.strip().lower() for ref in value.replace(",", ";").split(";")} - {""}


def bus_access(tags):
    for key in ("bus", "psv"):
        if tags.get(key) in YES:
            return True
        if tags.get(key) in NO:
            return False
    if tags["highway"] in NEEDS_EXPLICIT_ACCESS or tags.get("area") == "yes":
        return False
    for key in ("motor_vehicle", "motorcar", "vehicle", "access"):
        if tags.get(key) in NO:
            return False
        if tags.get(key) in YES:
            return True
    return True


def directions(tags):
    """(forward, backward) - may a bus travel along/against the way's direction?"""
    if tags.get("oneway:bus") == "no" or tags.get("oneway:psv") == "no":
        return True, True
    if any(
        key.startswith("busway") and value == "opposite_lane"
        for key, value in tags.items()
    ) or any(
        key.startswith(("bus:lanes:backward", "psv:lanes:backward")) for key in tags
    ):
        return True, True
    oneway = tags.get("oneway")
    if oneway in ("yes", "true", "1"):
        return True, False
    if oneway in ("-1", "reverse"):
        return False, True
    if oneway == "no":
        return True, True
    if tags.get("junction") in ("roundabout", "circular") or tags["highway"] in (
        "motorway",
        "motorway_link",
    ):
        return True, False
    return True, True


# geometry, in metres on a local equirectangular projection


class Projection:
    def __init__(self, longitude, latitude):
        self.longitude = longitude
        self.latitude = latitude
        self.ky = math.radians(1) * EARTH_RADIUS
        self.kx = self.ky * math.cos(math.radians(latitude))

    def forward(self, longitude, latitude):
        return (
            (longitude - self.longitude) * self.kx,
            (latitude - self.latitude) * self.ky,
        )

    def inverse(self, x, y):
        return (self.longitude + x / self.kx, self.latitude + y / self.ky)


def length(coords):
    return sum(math.dist(a, b) for a, b in pairwise(coords))


def locate(coords, point):
    """(distance along, distance from, side) of the closest point on coords to point.
    side is > 0 if point is on the left"""
    best = (0, INFINITY, 0)
    along = 0
    px, py = point
    for (x1, y1), (x2, y2) in pairwise(coords):
        dx, dy = x2 - x1, y2 - y1
        seg = math.hypot(dx, dy)
        if seg:
            t = min(max(((px - x1) * dx + (py - y1) * dy) / seg**2, 0), 1)
            offset = math.hypot(x1 + t * dx - px, y1 + t * dy - py)
            if offset < best[1]:
                best = (along + t * seg, offset, dx * (py - y1) - dy * (px - x1))
        along += seg
    return best


def substring(coords, start, end):
    """the part of coords from start to end metres along"""
    result = []
    along = 0
    for a, b in pairwise(coords):
        seg = math.dist(a, b)
        if seg:
            if not result and along + seg >= start:
                t = (start - along) / seg
                result.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
            if result:
                if along + seg >= end:
                    t = (end - along) / seg
                    result.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
                    return result
                result.append(b)
        along += seg
    if not result:
        result.append(coords[-1])
    if len(result) == 1:
        result.append(result[0])
    return result


def bearing(a, b):
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))


def end_bearings(coords):
    """bearing at the start and at the end, ignoring zero length segments"""
    segments = [(a, b) for a, b in pairwise(coords) if a != b]
    if not segments:
        return 0, 0
    return bearing(*segments[0]), bearing(*segments[-1])


def densify(coords, step=10):
    result = [coords[0]]
    for a, b in pairwise(coords):
        n = max(1, math.ceil(math.dist(a, b) / step))
        for i in range(1, n + 1):
            result.append((a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n))
    return result


def compare(coords_a, coords_b, projection=None):
    """Compare two (longitude, latitude) lines. Returns a dict with
    hausdorff - furthest either line gets from the other (metres)
    mean - average distance of each line from the other (metres)
    length_ratio - length of a / length of b"""
    if projection is None:
        projection = Projection(*coords_b[0])
    a = [projection.forward(*point) for point in coords_a]
    b = [projection.forward(*point) for point in coords_b]
    a_to_b = [locate(b, point)[1] for point in densify(a)]
    b_to_a = [locate(a, point)[1] for point in densify(b)]
    return {
        "hausdorff": max(max(a_to_b), max(b_to_a)),
        "mean": (sum(a_to_b) + sum(b_to_a)) / (len(a_to_b) + len(b_to_a)),
        "length_ratio": length(a) / max(length(b), 1),
    }


# the graph


@dataclass(eq=False)
class Edge:
    id: int
    way: int
    u: int  # from node
    v: int  # to node
    coords: list
    length: float
    factor: float
    piece: int  # the undirected bit of way this is (one of) the direction(s) of
    start_bearing: float = 0
    end_bearing: float = 0
    two_way: bool = False


@dataclass(eq=False)
class Candidate:
    edge: Edge
    along: float  # metres along the edge
    offset: float  # metres from the stop
    cost: float  # emission cost
    point: tuple  # the stop's projected position


@dataclass
class Link:
    """A matched route between two consecutive stops"""

    coords: list  # (longitude, latitude)
    length: float  # metres
    cost: float
    offsets: tuple  # metres from each stop to the matched route


class Graph:
    def __init__(self, osm, settings=None):
        """osm - an Overpass API JSON response"""
        self.settings = settings or Settings()

        nodes = {}
        ways = {}
        relations = []
        for element in osm["elements"]:
            match element["type"]:
                case "node":
                    nodes[element["id"]] = (element["lon"], element["lat"])
                case "way":
                    ways[element["id"]] = element
                case "relation":
                    relations.append(element)

        if nodes:
            longitudes = [lon for lon, _ in nodes.values()]
            latitudes = [lat for _, lat in nodes.values()]
            self.projection = Projection(
                (min(longitudes) + max(longitudes)) / 2,
                (min(latitudes) + max(latitudes)) / 2,
            )
        else:
            self.projection = Projection(0, 0)

        restrictions = []
        self.way_refs = defaultdict(set)  # way id: bus route relation refs
        for relation in relations:
            tags = relation.get("tags", {})
            if tags.get("type") == "route":
                route_refs = refs(tags.get("ref", "")) or {""}
                for member in relation["members"]:
                    if member["type"] == "way" and member.get("role") in (
                        "",
                        "forward",
                        "backward",
                        None,
                    ):
                        self.way_refs[member["ref"]] |= route_refs
            elif tags.get("type") == "restriction":
                restrictions.append(relation)

        usable = {}
        for way_id, way in ways.items():
            tags = way.get("tags", {})
            highway = tags.get("highway")
            if highway not in self.settings.road_factors or not bus_access(tags):
                continue
            factor = self.settings.road_factors[highway]
            if highway == "service" and "service" in tags:
                factor = self.settings.service_factors.get(tags["service"], factor)
            if tags.get("bus") in YES or tags.get("psv") in YES:
                factor = min(factor, 1.0)
            usable[way_id] = (way["nodes"], factor, directions(tags))

        # graph vertices - way ends, junctions and restriction via nodes
        node_uses = defaultdict(int)
        for way_nodes, _, _ in usable.values():
            for node in way_nodes:
                node_uses[node] += 1
            node_uses[way_nodes[0]] += 1
            node_uses[way_nodes[-1]] += 1
        for relation in restrictions:
            for member in relation["members"]:
                if member["type"] == "node" and member.get("role") == "via":
                    node_uses[member["ref"]] += 2

        self.edges = []
        self.out_edges = defaultdict(list)
        self.way_edges = defaultdict(list)
        self.pieces = []  # (coords, [edges])

        for way_id, (way_nodes, factor, (forward, backward)) in usable.items():
            piece_nodes = []
            for node in way_nodes:
                if node not in nodes:  # outside the downloaded area?
                    self.add_piece(
                        way_id, piece_nodes, nodes, factor, forward, backward
                    )
                    piece_nodes = []
                    continue
                piece_nodes.append(node)
                if len(piece_nodes) > 1 and node_uses[node] > 1:
                    self.add_piece(
                        way_id, piece_nodes, nodes, factor, forward, backward
                    )
                    piece_nodes = [node]
            self.add_piece(way_id, piece_nodes, nodes, factor, forward, backward)

        # turn restrictions
        self.banned_turns = set()  # (from edge id, to edge id)
        self.only_turns = defaultdict(set)  # from edge id: {allowed to edge ids}
        for relation in restrictions:
            tags = relation.get("tags", {})
            if "bus" in tags.get("except", "") or "psv" in tags.get("except", ""):
                continue
            restriction = (
                tags.get("restriction:bus")
                or tags.get("restriction:psv")
                or tags.get("restriction")
            )
            if not restriction or not restriction.startswith(("no_", "only_")):
                continue
            members = defaultdict(list)
            for member in relation["members"]:
                members[member.get("role")].append(member)
            if len(members["via"]) != 1 or members["via"][0]["type"] != "node":
                continue  # via way restrictions not supported
            via = members["via"][0]["ref"]
            from_edges = [
                edge.id
                for member in members["from"]
                for edge in self.way_edges.get(member["ref"], ())
                if edge.v == via
            ]
            to_edges = [
                edge.id
                for member in members["to"]
                for edge in self.way_edges.get(member["ref"], ())
                if edge.u == via
            ]
            for from_edge in from_edges:
                if restriction.startswith("no_"):
                    self.banned_turns.update((from_edge, to) for to in to_edges)
                else:
                    self.only_turns[from_edge].update(to_edges)

        # spatial index
        self.cell_size = 100
        self.grid = defaultdict(set)
        for i, (coords, _) in enumerate(self.pieces):
            for a, b in pairwise(coords):
                x1, x2 = sorted((a[0], b[0]))
                y1, y2 = sorted((a[1], b[1]))
                for cx in range(
                    math.floor(x1 / self.cell_size), math.floor(x2 / self.cell_size) + 1
                ):
                    for cy in range(
                        math.floor(y1 / self.cell_size),
                        math.floor(y2 / self.cell_size) + 1,
                    ):
                        self.grid[cx, cy].add(i)

    def add_piece(self, way_id, piece_nodes, nodes, factor, forward, backward):
        if len(piece_nodes) < 2:
            return
        coords = [self.projection.forward(*nodes[node]) for node in piece_nodes]
        piece_length = length(coords)
        piece = len(self.pieces)
        edges = []
        for u, v, edge_coords, ok in (
            (piece_nodes[0], piece_nodes[-1], coords, forward),
            (piece_nodes[-1], piece_nodes[0], coords[::-1], backward),
        ):
            if not ok:
                continue
            start_bearing, end_bearing = end_bearings(edge_coords)
            edge = Edge(
                id=len(self.edges),
                way=way_id,
                u=u,
                v=v,
                coords=edge_coords,
                length=piece_length,
                factor=factor,
                piece=piece,
                start_bearing=start_bearing,
                end_bearing=end_bearing,
                two_way=forward and backward,
            )
            self.edges.append(edge)
            self.out_edges[u].append(edge)
            self.way_edges[way_id].append(edge)
            edges.append(edge)
        self.pieces.append((coords, edges))

    def rate(self, edge, line_refs):
        """cost per metre of an edge"""
        route_refs = self.way_refs.get(edge.way)
        if route_refs is None:
            return edge.factor
        if line_refs and line_refs & route_refs:
            return self.settings.line_factor
        return min(edge.factor, self.settings.route_factor)

    def turn_cost(self, edge, next_edge):
        if next_edge.piece == edge.piece and next_edge.u == edge.v:
            return self.settings.u_turn_cost
        angle = abs((next_edge.start_bearing - edge.end_bearing + 180) % 360 - 180)
        if angle > 150:
            return self.settings.u_turn_cost
        return self.settings.turn_cost * angle / 90

    def allowed(self, edge, next_edge):
        if (edge.id, next_edge.id) in self.banned_turns:
            return False
        only = self.only_turns.get(edge.id)
        return not only or next_edge.id in only

    def candidates(self, point, radius):
        """candidate positions for a (projected) stop on edges within radius"""
        settings = self.settings
        cx, cy = (
            math.floor(point[0] / self.cell_size),
            math.floor(point[1] / self.cell_size),
        )
        cells = math.ceil(radius / self.cell_size)
        pieces = set()
        for dx in range(-cells, cells + 1):
            for dy in range(-cells, cells + 1):
                pieces |= self.grid.get((cx + dx, cy + dy), set())
        candidates = []
        for piece in pieces:
            coords, edges = self.pieces[piece]
            along, offset, side = locate(coords, point)
            if offset > radius:
                continue
            piece_length = edges[0].length
            for edge in edges:
                if edge.coords is coords:
                    edge_along, edge_side = along, side
                else:  # reverse direction
                    edge_along, edge_side = piece_length - along, -side
                cost = settings.emission_weight * offset
                if edge.two_way and edge_side < 0 and offset > 3:
                    cost += settings.wrong_side_cost
                candidates.append(Candidate(edge, edge_along, offset, cost, point))
        candidates.sort(key=lambda candidate: candidate.cost)
        return candidates[: settings.max_candidates]

    def search(self, source, targets, line_refs, limit):
        """one to many shortest paths from source to targets (candidates).
        Returns {target index: (cost, edge before target edge or None)}, prev"""
        results = {}
        target_edges = defaultdict(list)
        for i, target in enumerate(targets):
            target_edges[target.edge.id].append((i, target.along))
        rate = self.rate(source.edge, line_refs)
        for i, along in target_edges.get(source.edge.id, ()):
            if along >= source.along:
                results[i] = ((along - source.along) * rate, None)

        start_cost = (source.edge.length - source.along) * rate
        dist = {source.edge.id: start_cost}
        prev = {source.edge.id: None}
        heap = [(start_cost, source.edge.id)]
        while heap:
            cost, edge_id = heapq.heappop(heap)
            if cost > dist[edge_id]:
                continue
            if cost > limit:
                break
            if len(results) == len(targets) and cost >= max(
                result[0] for result in results.values()
            ):
                break
            edge = self.edges[edge_id]
            for next_edge in self.out_edges[edge.v]:
                if not self.allowed(edge, next_edge):
                    continue
                enter_cost = cost + self.turn_cost(edge, next_edge)
                next_rate = self.rate(next_edge, line_refs)
                for i, along in target_edges.get(next_edge.id, ()):
                    total = enter_cost + along * next_rate
                    if i not in results or total < results[i][0]:
                        results[i] = (total, edge_id)
                next_cost = enter_cost + next_edge.length * next_rate
                if next_cost < dist.get(next_edge.id, INFINITY):
                    dist[next_edge.id] = next_cost
                    prev[next_edge.id] = edge_id
                    heapq.heappush(heap, (next_cost, next_edge.id))
        return results, prev

    def path_coords(self, source, target, before, prev):
        """projected coords of the path from source to target candidates"""
        if before is None:  # both on the same edge
            return substring(source.edge.coords, source.along, target.along)
        edge_ids = []
        while before is not None:
            edge_ids.append(before)
            before = prev[before]
        edge_ids.reverse()
        coords = substring(source.edge.coords, source.along, source.edge.length)
        for edge_id in edge_ids[1:]:
            coords += self.edges[edge_id].coords[1:]
        coords += substring(target.edge.coords, 0, target.along)[1:]
        return coords

    def match(self, stops, line_names=()):
        """stops - list of (longitude, latitude)
        line_names - e.g. ("25",), to prefer OSM route relations with that ref

        returns a list of len(stops) - 1 Links (or None where matching failed)"""
        settings = self.settings
        if len(stops) < 2:
            return []
        line_refs = set().union(*(refs(name) for name in line_names))
        points = [self.projection.forward(*stop) for stop in stops]
        stop_candidates = [
            self.candidates(point, settings.search_radius)
            or self.candidates(point, settings.fallback_radius)
            for point in points
        ]

        links = [None] * (len(stops) - 1)

        # Viterbi, restarting where no path can be found
        run_start = 0
        scores = [candidate.cost for candidate in stop_candidates[0]]
        backpointers = []  # per transition: {b: (a, before, prev, cost)}

        for i in range(len(stops) - 1):
            sources = stop_candidates[i]
            targets = stop_candidates[i + 1]
            new_scores = [INFINITY] * len(targets)
            back = {}
            if sources and targets:
                limit = max(
                    settings.min_search_limit,
                    settings.search_limit_factor * math.dist(points[i], points[i + 1]),
                )
                for a, source in enumerate(sources):
                    if scores[a] == INFINITY:
                        continue
                    results, prev = self.search(source, targets, line_refs, limit)
                    for b, (cost, before) in results.items():
                        score = scores[a] + cost + targets[b].cost
                        if score < new_scores[b]:
                            new_scores[b] = score
                            back[b] = (a, before, prev, cost)
            if back:
                backpointers.append(back)
                scores = new_scores
            else:
                self.backtrack(links, stop_candidates, run_start, scores, backpointers)
                run_start = i + 1
                scores = [candidate.cost for candidate in targets]
                backpointers = []
        self.backtrack(links, stop_candidates, run_start, scores, backpointers)
        return links

    def backtrack(self, links, stop_candidates, run_start, scores, backpointers):
        if not backpointers:
            return
        b = min(range(len(scores)), key=scores.__getitem__)
        for offset in range(len(backpointers) - 1, -1, -1):
            i = run_start + offset  # link from stop i to stop i + 1
            a, before, prev, cost = backpointers[offset][b]
            source = stop_candidates[i][a]
            target = stop_candidates[i + 1][b]
            coords = self.path_coords(source, target, before, prev)
            if source.offset > self.settings.connector_threshold:
                coords.insert(0, source.point)
            if target.offset > self.settings.connector_threshold:
                coords.append(target.point)
            links[i] = Link(
                coords=[self.projection.inverse(*point) for point in coords],
                length=length(coords),
                cost=cost,
                offsets=(source.offset, target.offset),
            )
            b = a
