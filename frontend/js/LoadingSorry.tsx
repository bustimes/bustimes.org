import { captureException } from "@sentry/browser";
import React, { type ReactElement, type ReactNode } from "react";

export default function LoadingSorry({
  text,
}: {
  text?: string | ReactElement;
}) {
  return <div className="sorry">{text || "Loading…"}</div>;
}

export function ErrorFallback(error: { error?: unknown }) {
  return (
    <LoadingSorry
      text={
        <>
          <p>I’m really sorry, something’s gone wrong</p>
          {error.error ? (
            <p>
              <code>{error.error.toString()}</code>
            </p>
          ) : null}
          <p>You could try reloading the page</p>
          <p>
            <button
              type="button"
              className="button"
              onClick={() => window.location.reload()}
            >
              ↻ Reload the page
            </button>
          </p>
        </>
      }
    />
  );
}

export class ErrorBoundary extends React.Component<
  { children: ReactNode },
  { error?: unknown }
> {
  state: { error?: unknown } = {};

  static getDerivedStateFromError(error: unknown) {
    return { error };
  }

  componentDidCatch(error: unknown) {
    captureException(error);
  }

  render() {
    if (this.state.error) {
      return <ErrorFallback error={this.state.error} />;
    }
    return this.props.children;
  }
}
