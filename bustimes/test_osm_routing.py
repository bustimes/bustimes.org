from unittest import TestCase

from .osm_routing import (
    Graph,
    Projection,
    bus_access,
    compare,
    directions,
    overpass_query,
)

# a grid of streets, in metres:
#
#  A(1) --10-- B(2) --11-- C(3)
#   |           |           |
#  14          15          16
#   |           |           |
#  D(4) --12-- E(5) --13-- F(6)

PROJECTION = Projection(1.29, 52.63)

NODES = {
    1: (0, 0),
    2: (500, 0),
    3: (1000, 0),
    4: (0, -300),
    5: (500, -300),
    6: (1000, -300),
}

WAYS = {
    10: [1, 2],
    11: [2, 3],
    12: [4, 5],
    13: [5, 6],
    14: [1, 4],
    15: [2, 5],
    16: [3, 6],
}


def lon_lat(x, y):
    return PROJECTION.inverse(x, y)


def make_osm(tags=None, relations=()):
    """tags - {way id: extra tags}"""
    tags = tags or {}
    elements = []
    for node_id, (x, y) in NODES.items():
        lon, lat = lon_lat(x, y)
        elements.append({"type": "node", "id": node_id, "lon": lon, "lat": lat})
    for way_id, nodes in WAYS.items():
        elements.append(
            {
                "type": "way",
                "id": way_id,
                "nodes": nodes,
                "tags": {"highway": "tertiary", **tags.get(way_id, {})},
            }
        )
    for i, (members, relation_tags) in enumerate(relations):
        elements.append(
            {
                "type": "relation",
                "id": 100 + i,
                "members": members,
                "tags": relation_tags,
            }
        )
    return {"elements": elements}


def way(ref, role=""):
    return {"type": "way", "ref": ref, "role": role}


def ys(link):
    return [PROJECTION.forward(*point)[1] for point in link.coords]


class OSMRoutingTest(TestCase):
    def test_overpass_query(self):
        query = overpass_query(1.2, 52.6, 1.3, 52.7)
        self.assertIn("[bbox:52.6,1.2,52.7,1.3]", query)
        self.assertIn("busway", query)

    def test_tags(self):
        self.assertTrue(bus_access({"highway": "residential"}))
        self.assertFalse(bus_access({"highway": "residential", "access": "private"}))
        self.assertTrue(
            bus_access({"highway": "residential", "access": "no", "psv": "yes"})
        )
        self.assertFalse(bus_access({"highway": "primary", "bus": "no"}))
        self.assertFalse(bus_access({"highway": "pedestrian"}))
        self.assertTrue(bus_access({"highway": "pedestrian", "bus": "designated"}))

        self.assertEqual(directions({"highway": "primary"}), (True, True))
        self.assertEqual(
            directions({"highway": "primary", "oneway": "yes"}), (True, False)
        )
        self.assertEqual(
            directions({"highway": "primary", "oneway": "-1"}), (False, True)
        )
        self.assertEqual(
            directions({"highway": "primary", "junction": "roundabout"}), (True, False)
        )
        self.assertEqual(
            directions({"highway": "primary", "oneway": "yes", "oneway:bus": "no"}),
            (True, True),
        )
        self.assertEqual(
            directions(
                {"highway": "primary", "oneway": "yes", "busway:right": "opposite_lane"}
            ),
            (True, True),
        )

    def test_compare(self):
        line = [lon_lat(0, 0), lon_lat(500, 0)]
        self.assertAlmostEqual(compare(line, line)["hausdorff"], 0, places=3)

        result = compare(line, [lon_lat(0, 30), lon_lat(500, 30)])
        self.assertAlmostEqual(result["hausdorff"], 30, delta=0.5)
        self.assertAlmostEqual(result["mean"], 30, delta=0.5)
        self.assertAlmostEqual(result["length_ratio"], 1, places=2)

        result = compare(line, [lon_lat(0, 0), lon_lat(250, 100), lon_lat(500, 0)])
        self.assertAlmostEqual(result["hausdorff"], 100, delta=0.5)

    def test_straight(self):
        graph = Graph(make_osm())
        links = graph.match([lon_lat(100, 10), lon_lat(600, 10), lon_lat(900, 10)])
        self.assertEqual(len(links), 2)
        self.assertAlmostEqual(links[0].length, 500, delta=1)
        self.assertAlmostEqual(links[1].length, 300, delta=1)
        self.assertAlmostEqual(max(ys(links[0])), 0, delta=1)
        self.assertEqual([round(offset) for offset in links[0].offsets], [10, 10])

        # links join up
        self.assertEqual(links[0].coords[-1], links[1].coords[0])

        self.assertEqual(graph.match([lon_lat(100, 10)]), [])

    def test_oneway(self):
        stops = [lon_lat(100, 10), lon_lat(600, 10)]

        # westbound only - have to go round the block
        graph = Graph(make_osm({10: {"oneway": "-1"}, 11: {"oneway": "-1"}}))
        link = graph.match(stops)[0]
        self.assertGreater(link.length, 1000)
        self.assertLess(min(ys(link)), -250)

        # except buses
        graph = Graph(
            make_osm(
                {
                    10: {"oneway": "-1", "oneway:bus": "no"},
                    11: {"oneway": "-1", "oneway:bus": "no"},
                }
            )
        )
        link = graph.match(stops)[0]
        self.assertAlmostEqual(link.length, 500, delta=1)

    def test_turn_restriction(self):
        stops = [lon_lat(100, 10), lon_lat(510, -200)]

        graph = Graph(make_osm())
        self.assertAlmostEqual(graph.match(stops)[0].length, 600, delta=1)

        no_right_turn = [
            way(10, "from"),
            {"type": "node", "ref": 2, "role": "via"},
            way(15, "to"),
        ]
        graph = Graph(
            make_osm(
                relations=[
                    (
                        no_right_turn,
                        {"type": "restriction", "restriction": "no_right_turn"},
                    )
                ]
            )
        )
        self.assertGreater(graph.match(stops)[0].length, 900)

        graph = Graph(
            make_osm(
                relations=[
                    (
                        no_right_turn,
                        {
                            "type": "restriction",
                            "restriction": "no_right_turn",
                            "except": "bus;bicycle",
                        },
                    )
                ]
            )
        )
        self.assertAlmostEqual(graph.match(stops)[0].length, 600, delta=1)

        # only straight on
        graph = Graph(
            make_osm(
                relations=[
                    (
                        [
                            way(10, "from"),
                            {"type": "node", "ref": 2, "role": "via"},
                            way(11, "to"),
                        ],
                        {"type": "restriction", "restriction": "only_straight_on"},
                    )
                ]
            )
        )
        self.assertGreater(graph.match(stops)[0].length, 900)

    def test_route_relation(self):
        # shortest is via B-E, but the X1 goes via C-F
        stops = [lon_lat(100, 10), lon_lat(900, -290)]
        osm = make_osm(
            {15: {"highway": "residential"}},
            relations=[
                (
                    [way(10), way(11), way(16), way(13, "forward"), way(13, "stop")],
                    {"type": "route", "route": "bus", "ref": "X1;X1A"},
                )
            ],
        )
        graph = Graph(osm)

        link = graph.match(stops, ("x1",))[0]
        self.assertAlmostEqual(link.length, 1300, delta=1)

        link = graph.match(stops, ("Y2",))[0]
        self.assertAlmostEqual(link.length, 1100, delta=1)

        link = graph.match(stops)[0]
        self.assertAlmostEqual(link.length, 1100, delta=1)

    def test_no_buses(self):
        stops = [lon_lat(100, 10), lon_lat(900, -290)]
        graph = Graph(make_osm({15: {"bus": "no"}}))
        self.assertAlmostEqual(graph.match(stops)[0].length, 1300, delta=1)

        graph = Graph(
            make_osm({15: {"highway": "service", "service": "parking_aisle"}})
        )
        self.assertAlmostEqual(graph.match(stops)[0].length, 1300, delta=1)

    def test_unmatched_stop(self):
        graph = Graph(make_osm())
        links = graph.match(
            [
                lon_lat(100, 10),
                lon_lat(400, 10),
                lon_lat(5000, 5000),  # miles away
                lon_lat(900, -290),
                lon_lat(600, -290),
            ]
        )
        self.assertAlmostEqual(links[0].length, 300, delta=1)
        self.assertIsNone(links[1])
        self.assertIsNone(links[2])
        self.assertAlmostEqual(links[3].length, 300, delta=1)

    def test_connector(self):
        # bus station off the road network
        graph = Graph(make_osm())
        link = graph.match([lon_lat(250, 80), lon_lat(400, 10)])[0]
        self.assertAlmostEqual(PROJECTION.forward(*link.coords[0])[1], 80, delta=0.1)
        self.assertAlmostEqual(link.length, 80 + 150, delta=1)

    def test_loop(self):
        # stop after the previous one on the same edge, but behind - go round the block
        graph = Graph(
            make_osm(
                {
                    10: {"oneway": "yes"},
                    11: {"oneway": "yes"},
                    16: {"oneway": "yes"},
                    14: {"oneway": "-1"},
                }
            )
        )
        links = graph.match([lon_lat(400, 10), lon_lat(100, 10)])
        self.assertGreater(links[0].length, 1000)
