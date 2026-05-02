import { decodeTimeAwarePolyline } from "../time-aware-polyline";

// Regression test: the first timestamp in a real polyline is a unix timestamp
// in seconds (~1.7e9), and the encoder zigzags it to ~3.5e9 before chunking.
// During decoding, accumulating chunks via 32-bit `<<` overflows: `3 << 30`
// becomes negative in JS. Using arithmetic (`* 2 ** shift`) keeps full precision.
it("decodes large timestamps without 32-bit overflow", () => {
  const polyline = "whgDgqnaIcefv}hBbUpIq@hBx@G|VbNcAjBx@ItPxE{@Kj@a@qHW]yAAg@";
  const decoded = decodeTimeAwarePolyline(polyline);

  expect(decoded.length).toBe(9);

  // First point: lat ~52.8 (UK), lng ~0.86, time ~now
  const [lat, lng, ts] = decoded[0];
  expect(lat).toBeCloseTo(52.8362, 5);
  expect(lng).toBeCloseTo(0.86172, 5);

  // The 32-bit-overflow bug produced a negative timestamp around
  // -369766814000 ms (1958). The fix gives a real-world ms timestamp.
  expect(ts).toBeGreaterThan(new Date("2024-01-01").getTime());
  expect(ts).toBeLessThan(new Date("2030-01-01").getTime());
});
