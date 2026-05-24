import React from "react";

import { Layer, type LayerProps, Source } from "react-map-gl/maplibre";

import { ThemeContext } from "./Map";
import type { TripTime } from "./TripTimetable";

type RouteProps = {
  times: TripTime[];
};

export const Route = React.memo(function Route({ times }: RouteProps) {
  const theme = React.useContext(ThemeContext);
  const darkMode = theme.endsWith("_dark") || theme.endsWith("_satellite");

  const stopsStyle: LayerProps = {
    id: "stops",
    type: "symbol",
    layout: {
      "symbol-sort-key": ["get", "priority"],
      "icon-image": [
        "case",
        ["==", ["get", "bearing"], ["literal", null]],
        darkMode ? "route-stop-marker-dark-circle" : "route-stop-marker-circle",
        darkMode ? "route-stop-marker-dark" : "route-stop-marker",
      ],
      "icon-rotate": ["+", 45, ["get", "bearing"]],
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    },
  };

  const routeStyle: LayerProps = {
    type: "line",
    paint: {
      "line-color": darkMode ? "#ddd" : "#666",
      "line-width": 3,
      // "line-dasharray": [1, 2],
    },
  };

  const lineStrings = [];

  for (const time of times) {
    if (time.track) {
      // wiggly line from previous stop to this one
      lineStrings.push(time.track);
    }
  }

  return (
    <React.Fragment>
      <Source
        type="geojson"
        data={{
          type: "FeatureCollection",
          features: lineStrings.map((lineString) => {
            return {
              type: "Feature",
              geometry: {
                type: "LineString",
                coordinates: lineString,
              },
              properties: null,
            };
          }),
        }}
      >
        <Layer {...routeStyle} />
      </Source>

      <Source
        type="geojson"
        data={{
          type: "FeatureCollection",
          features: times
            .filter((stop) => stop.stop.location)
            .map((stop) => {
              return {
                type: "Feature",
                geometry: {
                  type: "Point",
                  coordinates: stop.stop.location as [number, number],
                },
                properties: {
                  url: stop.stop.atco_code
                    ? `/stops/${stop.stop.atco_code}`
                    : null,
                  name: stop.stop.name,
                  bearing: stop.stop.bearing,
                  aimed_arrival_time: stop.aimed_arrival_time,
                  aimed_departure_time: stop.aimed_departure_time,
                  expected_arrival_time: stop.expected_arrival_time,
                  expected_departure_time: stop.expected_departure_time,
                  actual_departure_time: stop.actual_departure_time,
                  priority: stop.timing_status === "PTP" ? 0 : 1, // symbol-sort-key lower number - "higher" priority
                },
              };
            }),
        }}
      >
        <Layer {...stopsStyle} />
      </Source>
    </React.Fragment>
  );
});
