import "./polyfills";

import { recordSkew } from "./clockSkew";

declare global {
  interface Window {
    SERVICE_ID?: number;
    OPERATOR_ID?: string;
    globalThis: Window;
  }
}

if (typeof window.globalThis === "undefined") {
  window.globalThis = window;
}

const mapLink = document.getElementById("map-link");
const hugeMap = document.getElementById("hugemap");

if (window.SERVICE_ID && mapLink) {
  const serviceId = window.SERVICE_ID;

  let opened = false;

  const openMap = () => {
    if (!opened && window.location.hash === "#map") {
      opened = true;
      import("./ServiceMapMap").catch(() => {
        // never mind, ServiceMap will ask for it again
      });
      import("./mountServiceMap").then(({ default: mount }) => {
        mount(mapLink, serviceId);
      });
    }
  };

  fetch(`/vehicles.json?service=${serviceId}`).then(
    (response) => {
      recordSkew(response);
      response.json().then((vehicles: unknown[]) => {
        const link = mapLink.querySelector("a");
        if (opened || !link) {
          return;
        }
        const count = vehicles.length;
        if (count === 1) {
          link.textContent = `Map (tracking ${count} bus)`;
        } else if (count) {
          link.textContent = `Map (tracking ${count} buses)`;
        } else {
          link.textContent = "Map";
        }
      });
    },
    () => {
      // never mind
    },
  );

  window.addEventListener("hashchange", openMap);
  openMap();
} else if (hugeMap) {
  import("./mountBigMap").then(({ default: mount }) => {
    mount(hugeMap);
  });
}
