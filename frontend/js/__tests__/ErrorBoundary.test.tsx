/**
 * @jest-environment jsdom
 */

import { captureException } from "@sentry/browser";
import { render, screen } from "@testing-library/preact";
import { ErrorBoundary } from "../LoadingSorry";

jest.mock("@sentry/browser", () => ({ captureException: jest.fn() }));

function Broken(): never {
  throw new Error("oh no");
}

it("catches errors", () => {
  render(
    <ErrorBoundary>
      <Broken />
    </ErrorBoundary>,
  );
  expect(screen.getByText("Error: oh no")).toBeTruthy();
  expect(captureException).toHaveBeenCalledWith(new Error("oh no"));
});
