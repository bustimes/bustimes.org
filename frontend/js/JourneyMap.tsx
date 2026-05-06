import React from "react";

import {
  Layer,
  type LayerProps,
  type MapLayerMouseEvent,
  Popup,
  Source,
} from "react-map-gl/maplibre";

import BusTimesMap, { ThemeContext } from "./Map";

import type { Map as MapGL } from "maplibre-gl";
import LoadingSorry from "./LoadingSorry";
import StopPopup, { type Stop } from "./StopPopup";
import TripTimetable, { type TripTime, tripFromJourney } from "./TripTimetable";
import VehicleMarker, {
  type Vehicle,
  getClickedVehicleMarkerId,
} from "./VehicleMarker";
import VehiclePopup from "./VehiclePopup";
import { recordSkew } from "./clockSkew";
import { getBounds, getFont } from "./utils";

type VehicleJourneyLocation = {
  id: number;
  coordinates: [number, number];
  // delta: number | null;
  direction?: number | null;
  datetime: string;
};

export type StopTime = {
  id: number;
  atco_code: string;
  name: string;
  aimed_arrival_time: string;
  aimed_departure_time: string | null;
  minor: boolean;
  heading: number;
  coordinates?: [number, number] | null;
  actual_departure_time: string;
};

export type VehicleJourney = {
  id?: string;
  vehicle_id?: number;
  service_id?: number;
  trip_id?: number;
  datetime: string;
  route_name?: string;
  code: string;
  destination: string;
  direction: string;
  stops?: StopTime[];
  locations?: VehicleJourneyLocation[];
  vehicle?: string;
  current: boolean;
  next: {
    id: number;
    datetime: string;
  };
  previous: {
    id: number;
    datetime: string;
  };
};

// --- helpers ---

function haversine(
  [lng1, lat1]: [number, number],
  [lng2, lat2]: [number, number],
): number {
  const R = 6371000;
  const φ1 = (lat1 * Math.PI) / 180;
  const φ2 = (lat2 * Math.PI) / 180;
  const Δφ = ((lat2 - lat1) * Math.PI) / 180;
  const Δλ = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(Δφ / 2) ** 2 +
    Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function findNearestLocation(
  { lng, lat }: { lng: number; lat: number },
  locations: VehicleJourneyLocation[],
): VehicleJourneyLocation | null {
  if (!locations.length) return null;
  let best = locations[0];
  let bestDist =
    (lng - best.coordinates[0]) ** 2 + (lat - best.coordinates[1]) ** 2;
  for (let i = 1; i < locations.length; i++) {
    const loc = locations[i];
    const d =
      (lng - loc.coordinates[0]) ** 2 + (lat - loc.coordinates[1]) ** 2;
    if (d < bestDist) {
      bestDist = d;
      best = loc;
    }
  }
  return best;
}

// Returns a line-gradient expression with stops coloured by speed (blue=slow, red=fast).
// Returns null when there are fewer than 2 points or no distance variation.
function buildLineGradient(locations: VehicleJourneyLocation[]): unknown[] | null {
  if (locations.length < 2) return null;

  const cumDist: number[] = [0];
  const segSpeeds: number[] = [];
  for (let i = 1; i < locations.length; i++) {
    const dist = haversine(locations[i - 1].coordinates, locations[i].coordinates);
    const dt =
      (new Date(locations[i].datetime).getTime() -
        new Date(locations[i - 1].datetime).getTime()) /
      1000;
    cumDist.push(cumDist[i - 1] + dist);
    segSpeeds.push(dt > 0 ? (dist / dt) * 3.6 : 0); // km/h
  }

  const totalDist = cumDist[cumDist.length - 1];
  if (totalDist === 0) return null;

  const fractions = cumDist.map((d) => d / totalDist);

  // Smooth speed per point by averaging adjacent segment speeds.
  const pointSpeeds = locations.map((_, i) => {
    if (i === 0) return segSpeeds[0] ?? 0;
    if (i === locations.length - 1) return segSpeeds[segSpeeds.length - 1] ?? 0;
    return ((segSpeeds[i - 1] ?? 0) + (segSpeeds[i] ?? 0)) / 2;
  });

  const maxSpeed = Math.max(...pointSpeeds, 1);

  // hue: 240 (blue) = stopped, 0 (red) = fastest
  const stops: unknown[] = ["interpolate", ["linear"], ["line-progress"]];
  for (let i = 0; i < locations.length; i++) {
    const t = Math.min(pointSpeeds[i] / maxSpeed, 1);
    const hue = Math.round(240 - 240 * t);
    stops.push(fractions[i], `hsl(${hue}, 100%, 45%)`);
  }
  return stops;
}

// Speed in km/h between a location and the following one (falls back to preceding).
export function locationSpeed(
  loc: VehicleJourneyLocation,
  locations: VehicleJourneyLocation[],
): number | null {
  const idx = locations.indexOf(loc);
  if (idx < 0 || locations.length < 2) return null;
  const next = locations[idx + 1];
  const prev = locations[idx - 1];
  const ref = next ?? prev;
  if (!ref) return null;
  const dist = haversine(loc.coordinates, ref.coordinates);
  const dt =
    Math.abs(
      new Date(ref.datetime).getTime() - new Date(loc.datetime).getTime(),
    ) / 1000;
  return dt > 0 ? Math.round((dist / dt) * 3.6) : null;
}

// --- Locations layer ---

export const Locations = React.memo(function Locations({
  locations,
}: {
  locations: VehicleJourneyLocation[];
}) {
  const theme = React.useContext(ThemeContext);
  const darkMode = theme.endsWith("_dark") || theme.endsWith("_satellite");

  const gradient = React.useMemo(() => buildLineGradient(locations), [locations]);

  const coordinates = React.useMemo(
    () => locations.map((l) => l.coordinates),
    [locations],
  );

  const lineStyle: LayerProps = {
    id: "journey-line",
    type: "line",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-width": 3,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ...(gradient ? { "line-gradient": gradient as any } : { "line-color": darkMode ? "#eee" : "#666" }),
    },
  };

  // Arrows spaced along the line, auto-rotated to follow the line direction.
  // icon-rotate: 45 corrects for the history-arrow image's NW-pointing orientation.
  const arrowStyle: LayerProps = {
    id: "journey-arrows",
    type: "symbol",
    layout: {
      "symbol-placement": "line",
      "symbol-spacing": 60,
      "icon-image": "history-arrow",
      "icon-rotate": 45,
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    },
    paint: {
      "icon-opacity": darkMode ? 0.8 : 0.6,
    },
  };

  // Invisible wide hit area so mouse events fire anywhere near the line.
  const hitStyle: LayerProps = {
    id: "journey-line-hit",
    type: "line",
    paint: { "line-width": 20, "line-opacity": 0 },
  };

  return (
    <Source
      type="geojson"
      lineMetrics={true}
      data={{ type: "LineString", coordinates }}
    >
      <Layer {...lineStyle} />
      <Layer {...arrowStyle} />
      <Layer {...hitStyle} />
    </Source>
  );
});

export const JourneyStops = React.memo(function Stops({
  stops,
  clickedStopUrl,
  setClickedStop,
}: {
  stops: StopTime[];
  clickedStopUrl: string | undefined;
  setClickedStop: (s: string | undefined) => void;
}) {
  const theme = React.useContext(ThemeContext);
  const darkMode = theme.endsWith("_dark") || theme.endsWith("_satellite");

  const features = React.useMemo(() => {
    return stops
      .filter((s) => s.coordinates)
      .map((s) => {
        return {
          type: "Feature" as const,
          geometry: {
            type: "Point" as const,
            coordinates: s.coordinates as [number, number],
          },
          properties: {
            url: `/stops/${s.atco_code}`,
            name: s.name,
            heading: s.heading,
          },
        };
      });
  }, [stops]);

  const featuresByUrl = React.useMemo<
    { [url: string]: Stop } | undefined
  >(() => {
    return Object.assign(
      {},
      ...features.map((stop) => ({ [stop.properties.url]: stop })),
    );
  }, [features]);

  const clickedStop =
    featuresByUrl && clickedStopUrl && featuresByUrl[clickedStopUrl];

  return (
    <React.Fragment>
      <Source
        type="geojson"
        data={{
          type: "FeatureCollection",
          features: features,
        }}
      >
        <Layer
          {...{
            id: "stops",
            type: "symbol",
            layout: {
              // "symbol-sort-key": ["get", "priority"],
              "icon-rotate": ["+", 45, ["get", "heading"]],
              "icon-image": [
                "case",
                ["==", ["get", "heading"], ["literal", null]],
                darkMode
                  ? "route-stop-marker-dark-circle"
                  : "route-stop-marker-circle",
                darkMode ? "route-stop-marker-dark" : "route-stop-marker",
              ],
              // "icon-padding": 0,
              "icon-allow-overlap": true,
              "icon-ignore-placement": true,
            },
          }}
        />
      </Source>
      {clickedStop && (
        <StopPopup
          item={clickedStop}
          onClose={() => setClickedStop(undefined)}
        />
      )}
    </React.Fragment>
  );
});

function formatDatetime(datetime: string, contextDate?: string) {
  if (contextDate && datetime.startsWith(contextDate)) {
    return datetime.slice(11, 16); // just the time
  }
  return datetime.slice(0, 16).replace("T", " ");
}

function Sidebar({
  journey,
  loading,
  onMouseEnter,
  vehicle,
}: {
  journey: VehicleJourney;
  loading: boolean;
  onMouseEnter: (t: TripTime) => void;
  vehicle?: Vehicle;
}) {
  let className = "trip-timetable map-sidebar";
  if (loading) {
    className += " loading";
  }

  const trip = React.useMemo(() => {
    return tripFromJourney(journey);
  }, [journey]);

  let previousLink: React.ReactElement | string | undefined;
  let nextLink: React.ReactElement | string | undefined;
  const date = journey.datetime.slice(0, 10);

  if (journey) {
    if (journey.previous) {
      previousLink = formatDatetime(journey.previous.datetime, date);

      previousLink = (
        <p className="previous">
          <a href={`#journeys/${journey.previous.id}`}>&larr; {previousLink}</a>
        </p>
      );
    }
    if (journey.next) {
      nextLink = formatDatetime(journey.next.datetime, date);
      nextLink = (
        <p className="next">
          <a href={`#journeys/${journey.next.id}`}>{nextLink} &rarr;</a>
        </p>
      );
    }
  }

  let text = formatDatetime(journey.datetime);
  let reg = null;
  if (journey.vehicle) {
    reg = journey.vehicle;
    if (journey.vehicle.includes(" ")) {
      if (journey.vehicle.includes(" - ")) {
        const parts = journey.vehicle.split(" - ", 2);
        text += ` ${parts[0]}`;
        reg = <span className="reg">{parts[1]}</span>;
      }
    }
  } else {
    text += ` ${journey.route_name}`;
    if (journey.destination) {
      text += ` to ${journey.destination}`;
    }
  }

  return (
    <div className={className}>
      <div className="navigation">
        {previousLink}
        {nextLink}
      </div>
      <p>
        {text} {reg}
      </p>
      {trip ? (
        <TripTimetable
          onMouseEnter={onMouseEnter}
          trip={trip}
          vehicle={vehicle}
        />
      ) : (
        <p>{journey.code}</p>
      )}
    </div>
  );
}

function JourneyVehicle({
  vehicleId,
  // journey,
  onVehicleMove,
  clickedVehicleMarker,
  setClickedVehicleMarker,
}: {
  vehicleId: number;
  // journey: VehicleJourney;
  onVehicleMove: (v: Vehicle) => void;
  clickedVehicleMarker: boolean;
  setClickedVehicleMarker: (b: boolean) => void;
}) {
  const [vehicle, setVehicle] = React.useState<Vehicle>();

  React.useEffect(() => {
    if (vehicle) {
      onVehicleMove(vehicle);
    }
  }, [vehicle, onVehicleMove]);

  React.useEffect(() => {
    if (!vehicleId) {
      return;
    }

    let timeout: number;
    let current = true;

    const loadVehicle = () => {
      fetch(`/vehicles.json?id=${vehicleId}`).then((response) => {
        recordSkew(response);
        response.json().then((data: Vehicle[]) => {
          if (current && data && data.length) {
            setVehicle(data[0]);
            timeout = window.setTimeout(loadVehicle, 12000); // 12 seconds
          }
        });
      });
    };

    loadVehicle();

    return () => {
      current = false;
      clearTimeout(timeout);
    };
  }, [vehicleId]);

  if (!vehicle) {
    return null;
  }

  return (
    <React.Fragment>
      <VehicleMarker selected={clickedVehicleMarker} vehicle={vehicle} />
      {clickedVehicleMarker ? (
        <VehiclePopup
          item={vehicle}
          onClose={() => setClickedVehicleMarker(false)}
        />
      ) : null}
    </React.Fragment>
  );
}

export default function JourneyMap({
  journey,
  loading = false,
}: {
  journey?: VehicleJourney;
  loading: boolean;
}) {
  const [cursor, setCursor] = React.useState<string>();

  const [hoveredPoint, setHoveredPoint] =
    React.useState<VehicleJourneyLocation | null>(null);

  const [clickedStopUrl, setClickedStop] = React.useState<string>();

  const [clickedVehicleMarker, setClickedVehicleMarker] =
    React.useState<boolean>(true);

  const [liveLocations, setLiveLocations] = React.useState<
    VehicleJourneyLocation[]
  >([]);

  const [vehicle, setVehicle] = React.useState<Vehicle>();

  // All locations to display: historical + any live updates for a current journey.
  const allLocations = React.useMemo<VehicleJourneyLocation[]>(() => {
    if (!journey?.locations) return [];
    return journey.current
      ? journey.locations.concat(liveLocations)
      : journey.locations;
  }, [journey, liveLocations]);

  const handleVehicleMove = React.useCallback(
    (vehicle: Vehicle) => {
      if (
        !allLocations.length ||
        allLocations[allLocations.length - 1].datetime < vehicle.datetime
      ) {
        setLiveLocations((prev) =>
          prev.concat([
            {
              id: new Date(vehicle.datetime).getTime(),
              coordinates: vehicle.coordinates,
              datetime: vehicle.datetime,
              direction: vehicle.heading,
            },
          ]),
        );
        setVehicle(vehicle);
      }
    },
    [allLocations],
  );

  const onMouseMove = React.useCallback(
    (e: MapLayerMouseEvent) => {
      if (getClickedVehicleMarkerId(e)) {
        setHoveredPoint(null);
        return;
      }

      if (e.features?.length) {
        for (const feature of e.features) {
          if (feature.layer.id === "journey-line-hit") {
            setCursor("crosshair");
            setHoveredPoint(findNearestLocation(e.lngLat, allLocations));
            return;
          }
          if (feature.layer.id === "stops") {
            setCursor("pointer");
            setHoveredPoint(null);
            return;
          }
        }
      }

      setCursor(undefined);
      setHoveredPoint(null);
    },
    [allLocations],
  );

  const onMouseLeave = React.useCallback(() => {
    setCursor(undefined);
    setHoveredPoint(null);
  }, []);

  const handleMapClick = React.useCallback((e: MapLayerMouseEvent) => {
    const vehicleId = getClickedVehicleMarkerId(e);
    if (vehicleId) {
      setClickedVehicleMarker(true);
      setClickedStop(undefined);
      return;
    }

    setClickedVehicleMarker(false);

    if (e.features?.length) {
      for (const feature of e.features) {
        if (feature.layer.id === "stops") {
          setClickedStop(feature.properties.url);
          break;
        }
      }
    } else {
      setClickedStop(undefined);
    }
  }, []);

  const handleRowHover = React.useCallback((a: TripTime) => {
    if (a.stop.location && a.stop.atco_code) {
      setClickedStop(`/stops/${a.stop.atco_code}`);
    }
  }, []);

  const mapRef = React.useRef<MapGL | null>(null);

  const bounds = React.useMemo(() => {
    if (journey) {
      const bounds = getBounds(journey.stops, (item) => item.coordinates);
      return getBounds(journey.locations, (item) => item.coordinates, bounds);
    }
  }, [journey]);

  const onMapInit = React.useCallback((map: MapGL) => {
    // debugger;
    mapRef.current = map;
  }, []);

  React.useEffect(() => {
    if (bounds && mapRef.current) {
      mapRef.current.fitBounds(bounds, {
        padding: 50,
      });
    }
  }, [bounds]);

  if (!journey) {
    return <LoadingSorry />;
  }

  let className = "journey-map has-sidebar";
  if (!journey.stops) {
    className += " no-stops";
  }

  return (
    <React.Fragment>
      <div className={className}>
        {bounds ? (
          <BusTimesMap
            initialViewState={{
              bounds: bounds,
              fitBoundsOptions: {
                padding: 50,
              },
            }}
            cursor={cursor}
            onMouseEnter={onMouseMove}
            onMouseMove={onMouseMove}
            onMouseLeave={onMouseLeave}
            onClick={handleMapClick}
            onMapInit={onMapInit}
            interactiveLayerIds={["stops", "journey-line-hit"]}
          >
            {journey.stops ? (
              <JourneyStops
                stops={journey.stops}
                clickedStopUrl={clickedStopUrl}
                setClickedStop={setClickedStop}
              />
            ) : null}

            {allLocations.length > 0 ? (
              <Locations locations={allLocations} />
            ) : null}

            {journey.locations && journey.current ? (
              <JourneyVehicle
                vehicleId={window.VEHICLE_ID}
                onVehicleMove={handleVehicleMove}
                clickedVehicleMarker={clickedVehicleMarker}
                setClickedVehicleMarker={setClickedVehicleMarker}
              />
            ) : null}

            {hoveredPoint && (
              <Popup
                longitude={hoveredPoint.coordinates[0]}
                latitude={hoveredPoint.coordinates[1]}
                closeButton={false}
                anchor="bottom"
                offset={10}
                style={{ pointerEvents: "none" }}
              >
                <time dateTime={hoveredPoint.datetime}>
                  {hoveredPoint.datetime.slice(11, 19)}
                </time>
              </Popup>
            )}
          </BusTimesMap>
        ) : null}
      </div>
      <Sidebar
        loading={loading}
        journey={journey}
        onMouseEnter={handleRowHover}
        vehicle={vehicle}
      />
    </React.Fragment>
  );
}
