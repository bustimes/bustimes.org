import json

from django.db import connection
from django.test import TestCase

from .osm_postgis import corridor_wkt, load_osm
from .osm_routing import Graph
from .test_osm_routing import lon_lat, make_osm, way

# like what osm2pgsql makes with osm_roads.lua
CREATE_TABLES = """
CREATE SCHEMA osm;
CREATE TABLE osm.roads (
    way_id int8 NOT NULL,
    tags jsonb,
    nodes int8[] NOT NULL,
    geom geometry(LineString, 4326) NOT NULL
);
CREATE TABLE osm.relations (relation_id int8 NOT NULL, tags jsonb, members jsonb);
CREATE TABLE osm.relation_ways (relation_id int8 NOT NULL, way_id int8 NOT NULL);
"""

MEMBER_TYPES = {"node": "n", "way": "w", "relation": "r"}


def load_test_data(osm):
    """put Overpass-style data in the osm tables, like osm2pgsql would"""
    nodes = {
        element["id"]: (element["lon"], element["lat"])
        for element in osm["elements"]
        if element["type"] == "node"
    }
    with connection.cursor() as cursor:
        cursor.execute(CREATE_TABLES)
        for element in osm["elements"]:
            if element["type"] == "way":
                points = ",".join(
                    f"{nodes[node][0]} {nodes[node][1]}" for node in element["nodes"]
                )
                cursor.execute(
                    "INSERT INTO osm.roads VALUES (%s, %s, %s, "
                    "ST_GeomFromText(%s, 4326))",
                    [
                        element["id"],
                        json.dumps(element["tags"]),
                        element["nodes"],
                        f"LINESTRING({points})",
                    ],
                )
            elif element["type"] == "relation":
                members = [
                    {**member, "type": MEMBER_TYPES[member["type"]]}
                    for member in element["members"]
                ]
                cursor.execute(
                    "INSERT INTO osm.relations VALUES (%s, %s, %s)",
                    [element["id"], json.dumps(element["tags"]), json.dumps(members)],
                )
                for member in element["members"]:
                    if member["type"] == "way":
                        cursor.execute(
                            "INSERT INTO osm.relation_ways VALUES (%s, %s)",
                            [element["id"], member["ref"]],
                        )


class OSMPostGISTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        osm = make_osm(
            {15: {"highway": "residential"}},
            relations=[
                (
                    [way(10), way(11), way(16), way(13, "forward")],
                    {"type": "route", "route": "bus", "ref": "X1"},
                ),
                (
                    [
                        way(10, "from"),
                        {"type": "node", "ref": 2, "role": "via"},
                        way(15, "to"),
                    ],
                    {"type": "restriction", "restriction": "no_right_turn"},
                ),
            ],
        )
        load_test_data(osm)

    def test_corridor_wkt(self):
        self.assertEqual(
            corridor_wkt([[(1, 2), (3, 4)], [(5, 6)], []]),
            "MULTILINESTRING((1 2,3 4),(5 6,5 6))",
        )

    def test_load_osm(self):
        self.assertEqual(load_osm([[]]), {"elements": []})

        # just the top road
        osm = load_osm([[lon_lat(100, 10), lon_lat(900, 10)]], distance=0.001)
        ways = {e["id"]: e for e in osm["elements"] if e["type"] == "way"}
        self.assertEqual(set(ways), {10, 11, 15})
        self.assertEqual(ways[10]["tags"], {"highway": "tertiary"})
        self.assertEqual(ways[10]["nodes"], [1, 2])
        relations = [e for e in osm["elements"] if e["type"] == "relation"]
        self.assertEqual(len(relations), 2)
        self.assertEqual(
            {e["id"] for e in osm["elements"] if e["type"] == "node"},
            {1, 2, 3, 5},
        )

        # everything
        stops = [lon_lat(100, 10), lon_lat(900, -290)]
        graph = Graph(load_osm([stops]))
        self.assertAlmostEqual(graph.match(stops, ["X1"])[0].length, 1300, delta=1)

        # turn restriction
        stops = [lon_lat(100, 10), lon_lat(510, -200)]
        self.assertGreater(graph.match(stops)[0].length, 900)
