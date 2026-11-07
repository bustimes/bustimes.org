// Format durations to the nearest quarter of a minute, using vulgar
// fraction characters – e.g. "½ minute", "1 minute", "2 ¾ minutes"

const FRACTIONS = ["", "¼", "½", "¾"];

export function formatMinutes(seconds: number): string {
  const quarters = Math.round(Math.abs(seconds) / 15);
  const whole = Math.floor(quarters / 4);
  const fraction = FRACTIONS[quarters % 4];

  if (!whole) {
    return `${fraction} minute`; // "¼ minute", "½ minute", "¾ minute"
  }
  if (!fraction) {
    return whole === 1 ? "1 minute" : `${whole} minutes`;
  }
  return `${whole} ${fraction} minutes`;
}

// would this round to zero (i.e. less than ⅛ of a minute)?
export function isNegligible(seconds: number): boolean {
  return Math.round(Math.abs(seconds) / 15) === 0;
}
