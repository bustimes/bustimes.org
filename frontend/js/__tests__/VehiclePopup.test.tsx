/**
 * @jest-environment jsdom
 */

import { render, screen } from "@testing-library/react";
import { Delay } from "../VehiclePopup";

it("shows delay", async () => {
  const { container, rerender } = render(<Delay item={{ delay: 212 }} />);
  expect(container).toMatchSnapshot();

  rerender(<Delay item={{ delay: -230 }} />);
  expect(container).toMatchSnapshot();

  rerender(<Delay item={{ delay: 20 }} />);
  expect(container).toMatchSnapshot();

  rerender(<Delay item={{ delay: -20 }} />);
  expect(container).toMatchSnapshot();

  rerender(<Delay item={{ delay: 5 }} />);
  expect(container).toMatchSnapshot();

  rerender(<Delay item={{}} />);
  expect(container).toMatchSnapshot();
});
