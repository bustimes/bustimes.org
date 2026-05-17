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

  // Add circle layer
  map.addLayer({
    id: "vehicle-circles",
    type: "circle",
    source: "vehicles",
    paint: {
      "circle-radius": 6,
      "circle-color": "#007cbf",
      "circle-opacity": 0.8,
    },
  });

  map.on("click", "vehicle-circles", (e) => {
    map.flyTo({
      center: e.features[0].geometry.coordinates,
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

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  const items = message.items || []; // items is now [[lng, lat, id], [lng, lat, id], ...]

  // Update vehicles map with new positions
  for (const [lng, lat, id] of items) {
    vehicles.set(id, {
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: [lng, lat],
      },
      properties: { id },
    });
  }

  // Update the data source with all vehicles
  const source = map.getSource("vehicles");
  if (source && source.type === "geojson") {
    source.setData({
      type: "FeatureCollection",
      features: Array.from(vehicles.values()),
    });
  }
};
