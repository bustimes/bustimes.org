import { formatMinutes, isNegligible } from "../fractionalTime";

it("formats minutes to the nearest quarter", () => {
  expect(formatMinutes(15)).toBe("¼ minute");
  expect(formatMinutes(30)).toBe("½ minute");
  expect(formatMinutes(-30)).toBe("½ minute");
  expect(formatMinutes(44)).toBe("¾ minute");
  expect(formatMinutes(60)).toBe("1 minute");
  expect(formatMinutes(75)).toBe("1 ¼ minutes");
  expect(formatMinutes(120)).toBe("2 minutes");
  expect(formatMinutes(165)).toBe("2 ¾ minutes");
  expect(formatMinutes(173)).toBe("3 minutes");
});

it("knows when a delay is negligible", () => {
  expect(isNegligible(0)).toBe(true);
  expect(isNegligible(-7)).toBe(true);
  expect(isNegligible(8)).toBe(false);
});
