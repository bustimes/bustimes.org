"""Load OSM data for osm_routing.Graph from PostGIS, instead of the Overpass API.

The tables (in the "osm" schema) are made by osm2pgsql with the osm_roads.lua style,
from a Geofabrik extract filtered down to just the relevant bits:

    wget https://download.geofabrik.de/europe/united-kingdom/england-latest.osm.pbf
    wget https://download.geofabrik.de/europe/united-kingdom/wales-latest.osm.pbf
    osmium merge england-latest.osm.pbf wales-latest.osm.pbf -o gb.osm.pbf
    osmium tags-filter gb.osm.pbf \\
        w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,\\
secondary,secondary_link,tertiary,tertiary_link,busway,bus_guideway,unclassified,road,\\
residential,living_street,service,pedestrian,track \\
        r/route=bus,trolleybus,coach r/type=restriction \\
        -o roads.osm.pbf
    osm2pgsql --create --output=flex --style bustimes/osm_roads.lua \\
        --database bustimes roads.osm.pbf

(Run it again, with --create, to refresh the data. Geofabrik updates its extracts
daily, but please don't download them more often than you need to.)
"""

import json

from django.db import connections

MEMBER_TYPES = {"n": "node", "w": "way", "r": "relation"}


def from_json(value):
    # Django's psycopg backend leaves jsonb values as strings
    return json.loads(value) if isinstance(value, str) else value


ROADS_QUERY = """
SELECT way_id, tags, nodes, ST_AsGeoJSON(geom)
FROM osm.roads
WHERE ST_DWithin(geom, ST_GeomFromText(%(corridor)s, 4326), %(distance)s)
"""

RELATIONS_QUERY = """
SELECT relation_id, tags, members
FROM osm.relations
WHERE relation_id IN (
    SELECT relation_id FROM osm.relation_ways WHERE way_id = ANY(%(way_ids)s)
)
"""


def corridor_wkt(lines):
    """lines - lists of (longitude, latitude) - e.g. a service's stop sequences"""
    parts = []
    for line in lines:
        if len(line) == 1:
            line = [line[0], line[0]]
        if line:
            parts.append(
                "("
                + ",".join(f"{longitude} {latitude}" for longitude, latitude in line)
                + ")"
            )
    return f"MULTILINESTRING({','.join(parts)})"


def load_osm(lines, distance=0.02, using="default"):
    """Get roads (and relations) within distance (in degrees) of lines.
    Returns a dict like an Overpass API JSON response, for osm_routing.Graph"""
    elements = []
    nodes = {}
    way_ids = []

    if not any(lines):
        return {"elements": elements}

    with connections[using].cursor() as cursor:
        cursor.execute(
            ROADS_QUERY, {"corridor": corridor_wkt(lines), "distance": distance}
        )
        for way_id, tags, way_nodes, geojson in cursor.fetchall():
            coordinates = json.loads(geojson)["coordinates"]
            if len(way_nodes) != len(coordinates):
                # osm2pgsql drops repeated nodes when making a linestring
                way_nodes = [
                    node
                    for i, node in enumerate(way_nodes)
                    if i == 0 or node != way_nodes[i - 1]
                ]
                if len(way_nodes) != len(coordinates):
                    continue
            for node, (longitude, latitude) in zip(way_nodes, coordinates):
                nodes[node] = (longitude, latitude)
            elements.append(
                {
                    "type": "way",
                    "id": way_id,
                    "nodes": way_nodes,
                    "tags": from_json(tags),
                }
            )
            way_ids.append(way_id)

        if way_ids:
            cursor.execute(RELATIONS_QUERY, {"way_ids": way_ids})
            for relation_id, tags, members in cursor.fetchall():
                elements.append(
                    {
                        "type": "relation",
                        "id": relation_id,
                        "tags": from_json(tags),
                        "members": [
                            {
                                "type": MEMBER_TYPES.get(
                                    member["type"], member["type"]
                                ),
                                "ref": member["ref"],
                                "role": member["role"],
                            }
                            for member in from_json(members) or ()
                        ],
                    }
                )

    elements += [
        {"type": "node", "id": node, "lon": longitude, "lat": latitude}
        for node, (longitude, latitude) in nodes.items()
    ]
    return {"elements": elements}
