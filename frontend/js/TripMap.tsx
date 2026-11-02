import React from "react";

import type {
  CircleLayerSpecification,
  ExpressionSpecification,
  MapSourceDataEvent,
} from "maplibre-gl";
import { Layer, type LayerProps, Source, useMap } from "react-map-gl/maplibre";

import { ThemeContext, hasBearing, routeStopCircleStyle } from "./Map";
import type { TripTime } from "./TripTimetable";

type RouteProps = {
  times: TripTime[];
  hoveredStopUrl?: string;
  clickedStopUrl?: string;
};

const sourceId = "route-stops";

// set by feature state, not a filter, so the marker changes immediately
// instead of after the tile has been re-laid-out
const highlighted: ExpressionSpecification = [
  "boolean",
  ["feature-state", "highlighted"],
  false,
];

export const Route = React.memo(function Route({
  times,
  hoveredStopUrl,
  clickedStopUrl,
}: RouteProps) {
  const theme = React.useContext(ThemeContext);
  const darkMode = theme.endsWith("_dark") || theme.endsWith("_satellite");

  const { current: map } = useMap();

  React.useEffect(() => {
    const urls = [hoveredStopUrl, clickedStopUrl].filter(
      (url): url is string => !!url,
    );
    const m = map?.getMap();
    if (!m || !urls.length) {
      return;
    }
    const apply = () => {
      if (!m.getSource(sourceId)) {
        return;
      }
      for (const id of urls) {
        m.setFeatureState({ source: sourceId, id }, { highlighted: true });
      }
      // on top of everything else (e.g. a vehicle's trail)
      const order = m.getLayersOrder();
      if (
        order.includes("stops-highlighted") &&
        order[order.length - 1] !== "stops-highlighted"
      ) {
        m.moveLayer("stops-highlighted");
      }
    };
    // the source may not exist yet, or may be re-added when the style changes
    const onSourceData = (e: MapSourceDataEvent) => {
      if (e.sourceId === sourceId) {
        apply();
      }
    };
    apply();
    m.on("sourcedata", onSourceData);
    return () => {
      m.off("sourcedata", onSourceData);
      if (m.getSource(sourceId)) {
        for (const id of urls) {
          m.setFeatureState({ source: sourceId, id }, { highlighted: false });
        }
      }
    };
  }, [map, hoveredStopUrl, clickedStopUrl]);

  const markerImage = darkMode ? "route-stop-marker-dark" : "route-stop-marker";

  const stopsStyle: LayerProps = {
    id: "stops",
    type: "symbol",
    filter: hasBearing,
    layout: {
      "symbol-sort-key": ["get", "priority"],
      "icon-image": markerImage,
      "icon-rotate": ["+", 45, ["get", "bearing"]],
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    },
  };

  const highlightedStopsStyle: LayerProps = {
    ...stopsStyle,
    id: "stops-highlighted",
    layout: { ...stopsStyle.layout, "icon-image": `${markerImage}-large` },
    paint: { "icon-opacity": ["case", highlighted, 1, 0] },
  };

  const circleStyle = routeStopCircleStyle(
    darkMode,
  ) as CircleLayerSpecification;
  circleStyle.paint = {
    ...circleStyle.paint,
    "circle-radius": ["case", highlighted, 5, 3],
  };

  const routeStyle: LayerProps = {
    type: "line",
    paint: {
      "line-color": darkMode ? "#ddd" : "#666",
      "line-width": 3,
      // "line-dasharray": [1, 2],
    },
  };

  const lineStrings = React.useMemo(
    () => ({
      type: "FeatureCollection" as const,
      features: times
        .filter((time) => time.track) // wiggly line from previous stop to this one
        .map((time) => ({
          type: "Feature" as const,
          geometry: {
            type: "LineString" as const,
            coordinates: time.track as [number, number][],
          },
          properties: null,
        })),
    }),
    [times],
  );

  const stops = React.useMemo(
    () => ({
      type: "FeatureCollection" as const,
      features: times
        .filter((stop) => stop.stop.location)
        .map((stop) => ({
          type: "Feature" as const,
          geometry: {
            type: "Point" as const,
            coordinates: stop.stop.location as [number, number],
          },
          properties: {
            url: stop.stop.atco_code ? `/stops/${stop.stop.atco_code}` : null,
            name: stop.stop.name,
            bearing: stop.stop.bearing,
            aimed_arrival_time: stop.aimed_arrival_time,
            aimed_departure_time: stop.aimed_departure_time,
            expected_arrival_time: stop.expected_arrival_time,
            expected_departure_time: stop.expected_departure_time,
            actual_departure_time: stop.actual_departure_time,
            priority: stop.timing_status === "PTP" ? 0 : 1, // symbol-sort-key lower number - "higher" priority
          },
        })),
    }),
    [times],
  );

  return (
    <React.Fragment>
      <Source type="geojson" data={lineStrings}>
        <Layer {...routeStyle} />
      </Source>

      <Source type="geojson" id={sourceId} promoteId="url" data={stops}>
        <Layer {...circleStyle} />
        <Layer {...stopsStyle} />
        <Layer {...highlightedStopsStyle} />
      </Source>
    </React.Fragment>
  );
});
