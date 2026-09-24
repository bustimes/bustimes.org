import * as Sentry from "@sentry/react";
import React from "react";
import { createRoot } from "react-dom/client";

import { ErrorFallback } from "./LoadingSorry";

if (process.env.NODE_ENV === "production") {
  Sentry.init({
    dsn: "https://0d628b6fff45463bb803d045b99aa542@o55224.ingest.sentry.io/1379883",
    allowUrls: [/https:\/\/bustimes\.org\/static\//],
    ignoreErrors: [
      "AbortError: The user aborted a request.",
      "ownerDocument",
      // ads
      "Can't access contentWindow of an iframe",
      "Invalid state transition request!",
      "this._resizeCallback is not a function",
      "Analytics would be confused",
      // GPU/browser
      "WebGL2 is required to display this map",
      "Could not compile fragment shader",
      "Program failed to link",
      "reading the Blob argument to createImageBitmap",
      "AJAXError: Failed to fetch",
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

export default function renderRoot(
  element: HTMLElement,
  children: React.ReactNode,
) {
  const root = createRoot(element, {
    // Callback called when an error is thrown and not caught by an ErrorBoundary.
    onUncaughtError: Sentry.reactErrorHandler((error, errorInfo) => {
      console.warn("Uncaught error", error, errorInfo.componentStack);
    }),
    // Callback called when React catches an error in an ErrorBoundary.
    onCaughtError: Sentry.reactErrorHandler(),
    // Callback called when React automatically recovers from errors.
    onRecoverableError: Sentry.reactErrorHandler(),
  });

  root.render(
    <React.StrictMode>
      <Sentry.ErrorBoundary fallback={ErrorFallback}>
        {children}
      </Sentry.ErrorBoundary>
    </React.StrictMode>,
  );
}
