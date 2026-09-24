import { ErrorBoundary, captureException } from "@sentry/react";
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
import routeStopMarkerDark from "data-url:../route-stop-marker-dark.png";
import routeStopMarker from "data-url:../route-stop-marker.png";
import stopMarkerCircle from "data-url:../stop-marker-circle.png";
import stopMarker from "data-url:../stop-marker.png";
import osmBright from "url:../osm_bright.json";
import maplibreWorkerUrl from "worklet:./maplibre-worker.ts";
import {
  type FilterSpecification,
  type Map as MapLibreMap,
  setWorkerUrl,
} from "maplibre-gl";
import { ErrorFallback } from "./LoadingSorry";

setWorkerUrl(maplibreWorkerUrl);

const imagesByName: { [imageName: string]: string } = {
  "stop-marker": stopMarker,
  "stop-marker-circle": stopMarkerCircle,
  "route-stop-marker": routeStopMarker,
  "route-stop-marker-dark": routeStopMarkerDark,
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
  os_light: "Ordnance Survey light",
  os_dark: "Ordnance Survey night",
};

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
            _map.addImage(id, image, {
              pixelRatio: 2,
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

  let mapStyleURL = `https://tiles.stadiamaps.com/styles/${mapStyle}.json`;
  if (mapStyle === "os_light") {
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
    <ErrorBoundary fallback={ErrorFallback}>
      <ThemeContext.Provider value={mapStyle}>
        <MapGL
          {...props}
          // reuseMaps
          crossSourceCollisions={false}
          touchPitch={false}
          pitchWithRotate={false}
          dragRotate={false}
          minZoom={4}
          maxZoom={18}
          // projection="globe"
          mapStyle={mapStyleURL}
          RTLTextPlugin={""}
          attributionControl={false}
          onError={(e) => captureException(e.error)}
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
