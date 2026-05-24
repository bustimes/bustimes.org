import React from "react";

import {
  Layer,
  type LayerProps,
  type MapLayerMouseEvent,
  Source,
} from "react-map-gl/maplibre";

import BusTimesMap, { ThemeContext } from "./Map";

import type { Map as MapGL } from "maplibre-gl";
import LoadingSorry from "./LoadingSorry";
import StopPopup from "./StopPopup";
import { Route } from "./TripMap";
import TripTimetable, { type Trip, type TripTime } from "./TripTimetable";
import VehicleMarker, {
  type Vehicle,
  getClickedVehicleMarkerId,
} from "./VehicleMarker";
import VehiclePopup from "./VehiclePopup";
import { recordSkew } from "./clockSkew";
import { decodeTimeAwarePolyline } from "./time-aware-polyline";
import { getBounds } from "./utils";

export type VehicleJourneyLocation = {
  coordinates: [number, number];
  direction?: number | null;
  datetime: string;
};

export type VehicleJourney = {
  id?: string | number;
  date: string;
  datetime: string;
  vehicle?: {
    id: number;
    slug: string;
    fleet_code: string;
    reg: string;
  };
  route_name?: string;
  destination?: string;
  trip_id?: number | null;
  trip?: Trip;
  times?: TripTime[];
  time_aware_polyline?: string;
  service?: {
    id: number;
    slug: string;
  };
  operator?: {
    noc: string;
    slug: string;
    name: string;
  };
  live?: Vehicle[];
};

function calcBearing(
  [lng1, lat1]: [number, number],
  [lng2, lat2]: [number, number],
): number {
  const φ1 = (lat1 * Math.PI) / 180;
  const φ2 = (lat2 * Math.PI) / 180;
  const Δλ = ((lng2 - lng1) * Math.PI) / 180;
  const y = Math.sin(Δλ) * Math.cos(φ2);
  const x =
    Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

// Parse "+HH:MM" / "-HH:MM" / "Z" offset from an ISO datetime string.
// Used so the client doesn't need to trust the browser's local time zone
// (some privacy-focused browsers force UTC).
export function getUtcOffsetSeconds(isoStr: string): number {
  const m = isoStr.match(/([+-])(\d{2}):?(\d{2})$/);
  if (!m) return 0;
  const sign = m[1] === "-" ? -1 : 1;
  return (
    sign * (Number.parseInt(m[2], 10) * 3600 + Number.parseInt(m[3], 10) * 60)
  );
}

export function locationsFromPolyline(
  polyline: string,
  utcOffsetSeconds = 0,
): VehicleJourneyLocation[] {
  const points = decodeTimeAwarePolyline(polyline);
  const offsetMs = utcOffsetSeconds * 1000;
  return points.map(([lat, lng, ts], i) => {
    const coordinates: [number, number] = [lng, lat];
    const prev = points[i - 1] || [lat, lng];
    const next = points[i + 1] || [lat, lng];
    const direction = calcBearing([prev[1], prev[0]], [next[1], next[0]]);
    return {
      coordinates: [lng, lat],
      datetime: new Date(ts + offsetMs).toISOString(),
      direction,
    };
  });
}

export const Locations = React.memo(function Locations({
  locations,
}: {
  locations: VehicleJourneyLocation[];
}) {
  const theme = React.useContext(ThemeContext);
  const darkMode = theme.endsWith("_dark") || theme.endsWith("_satellite");

  const routeStyle: LayerProps = {
    type: "line",
    paint: {
      "line-color": "#54c",
      "line-width": 4,
    },
  };

  const locationsStyle: LayerProps = {
    id: "locations",
    type: "symbol",
    layout: {
      "icon-rotate": ["+", 45, ["get", "heading"]],
      "icon-image": "history-arrow",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "icon-anchor": "top-left",
      "icon-padding": [4],
      "icon-offset": [-4, -4],
    },
  };

  return (
    <React.Fragment>
      <Source
        type="geojson"
        data={{
          type: "LineString",
          coordinates: locations.map((l) => l.coordinates),
        }}
      >
        <Layer {...routeStyle} />
      </Source>
      <Source
        type="geojson"
        id="locations"
        data={{
          type: "FeatureCollection",
          features: locations.map((l) => {
            return {
              type: "Feature",
              id: l.datetime,
              geometry: {
                type: "Point",
                coordinates: l.coordinates,
              },
              properties: {
                heading: l.direction,
                time: l.datetime.slice(11, 19),
              },
            };
          }),
        }}
      >
        <Layer {...locationsStyle} />
      </Source>
    </React.Fragment>
  );
});
