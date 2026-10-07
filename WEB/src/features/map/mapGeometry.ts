/** Pure Yandex map URL, zoom and tile coordinate helpers (BAT-308). */
export function yandexMapsApiUrl(apiKey?: string) {
  const params = new URLSearchParams({ lang: "ru_RU", csp: "true" });
  const normalizedKey = apiKey?.trim();
  if (normalizedKey) params.set("apikey", normalizedKey);
  return `https://api-maps.yandex.ru/2.1.77/?${params.toString()}`;
}

export function mapLimitForZoom(zoom: number) {
  if (zoom <= 7) return 250;
  if (zoom <= 10) return 750;
  return 1500;
}

export function mapBoundsPrecision(zoom: number) {
  if (zoom <= 7) return 1;
  if (zoom < 10) return 2;
  if (zoom < 16) return 3;
  return 4;
}

export function visibleTileCoordinates(bounds: [number, number, number, number], zoom: number) {
  const z = Math.min(14, Math.max(0, Math.floor(zoom)));
  const scale = 2 ** z;
  const tileX = (lon: number) => Math.max(0, Math.min(scale - 1, Math.floor((lon + 180) / 360 * scale)));
  const tileY = (lat: number) => {
    const bounded = Math.max(-85.05112878, Math.min(85.05112878, lat));
    const rad = bounded * Math.PI / 180;
    return Math.max(0, Math.min(scale - 1, Math.floor((1 - Math.asinh(Math.tan(rad)) / Math.PI) / 2 * scale)));
  };
  const [west, south, east, north] = bounds;
  const y0 = tileY(north); const y1 = tileY(south);
  const xRanges = west <= east ? [[tileX(west), tileX(east)]] : [[tileX(west), scale - 1], [0, tileX(east)]];
  const result: Array<{ z: number; x: number; y: number; key: string }> = [];
  for (const [x0, x1] of xRanges) for (let x = x0; x <= x1; x += 1) for (let y = y0; y <= y1; y += 1) {
    result.push({ z, x, y, key: `${z}/${x}/${y}` });
  }
  return result;
}

export type TileCoordinate = { z: number; x: number; y: number };

export function mapTileCacheKey(version: string, tile: TileCoordinate) {
  return `${version}:${tile.z}:${tile.x}:${tile.y}`;
}
