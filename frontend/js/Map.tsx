import { captureException } from "@sentry/browser";
import React, { memo, useEffect, createContext } from "react";
import { createRoot } from "react-dom/client";

import MapGL, {
  NavigationControl,
  GeolocateControl,
  AttributionControl,
  type LayerProps,
  type MapProps,
  useControl,
  useMap,
  Popup,
  type LngLat,
  type MapLayerMouseEvent,
  type PopupEvent,
} from "react-map-gl/maplibre";

import arrow from "data-url:../history-arrow.png";
import routeStopMarkerDarkLarge from "data-url:../route-stop-marker-dark-large.png";
import routeStopMarkerDark from "data-url:../route-stop-marker-dark.png";
import routeStopMarkerLarge from "data-url:../route-stop-marker-large.png";
import routeStopMarker from "data-url:../route-stop-marker.png";
import stopMarkerCircleSdf from "data-url:../stop-marker-circle-sdf.png";
import stopMarkerSdf from "data-url:../stop-marker-sdf.png";
import osmBright from "url:../osm_bright.json";
import "maplibre-gl/dist/maplibre-gl.css";
import "./maps.css";
import maplibreWorkerUrl from "worklet:./maplibre-worker.ts";
import {
  type ExpressionSpecification,
  type FilterSpecification,
  type LayerSpecification,
  type Map as MapLibreMap,
  type StyleSpecification,
  setWorkerUrl,
} from "maplibre-gl";
import { ErrorBoundary } from "./LoadingSorry";

setWorkerUrl(maplibreWorkerUrl);

// navigating away kills the worker, which maplibre reports as "Worker failed to load"
let pageHidden = false;
window.addEventListener("pagehide", () => {
  pageHidden = true;
});
window.addEventListener("pageshow", () => {
  pageHidden = false;
});

const imagesByName: { [imageName: string]: string } = {
  "stop-marker-sdf": stopMarkerSdf,
  "stop-marker-circle-sdf": stopMarkerCircleSdf,
  "route-stop-marker": routeStopMarker,
  "route-stop-marker-dark": routeStopMarkerDark,
  "route-stop-marker-large": routeStopMarkerLarge,
  "route-stop-marker-dark-large": routeStopMarkerDarkLarge,
  "history-arrow": arrow,
};

export const hasBearing: FilterSpecification = [
  "!=",
  ["get", "bearing"],
  ["literal", null],
];

// stops with no bearing to point in get a plain circle,
// instead of a rotated "route-stop-marker" image
export const routeStopCircleStyle = (darkMode: boolean): LayerProps => ({
  id: "stops-circle",
  type: "circle",
  filter: ["!", hasBearing],
  paint: {
    "circle-radius": 3,
    "circle-color": darkMode ? "#424242" : "#fff",
    "circle-stroke-width": 2.25,
    "circle-stroke-color": darkMode ? "#d6d6d6" : "#666",
  },
});

const mapStyles: { [key: string]: string } = {
  alidade_smooth: "Smooth",
  alidade_smooth_dark: "Smooth dark",
  osm_bright: "Bright",
  aws_satellite: "Satellite",
};

// Ordnance Survey maps only cover Great Britain
if (Intl.DateTimeFormat().resolvedOptions().timeZone === "Europe/London") {
  mapStyles.os_light = "Ordnance Survey light";
  mapStyles.os_dark = "Ordnance Survey night";
  mapStyles.protomaps_light = "Protomaps light";
  mapStyles.protomaps_dark = "Protomaps dark";
}

const protomapsTileJSON = "http://localhost:8080/gb.json";

type StyleSwitcherProps = {
  style: string;
  onChange: React.ChangeEventHandler<HTMLInputElement>;
};

class StyleSwitcher {
  style: string;
  handleChange: React.ChangeEventHandler<HTMLInputElement>;
  _container?: HTMLElement;

  constructor(props: StyleSwitcherProps) {
    this.style = props.style;
    this.handleChange = props.onChange;
  }

  onAdd() {
    this._container = document.createElement("div");

    const root = createRoot(this._container);
    root.render(
      <details className="maplibregl-ctrl maplibregl-ctrl-group map-style-switcher">
        <summary>Map style</summary>
        {Object.entries(mapStyles).map(([key, value]) => (
          <label key={key}>
            <input
              type="radio"
              value={key}
              name="map-style"
              defaultChecked={key === this.style}
              onChange={this.handleChange}
            />
            {value}
          </label>
        ))}
      </details>,
    );
    return this._container;
  }

  onRemove() {
    this._container?.parentNode?.removeChild(this._container);
  }
}

const StyleSwitcherControl = memo(function StyleSwitcherControl(
  props: StyleSwitcherProps,
) {
  useControl(() => new StyleSwitcher(props));

  return null;
});

export const ThemeContext = createContext("");

function MapChild({ onInit }: { onInit?: (map: MapLibreMap) => void }) {
  const { current: map } = useMap();

  useEffect(() => {
    if (map) {
      const _map = map.getMap();
      _map.keyboard.disableRotation();
      _map.touchZoomRotate.disableRotation();

      if (onInit) {
        onInit(_map);
      }

      _map.setMissingStyleImageResolver(async (id) => {
        if (id in imagesByName) {
          const image = new Image();
          image.src = imagesByName[id];
          await image.decode();
          if (!_map.hasImage(id)) {
            const sdf = id.endsWith("-sdf");
            // SDF icons are drawn at icon-size 0.5 instead, which doubles the
            // halo thickness (halos wider than 1 break overlapping icons)
            _map.addImage(id, image, {
              pixelRatio: sdf ? 1 : 2,
              sdf,
            });
          }
        }
      });

      return () => {
        _map.setMissingStyleImageResolver(null);
      };
    }
  });

  return null;
}

export default function BusTimesMap(
  props: MapProps & {
    onMapInit?: (map: MapLibreMap) => void;
  },
) {
  const darkModeQuery = window.matchMedia("(prefers-color-scheme: dark)");

  const [mapStyle, setMapStyle] = React.useState(() => {
    try {
      const style = localStorage.getItem("map-style");
      if (style && style in mapStyles) {
        return style;
      }
    } catch {
      // ignore
    }

    return darkModeQuery.matches ? "alidade_smooth_dark" : "alidade_smooth";
  });

  useEffect(() => {
    const handleChange = (e: MediaQueryListEvent) => {
      setMapStyle(e.matches ? "alidade_smooth_dark" : "alidade_smooth");
    };

    if (darkModeQuery.addEventListener) {
      darkModeQuery.addEventListener("change", handleChange);

      return () => {
        darkModeQuery.removeEventListener("change", handleChange);
      };
    }
  }, [darkModeQuery]);

  const handleMapStyleChange = React.useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const style = e.target.value;
      const defaultStyle = darkModeQuery.matches
        ? "alidade_smooth_dark"
        : "alidade_smooth";
      setMapStyle(style);
      try {
        if (style === defaultStyle) {
          localStorage.removeItem("map-style");
        } else {
          localStorage.setItem("map-style", style);
        }
      } catch {
        // ignore
      }
    },
    [darkModeQuery.matches],
  );

  const [basemaps, setBasemaps] =
    React.useState<typeof import("@protomaps/basemaps")>();

  useEffect(() => {
    if (mapStyle.startsWith("protomaps_") && !basemaps) {
      import("@protomaps/basemaps").then(setBasemaps);
    }
  }, [mapStyle, basemaps]);

  // memoised so react-map-gl doesn't call setStyle on every render
  const protomapsStyle = React.useMemo((): StyleSpecification | undefined => {
    if (!basemaps || !mapStyle.startsWith("protomaps_")) {
      return;
    }
    const flavor = mapStyle === "protomaps_dark" ? "dark" : "light";
    const theme = basemaps.namedFlavor(flavor);
    // whole word " bus " to avoid matching "Business Park"
    const busy: ExpressionSpecification = [
      "any",
      ...[" bus ", " buses ", "coach", "depot"].map(
        (word): ExpressionSpecification => [
          "in",
          word,
          ["concat", " ", ["downcase", ["coalesce", ["get", "name"], ""]], " "],
        ],
      ),
    ];
    const industrial: LayerSpecification = {
      id: "pois_industrial",
      type: "symbol",
      source: "protomaps",
      "source-layer": "pois",
      filter: [
        "all",
        ["==", ["get", "kind"], "industrial"],
        [">=", ["zoom"], ["case", busy, 15, 16]],
      ],
      layout: {
        "text-field": ["get", "name"],
        "text-font": [
          "case",
          busy,
          ["literal", ["Noto Sans Medium"]],
          ["literal", ["Noto Sans Regular"]],
        ],
        "text-size": 11,
        "text-max-width": 8,
      },
      paint: {
        "text-color": [
          "case",
          busy,
          theme.pois?.lapis ?? "#315bcf",
          theme.pois?.slategray ?? "#6a5b8f",
        ],
        "text-halo-color": theme.earth,
        "text-halo-width": 1,
      },
    };
    return {
      version: 8,
      sources: {
        protomaps: {
          type: "vector",
          url: protomapsTileJSON,
          attribution:
            '<a href="https://protomaps.com">Protomaps</a> © <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
        },
      },
      glyphs:
        "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
      sprite: `https://protomaps.github.io/basemaps-assets/sprites/v4/${flavor}`,
      layers: basemaps
        .layers("protomaps", theme, { lang: "en" })
        .flatMap((layer): LayerSpecification[] => {
          if (layer.id === "pois" && layer.type === "symbol") {
            return [
              {
                ...layer,
                filter: [
                  "all",
                  layer.filter as ExpressionSpecification,
                  ["!=", ["get", "kind"], "bus_stop"],
                ],
              },
              industrial,
            ];
          }
          return [layer];
        }),
    };
  }, [basemaps, mapStyle]);

  const [contextMenu, setContextMenu] = React.useState<LngLat>();

  const onContextMenu = (e: MapLayerMouseEvent | PopupEvent) => {
    if ("lngLat" in e) {
      setContextMenu(e.lngLat);
    } else {
      setContextMenu(undefined);
    }
  };

  useEffect(() => {
    document.body.classList.toggle(
      "dark-mode",
      mapStyle.endsWith("_dark") ||
        (mapStyle.endsWith("_satellite") && darkModeQuery.matches),
    );
  }, [mapStyle, darkModeQuery.matches]);

  let mapStyleURL: string | StyleSpecification | undefined =
    `https://tiles.stadiamaps.com/styles/${mapStyle}.json`;
  if (mapStyle.startsWith("protomaps_")) {
    mapStyleURL = protomapsStyle;
  } else if (mapStyle === "os_light") {
    mapStyleURL = "https://tiles.bustimes.org.uk/styles/light/style.json";
  } else if (mapStyle === "os_dark") {
    mapStyleURL = "https://tiles.bustimes.org.uk/styles/night/style.json";
  } else if (mapStyle === "osm_bright") {
    mapStyleURL = osmBright;
  } else if (mapStyle === "aws" || mapStyle === "aws_satellite") {
    const region = "eu-west-2";
    const style = "Satellite";
    const colorScheme = "Light";
    const apiKey =
      "v1.public.eyJqdGkiOiI2MjQzYzk5OS01MGEyLTRkMWMtODhiOS01MDJkZGM0YzhhMjgifYG7rwIXmwqKDMGq4w0KvLsj3jfpzAzas89W6R8tZkaQi2PguPStCuPqEqbnIEAUhqIWBe6IhpYKkA_VfIvMtVpSIZgX1ha-nxPBiC61thXiBHIdjhoUiUshZshcnP5-yw0Hui2GWYlejOaJn4EXQTwSslSrmCqZWa_zMtoRV0EUxYwpQ26nbadeEpXva7Ka3e6rDt0lcvBG7r_wAxtKji5L8XlmHU7lSqwQkM9sJQCYUJyHfuURYHiC_5C3_2xJ5_wASu0-EQjH8SDO1IxvqPe2M9gK8Nud8ji3iUOuPv2uyXuteB5cuKO7jKCfLN_jvBDgUJAZ7sbFGr59MUIOS7A.Mzc3ODIwNDMtZWI3YS00NWY5LThjNTktM2UwMmJlOGFhZmY3";
    mapStyleURL = `https://maps.geo.${region}.amazonaws.com/v2/styles/${style}/descriptor?key=${apiKey}&color-scheme=${colorScheme}`;
  }

  return (
    <ErrorBoundary>
      <ThemeContext.Provider value={mapStyle}>
        <MapGL
          {...props}
          // reuseMaps
          crossSourceCollisions={false}
          touchPitch={false}
          pitchWithRotate={false}
          dragRotate={false}
          maxZoom={18}
          mapStyle={mapStyleURL}
          RTLTextPlugin={""}
          attributionControl={false}
          onError={(e) => {
            if (!pageHidden) {
              captureException(e.error);
            }
          }}
          onContextMenu={onContextMenu}
        >
          <AttributionControl compact={false} position="top-right" />
          <NavigationControl showCompass={false} />
          <GeolocateControl trackUserLocation />
          <StyleSwitcherControl
            style={mapStyle}
            onChange={handleMapStyleChange}
          />
          <MapChild onInit={props.onMapInit} />

          {props.children}
          {contextMenu ? (
            <Popup
              longitude={contextMenu.lng}
              latitude={contextMenu.lat}
              onClose={onContextMenu}
            >
              <a
                href={`https://www.openstreetmap.org/#map=15/${contextMenu.lat}/${contextMenu.lng}`}
                rel="noopener noreferrer"
              >
                OpenStreetMap
              </a>
              <a
                href={`https://www.google.com/maps/search/?api=1&query=${contextMenu.lat},${contextMenu.lng}`}
                rel="noopener noreferrer"
              >
                Google Maps
              </a>
            </Popup>
          ) : null}
        </MapGL>
      </ThemeContext.Provider>
    </ErrorBoundary>
  );
}
