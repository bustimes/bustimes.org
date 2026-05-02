function getDecodedDimensionFromPolyline(polyline: string, index: number) {
  let result = 1;
  let shift = 0;
  let b = 0x1f;

  while (b >= 0x1f) {
    b = polyline.charCodeAt(index) - 64;
    index += 1;
    result += b * 2 ** shift;
    shift += 5;
  }
  // Bitwise ops would truncate to 32 bits, which overflows for unix timestamps.
  return [index, result % 2 !== 0 ? -(result + 1) / 2 : result / 2];
}

export function decodeTimeAwarePolyline(
  polyline: string,
): [number, number, number][] {
  let index = 0;
  let lat = 0;
  let lng = 0;
  let timestamp = 0;
  const response: [number, number, number][] = [];

  const length = polyline.length;

  while (index < length) {
    let lng_diff: number;
    let lat_diff: number;
    let timestamp_diff: number;

    [index, lng_diff] = getDecodedDimensionFromPolyline(polyline, index);
    [index, lat_diff] = getDecodedDimensionFromPolyline(polyline, index);
    [index, timestamp_diff] = getDecodedDimensionFromPolyline(polyline, index);

    lng += lng_diff;
    lat += lat_diff;
    timestamp += timestamp_diff;

    response.push([lat * 1e-5, lng * 1e-5, timestamp * 1000]);
  }
  return response;
}
