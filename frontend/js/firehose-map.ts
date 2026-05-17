import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import maplibreWorkerUrl from "url:maplibre-gl/dist/maplibre-gl-worker.mjs";

maplibregl.setWorkerUrl(maplibreWorkerUrl);

const map = new maplibregl.Map({
  container: "hugemap",
  style: "https://tiles.bustimes.org.uk/styles/night/style.json",
  center: [-2.9, 54],
  zoom: 5,
  attributionControl: {
    compact: false,
    customAttribution: "",
  },
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

  // Render each vehicle as a rotated route-name label
  map.addLayer({
    id: "vehicle-labels",
    type: "symbol",
    source: "vehicles",
    layout: {
      "text-field": ["get", "line_name"],
      "text-rotate": ["coalesce", ["get", "heading"], 0],
      "text-rotation-alignment": "map",
      "text-size": 13,
      "text-allow-overlap": true,
      "text-ignore-placement": true,
      "text-keep-upright": false,
    },
    paint: {
      "text-color": "#fff",
      "text-halo-color": "#000",
      "text-halo-width": 1.5,
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

    const id = (feature.properties as { id: number }).id;
    openPopup = new maplibregl.Popup()
      .setLngLat(feature.geometry.coordinates as [number, number])
      .setHTML(popupHTML(feature.properties as VehicleProps))
      .addTo(map);
    openPopupId = id;
    openPopup.on("close", () => {
      openPopup = null;
      openPopupId = null;
    });
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
