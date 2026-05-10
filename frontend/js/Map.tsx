import { ErrorBoundary, captureException } from "@sentry/react";
import React, { memo, useEffect, createContext } from "react";
import { createRoot } from "react-dom/client";

import MapGL, {
  NavigationControl,
  GeolocateControl,
  AttributionControl,
  type MapProps,
  useControl,
  useMap,
  Popup,
  type LngLat,
  type MapLayerMouseEvent,
  type PopupEvent,
} from "react-map-gl/maplibre";

import arrow from "data-url:../history-arrow.png";
import linePattern from "data-url:../line-pattern.png";
import routeStopMarkerCircle from "data-url:../route-stop-marker-circle.png";
import routeStopMarkerDarkCircle from "data-url:../route-stop-marker-dark-circle.png";
import routeStopMarkerDark from "data-url:../route-stop-marker-dark.png";
import routeStopMarker from "data-url:../route-stop-marker.png";
import stopMarkerCircle from "data-url:../stop-marker-circle.png";
import stopMarker from "data-url:../stop-marker.png";
import osmBright from "url:../osm_bright.json";
import type {
  Map as MapLibreMap,
  MapStyleImageMissingEvent,
} from "maplibre-gl";
import { ErrorFallback } from "./LoadingSorry";

const imagesByName: { [imageName: string]: string } = {
  "stop-marker": stopMarker,
  "stop-marker-circle": stopMarkerCircle,
  "route-stop-marker": routeStopMarker,
  "route-stop-marker-circle": routeStopMarkerCircle,
  "route-stop-marker-dark": routeStopMarkerDark,
  "route-stop-marker-dark-circle": routeStopMarkerDarkCircle,
  "history-arrow": arrow,
  "line-pattern": linePattern,
};

const mapStyles: { [key: string]: string } = {
  alidade_smooth: "Smooth",
  alidade_smooth_dark: "Smooth dark",
  // alidade_satellite: "Satellite",
  osm_bright: "Bright",
  // outdoors: "Outdoors",
  aws: "Traffic",
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

      const onStyleImageMissing = (e: MapStyleImageMissingEvent) => {
        if (e.id in imagesByName) {
          const image = new Image();
          image.src = imagesByName[e.id];
          image.onload = () => {
            if (!map.hasImage(e.id)) {
              map.addImage(e.id, image, {
                pixelRatio: 2,
              });
            }
          };
        }
      };

      map.on("styleimagemissing", onStyleImageMissing);

      return () => {
        map.off("styleimagemissing", onStyleImageMissing);
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
    const region = "eu-west-1";
    let style = "Standard";
    let traffic = "&traffic=All";
    if (mapStyle === "aws_satellite") {
      style = "Hybrid";
      traffic = "";
    }
    // const colorScheme = "Light";
    const apiKey =
      "v1.public.eyJqdGkiOiIzN2Q2N2JhYi05NTYyLTRlOGItYjQ4Zi1iMDE4OTk3ZTExODUifX12J0dnJVXJbfadbzrJW3oeYvqHGJxm0iSO2aUyyDSZVER5A7gOTdKF5-iQxaqDcRIkJTZ4rIxdGqXVLG-MkDWi8n8jWEkIBploD6QX0lEp-dtl4cd0lhfcXfBgar8kgJCaBPcjaglztZs_SXOVWIgQmlY5hSzVxBnoezvFxW2dk7BBzlRREHscAjP9Oyx_c3wUJReYAc4rA8JxXWYVyLbe9a-FgapbrgQkSTKbjPChPfesLZjTZek1FChtCNs4EDOg8RX_sCFSDPIXtG-cR8IBsCSmMTgA8pubXyJuhIRgy2VOfSuwBGK983sX8i4uujcpsv7IUZR_b7oj9MRV9Vk.ZGQzZDY2OGQtMWQxMy00ZTEwLWIyZGUtOGVjYzUzMjU3OGE4";
    mapStyleURL = `https://maps.geo.${region}.amazonaws.com/v2/styles/${style}/descriptor?key=${apiKey}&color-scheme=Light${traffic}`;
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
          // onError={(e) => captureException(e)}
          onContextMenu={onContextMenu}
        >
          <NavigationControl showCompass={false} />
          <GeolocateControl trackUserLocation />
          <StyleSwitcherControl
            style={mapStyle}
            onChange={handleMapStyleChange}
          />
          <AttributionControl compact={false} />
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
