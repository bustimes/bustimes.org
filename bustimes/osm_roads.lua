-- osm2pgsql flex style - roads, bus route relations and turn restrictions,
-- for bustimes/osm_routing.py (via bustimes/osm_postgis.py)
--
-- see bustimes/osm_postgis.py for how to use it

-- keep in sync with osm_routing.Settings.road_factors
local HIGHWAYS = {
    motorway = true, motorway_link = true, trunk = true, trunk_link = true,
    primary = true, primary_link = true, secondary = true, secondary_link = true,
    tertiary = true, tertiary_link = true, busway = true, bus_guideway = true,
    unclassified = true, road = true, residential = true, living_street = true,
    service = true, pedestrian = true, track = true,
}

local ROUTES = { bus = true, trolleybus = true, coach = true }

local roads = osm2pgsql.define_table({
    name = 'roads',
    schema = 'osm',
    ids = { type = 'way', id_column = 'way_id' },
    columns = {
        { column = 'tags', type = 'jsonb' },
        { column = 'nodes', sql_type = 'int8[]', not_null = true },
        { column = 'geom', type = 'linestring', projection = 4326, not_null = true },
    },
    indexes = {
        { column = 'geom', method = 'gist' },
    },
})

-- bus routes and turn restrictions
local relations = osm2pgsql.define_table({
    name = 'relations',
    schema = 'osm',
    ids = { type = 'relation', id_column = 'relation_id' },
    columns = {
        { column = 'tags', type = 'jsonb' },
        { column = 'members', type = 'jsonb' },
    },
    indexes = {
        { column = 'relation_id', method = 'btree' },
    },
})

-- to find the relations for a set of roads
local relation_ways = osm2pgsql.define_table({
    name = 'relation_ways',
    schema = 'osm',
    ids = { type = 'relation', id_column = 'relation_id' },
    columns = {
        { column = 'way_id', sql_type = 'int8', not_null = true },
    },
    indexes = {
        { column = 'way_id', method = 'btree' },
    },
})

function osm2pgsql.process_way(object)
    if not HIGHWAYS[object.tags.highway] or object.tags.area == 'yes' then
        return
    end
    roads:insert({
        tags = object.tags,
        nodes = '{' .. table.concat(object.nodes, ',') .. '}',
        geom = object:as_linestring(),
    })
end

function osm2pgsql.process_relation(object)
    local type = object.tags.type
    if type == 'route' then
        if not ROUTES[object.tags.route] then
            return
        end
    elseif type ~= 'restriction' then
        return
    end

    local members = {}
    for _, member in ipairs(object.members) do
        members[#members + 1] = { type = member.type, ref = member.ref, role = member.role }
        if member.type == 'w' then
            relation_ways:insert({ way_id = member.ref })
        end
    end
    relations:insert({ tags = object.tags, members = members })
end
