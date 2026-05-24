// Some clients (notably Windows PCs with stale clocks) have clocks
// that drift seconds-to-minutes from real time, which makes "X seconds ago"
// labels nonsense. Each response from our backend carries a `Date` header,
// so we use it to estimate the offset between the client clock and real time
// and apply it wherever we render relative times.

let skew = 0;

export function recordSkew(response: Response): void {
  const dateHeader = response.headers.get("Date");
  if (!dateHeader) {
    return;
  }
  const serverTime = Date.parse(dateHeader);
  if (Number.isNaN(serverTime)) {
    return;
  }
  const now = Date.now();
  if (serverTime > now) {
    skew = serverTime - now;
  } else {
    // if skew is negative, assume it's due to network latency - we're only correcting for slow clocks
    skew = 0;
  }
}

export function now(): number {
  return Date.now() + skew;
}
