import * as Sentry from "@sentry/react";
import React, { lazy } from "react";
import { createRoot } from "react-dom/client";

import "./maps.css";
import "maplibre-gl/dist/maplibre-gl.css";

import { ErrorFallback } from "./LoadingSorry";
import ServiceMap from "./ServiceMap";
const MapRouter = lazy(() => import("./MapRouter"));

if (process.env.NODE_ENV === "production") {
  Sentry.init({
    dsn: "https://0d628b6fff45463bb803d045b99aa542@o55224.ingest.sentry.io/1379883",
    allowUrls: [/https:\/\/bustimes\.org\/static\//],
    // belt-and-braces alongside allowUrls: catches errors whose culprit frame
    // gets misattributed to our own URL (eg thrown from inside a same-origin
    // iframe created by the ad script)
    denyUrls: [
      /cdn\.adfirst\.media/,
      /googletagmanager\.com/,
      /googlesyndication\.com/,
      /doubleclick\.net/,
    ],
    ignoreErrors: [
      // third-party ad tags (header bidding via adfirst.media, Google
      // Publisher Tag) and the cross-origin iframes they create - opaque
      // "Script error." is what a script loaded without CORS produces, and
      // is the commonest shape this noise takes
      "Script error.",
      "Script error",
      /googletag/i,
      /Blocked a frame with origin/,
      /Permission denied to access property .* on cross-origin object/,
      "ResizeObserver loop limit exceeded",
      "ResizeObserver loop completed with undelivered notifications.",

      // lazy-loaded chunk fetch failing, usually a flaky mobile connection
      // or a stale page still referencing assets from a previous deploy
      /Failed to fetch dynamically imported module/,
      /error loading dynamically imported module/,
      /Importing a module script failed/,

      // other unactionable browser/network noise
      "'_loaded'",
      "Load failed",
      "AbortError: The user aborted a request.",
      "'this.getContainer().ownerDocument'",
    ],
    integrations: [
      Sentry.globalHandlersIntegration({
        onerror: false,
        onunhandledrejection: false,
      }),
    ],
    release: process.env.KAMAL_CONTAINER_NAME,
  });
}

declare global {
  interface Window {
    SERVICE_ID?: number;
    OPERATOR_ID?: string;
    VEHICLE_ID: number;
    globalThis: Window;
  }
}

if (typeof window.globalThis === "undefined") {
  window.globalThis = window;
}

const createRootOptions = {
  // Callback called when an error is thrown and not caught by an ErrorBoundary.
  onUncaughtError: Sentry.reactErrorHandler((error, errorInfo) => {
    console.warn("Uncaught error", error, errorInfo.componentStack);
  }),
  // Callback called when React catches an error in an ErrorBoundary.
  onCaughtError: Sentry.reactErrorHandler(),
  // Callback called when React automatically recovers from errors.
  onRecoverableError: Sentry.reactErrorHandler(),
};

let rootElement: HTMLElement | null;
if (window.SERVICE_ID && (rootElement = document.getElementById("map-link"))) {
  const root = createRoot(rootElement, createRootOptions);
  root.render(
    <React.StrictMode>
      <Sentry.ErrorBoundary fallback={ErrorFallback}>
        <ServiceMap
          serviceId={window.SERVICE_ID}
          buttonText={rootElement.innerText}
        />
      </Sentry.ErrorBoundary>
    </React.StrictMode>,
  );
} else if ((rootElement = document.getElementById("hugemap"))) {
  const root = createRoot(rootElement, createRootOptions);
  root.render(
    <React.StrictMode>
      <Sentry.ErrorBoundary fallback={ErrorFallback}>
        <MapRouter />
      </Sentry.ErrorBoundary>
    </React.StrictMode>,
  );
}
