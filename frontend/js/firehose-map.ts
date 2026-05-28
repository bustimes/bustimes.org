import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import busIconUrl from "data-url:../bus-droplet.png";
// import maplibreWorkerUrl from "url:maplibre-gl/dist/maplibre-gl-worker.mjs";

// maplibregl.setWorkerUrl(maplibreWorkerUrl);

type View = { zoom: number; center: [number, number] };

const parseVehicleMap = (raw: string | null): View | null => {
  if (!raw) return null;
  const [zoom, lat, lng] = raw.split("/").map(Number);
  if (!Number.isFinite(zoom) || !Number.isFinite(lat) || !Number.isFinite(lng))
    return null;
  return { zoom, center: [lng, lat] };
};

const initialView = parseVehicleMap(localStorage.getItem("vehicleMap"));

const map = new maplibregl.Map({
  container: "hugemap",
  style: "https://tiles.openfreemap.org/styles/positron",
  center: initialView?.center ?? [-2.9, 54],
  zoom: initialView?.zoom ?? 5,
  attributionControl: {
    compact: false,
    customAttribution: "",
  },
});

window.addEventListener("storage", (e) => {
  if (e.key !== "vehicleMap") return;
  const view = parseVehicleMap(e.newValue);
  if (view) map.easeTo(view);
});

map.on("load", () => {
  // Add GeoJSON source for vehicle locations
  map.addSource("vehicles", {
    type: "geojson",
    data: {
      type: "FeatureCollection",
      features: [],
    },
  });

  const busImage = new Image();
  busImage.src = busIconUrl;
  busImage.onload = () => {
    if (!map.hasImage("vehicle-marker")) {
      map.addImage("vehicle-marker", busImage, { pixelRatio: 2, sdf: true });
    }
  };

  map.addLayer({
    id: "vehicle-labels",
    type: "symbol",
    source: "vehicles",
    layout: {
      "icon-image": "vehicle-marker",
      // arrow points to the bottom-left of the icon (compass 225° when
      // un-rotated), so subtract 225 to align with heading
      "icon-rotate": ["-", ["coalesce", ["get", "heading"], 0], 225],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "text-field": ["get", "line_name"],
      "text-font": ["Noto Sans Regular"],
      "text-size": 12,
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: {
      "text-color": "#fff",
      "icon-color": ["coalesce", ["get", "colour"], "#000"],
    },
  });

  map.on("mouseenter", "vehicle-labels", () => {
    map.getCanvas().style.cursor = "pointer";
  });

  map.on("mouseleave", "vehicle-labels", () => {
    map.getCanvas().style.cursor = "";
  });

  map.on("click", "vehicle-labels", (e) => {
    const feature = e.features?.[0];
    if (!feature || feature.geometry.type !== "Point") return;

    const id = Number((feature.properties as { id: number | string }).id);
    const coords = feature.geometry.coordinates as [number, number];
    openPopup = new maplibregl.Popup({ offset: [0, -6] })
      .setLngLat(coords)
      .setHTML(popupHTML(feature.properties as VehicleProps))
      .addTo(map);
    openPopupId = id;
    openPopup.on("close", () => {
      openPopup = null;
      openPopupId = null;
    });
    map.panTo(coords);
  });
});

const wsProtocol = window.location.protocol === "http:" ? "ws" : "wss";
const ws = new WebSocket(`${wsProtocol}://${window.location.host}/firehose`);

const statusBar = document.getElementById("skew");

ws.onopen = (event) => {
  if (statusBar) {
    statusBar.innerText = "connected";
  }
};

ws.onclose = (event) => {
  if (statusBar) {
    statusBar.innerText = "disconnected";
  }
};

const vehicles = new Map(); // Track all vehicles by id

let openPopup: maplibregl.Popup | null = null;
let openPopupId: number | null = null;

const randomColour = () =>
  `#${Math.floor(Math.random() * 0xffffff)
    .toString(16)
    .padStart(6, "0")}`;

type VehicleItem = {
  id: number;
  coordinates: [number, number];
  heading?: number;
  datetime?: string;
  destination?: string;
  service?: { line_name?: string };
};

type VehicleProps = {
  id: number;
  heading: number;
  datetime?: string;
  destination?: string;
  line_name?: string;
};

const popupHTML = (props: VehicleProps) => {
  const when = props.datetime
    ? new Date(props.datetime).toLocaleTimeString()
    : "";
  return [
    props.line_name &&
      `<strong>${props.line_name}</strong>${props.destination ? ` to ${props.destination}` : ""}`,
    when,
    `<a href="/vehicles/${props.id}">vehicle ${props.id}</a>`,
  ]
    .filter(Boolean)
    .join("<br>");
};

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  const items: VehicleItem[] = message.items || [];

  for (const item of items) {
    vehicles.set(item.id, {
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: item.coordinates,
      },
      properties: {
        id: item.id,
        heading: item.heading ?? 0,
        datetime: item.datetime,
        destination: item.destination,
        line_name: item.service?.line_name,
        colour: randomColour(),
      },
    });

    if (openPopup && openPopupId === item.id) {
      openPopup.setLngLat(item.coordinates).setHTML(
        popupHTML({
          id: item.id,
          heading: item.heading ?? 0,
          datetime: item.datetime,
          destination: item.destination,
          line_name: item.service?.line_name,
        }),
      );
      map.panTo(item.coordinates);
    }
  }

  const source = map.getSource("vehicles");
  if (source && source.type === "geojson") {
    source.setData({
      type: "FeatureCollection",
      features: Array.from(vehicles.values()),
    });
  }
};
