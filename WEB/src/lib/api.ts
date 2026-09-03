export type SortMode = "recommended" | "price_asc" | "price_desc" | "discount" | "newest";
export type MainView = "search" | "registry" | "map" | "deal" | "reliability";
export type SearchSource = "torgi-gov" | "tbankrot" | "lot-online";

export type LotListItem = {
  id: number;
  external_id: string;
  title: string;
  description: string | null;
  category: string;
  region_slug: string | null;
  address: string | null;
  current_price: number | null;
  market_price: number | null;
  discount_percent: number | null;
  risk_score: number | null;
  rating: number | null;
  auction_status: string;
  lot_url: string | null;
  source_url?: string | null;
  source_system?: string;
  building_area: number | null;
  land_area: number | null;
  land_area_sotki: number | null;
  area: number | null;
  last_update: string | null;
  needs_human_review: boolean;
  review_status?: string | null;
  duplicate_of_id?: number | null;
};

export type LotDetail = LotListItem & {
  source: string;
  source_system: string;
  region_name: string | null;
  cadastral_number: string | null;
  cadastral_numbers: string[] | null;
  vin: string | null;
  start_price: number | null;
  market_price_min: number | null;
  market_price_max: number | null;
  ai_recommendation: string | null;
  links_to_analogs: string[];
  floors: number | null;
  year_built: number | null;
  legal_status: string | null;
  encumbrances: string | null;
  technical_condition: string | null;
  published_at: string | null;
  geo: null | {
    source: string;
    method: string;
    confidence: string;
    centroid_lat: number;
    centroid_lon: number;
    geometry_json: unknown;
    trace_reason: string | null;
    metadata_json: unknown;
    observed_at: string | null;
  };
};

export type LotsResponse = { items: LotListItem[]; total: number };
export type StatsResponse = {
  total_lots: number;
  active_lots: number;
  appraised_lots: number;
  average_discount: number | null;
  region: string;
};
export type AuthUser = { id: number; username: string; role: string };
export type ServerTime = { utc: string; moscow: string; timezone: string; source: string; synchronized: boolean; offset_seconds: number };
export type LotQuery = {
  city_slug: string;
  page: number;
  per_page: number;
  search: string;
  categories: string[];
  statuses: string[];
  min_price?: number;
  max_price?: number;
  min_discount?: number;
  max_discount?: number;
  min_risk?: number;
  max_risk?: number;
  sort: SortMode;
};

export type OnlineLot = {
  external_id: string;
  source: string;
  source_system: string;
  title: string;
  description: string;
  category: string;
  region_slug: string | null;
  region_name: string | null;
  address: string | null;
  cadastral_number: string | null;
  current_price: number | null;
  start_price: number | null;
  auction_status: string;
  lot_url: string | null;
  source_url: string | null;
  published_at: string | null;
};
export type OnlineSearchResponse = { source: SearchSource; items: OnlineLot[]; meta: Record<string, unknown> };
export type RegionOption = { code: string; name: string };

export type MapMarkerLot = {
  id: number;
  title: string;
  address: string | null;
  current_price: number | null;
  start_price: number | null;
  region_code: string | null;
  status: string;
  is_archived: boolean;
  review_status: string | null;
  lat: number;
  lon: number;
};

export type MapLot = {
  id: number;
  external_id: string;
  title: string;
  description: string;
  address: string | null;
  cadastral_number: string | null;
  category: string;
  region: string | null;
  status: string;
  is_archived: boolean;
  review_status: string | null;
  current_price: number | null;
  lat: number;
  lon: number;
  geometry: GeoJSON.GeoJsonObject | null;
  confidence: string;
  source: string;
  source_name: string;
  source_url: string | null;
  gis_torgi_url: string | null;
  etp_url: string | null;
  torgi_russia_url: string | null;
  image_url: string | null;
  image_urls: string[];
  procedure_number: string | null;
  application_deadline: string | null;
  auction_at: string | null;
  sources: Array<{
    processed_lot_id: number;
    source_system: string;
    external_id: string;
    title: string;
    price: number | null;
    url: string | null;
    is_primary: boolean;
  }>;
};

export type Procedure = Record<string, string | number | boolean | null | unknown[]>;
export type MaxBidScenario = {
  id: number;
  name: string;
  inputs: Record<string, unknown>;
  results: Record<string, Record<string, number>>;
  created_at: string;
};
export type DocumentVersion = { id: number; sha256: string; mime_type: string | null; size_bytes: number | null; fetched_at: string };
export type LotDocument = { id: number; filename: string; source_url: string | null; document_kind: string | null; versions: DocumentVersion[] };

export type Participation = {
  lot_id: number;
  source_lot_id: number;
  etp_accredited: boolean;
  signature_valid: boolean;
  application_completed: boolean;
  deposit_sent: boolean;
  payment_purpose_verified: boolean;
  deposit_received: boolean;
  documents_signed: boolean;
  application_accepted: boolean;
  notes: string | null;
};

export type QualitySnapshot = Record<string, number>;
export type SourceHealth = {
  source_system: string;
  status: string;
  items_seen: number;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_error: string | null;
};

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const RETRYABLE_READ_STATUSES = new Set([502, 503, 504]);
const READ_RETRY_DELAY_MS = 250;
const TEMPORARY_UNAVAILABLE_MESSAGE =
  "Сервис временно недоступен. Повторите попытку через несколько секунд.";

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

async function waitForReadRetry(signal?: AbortSignal) {
  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, READ_RETRY_DELAY_MS);
    signal?.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(signal.reason ?? new DOMException("The operation was aborted", "AbortError"));
    }, { once: true });
  });
}

async function fetchWithReadRetry(input: RequestInfo | URL, init: RequestInit = {}, maxAttempts?: number) {
  const method = (init.method || "GET").toUpperCase();
  const attempts = maxAttempts ?? (method === "GET" || method === "HEAD" ? 2 : 1);
  let lastError: unknown;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetch(input, init);
      if (!RETRYABLE_READ_STATUSES.has(response.status) || attempt === attempts - 1) {
        return response;
      }
      await response.body?.cancel();
    } catch (error) {
      if (isAbortError(error)) throw error;
      lastError = error;
      if (attempt === attempts - 1) {
        throw new ApiError(TEMPORARY_UNAVAILABLE_MESSAGE);
      }
    }
    await waitForReadRetry(init.signal ?? undefined);
  }

  throw lastError instanceof Error
    ? lastError
    : new ApiError(TEMPORARY_UNAVAILABLE_MESSAGE);
}

export function makeUrl(path: string, params?: Record<string, string | number | boolean | undefined>) {
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
  });
  return url.toString();
}

export async function requestJson<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
  init?: RequestInit
): Promise<T> {
  const response = await fetchWithReadRetry(makeUrl(path, params), {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
    credentials: "same-origin"
  });
  if (!response.ok) {
    const text = await response.text();
    let message = text || `HTTP ${response.status}`;
    try { message = JSON.parse(text).detail || message; } catch { /* plain text */ }
    if (RETRYABLE_READ_STATUSES.has(response.status)) {
      message = TEMPORARY_UNAVAILABLE_MESSAGE;
    }
    throw new ApiError(message, response.status);
  }
  return response.json() as Promise<T>;
}

export async function login(username: string, password: string) {
  return requestJson<AuthUser>("/api/auth/login", undefined, {
    method: "POST",
    body: JSON.stringify({ username, password })
  });
}
export const fetchCurrentUser = () => requestJson<AuthUser>("/api/auth/me");
export async function fetchServerTime(): Promise<ServerTime> {
  const response = await fetchWithReadRetry(makeUrl("/api/auth/me"), {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) throw new ApiError(`HTTP ${response.status}`, response.status);
  const date = response.headers.get("date");
  await response.body?.cancel();
  if (!date) throw new ApiError("Online time header is unavailable");
  const utc = new Date(date).toISOString();
  return { utc, moscow: utc, timezone: "Europe/Moscow", source: "http_date", synchronized: true, offset_seconds: 0 };
}
export const logout = () => requestJson<{ status: string }>("/api/auth/logout", undefined, { method: "POST" });

export function fetchLots(query: LotQuery) {
  return requestJson<LotsResponse>("/api/lots", {
    city_slug: query.city_slug, page: query.page, per_page: query.per_page, search: query.search,
    categories: query.categories.join(","), statuses: query.statuses.join(","), min_price: query.min_price,
    max_price: query.max_price, min_discount: query.min_discount, max_discount: query.max_discount,
    min_risk: query.min_risk, max_risk: query.max_risk, sort: query.sort
  });
}
export const fetchStats = (citySlug: string) => requestJson<StatsResponse>("/api/stats", { city_slug: citySlug });
export const fetchLotDetail = (id: number, citySlug: string) => requestJson<LotDetail>(`/api/lots/${id}`, { city_slug: citySlug });
export const fetchProcedure = (id: number) => requestJson<Procedure>(`/api/lots/${id}/procedure`);

export const searchOnline = (source: SearchSource, params: Record<string, string | number | boolean | undefined>) =>
  requestJson<OnlineSearchResponse>(`/api/search/${source}`, params);
export const importOnlineLot = (lot: OnlineLot) => requestJson<{ id: number }>("/api/search/import", undefined, {
  method: "POST",
  body: JSON.stringify({
    external_id: lot.external_id,
    source: lot.source,
    source_system: lot.source_system,
    title: lot.title,
    description: lot.description || "",
    category: lot.category || "other",
    region_slug: lot.region_slug,
    region_name: lot.region_name,
    address: lot.address,
    cadastral_number: lot.cadastral_number,
    current_price: lot.current_price,
    start_price: lot.start_price,
    auction_status: lot.auction_status || "unknown",
    lot_url: lot.lot_url,
    source_url: lot.source_url,
    published_at: lot.published_at
  })
});

export type MapLotsResponse = {
  items: MapMarkerLot[];
  returned: number;
  limit: number;
  truncated: boolean;
  total: number;
  mapped_total: number;
  without_coordinates: number;
  updated_at: string | null;
  statistics_exact?: boolean;
  timings: { server_ms: number };
};
export type MapViewportQuery = {
  city_slug?: string;
  region_code?: string;
  min_start_price?: number;
  max_start_price?: number;
  include_archived?: boolean;
  west?: number;
  south?: number;
  east?: number;
  north?: number;
  review_status?: "approved" | "maybe" | "rejected";
  limit?: number;
};
export type LotSyncStatus = {
  task_id: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  result?: Record<string, unknown> | null;
  sources?: Array<{ source_system: string; status: string; items_seen: number }>;
};
export const startNationwideLotSync = () =>
  requestJson<{ task_id: string; status: string }>("/api/sync/lots", undefined, { method: "POST" });
export const fetchNationwideLotSync = (taskId: string) =>
  requestJson<LotSyncStatus>(`/api/sync/lots/${encodeURIComponent(taskId)}`);
export const fetchMapLots = (query: MapViewportQuery = {}) =>
  requestJson<MapLotsResponse>("/api/map/lots", query);
export const fetchMapLotDetail = (lotId: number) =>
  requestJson<MapLot>(`/api/map/lots/${lotId}`);

const MAP_CACHE_NAME = "bankrotai-map-v3";
const MAP_CACHE_MAX_ENTRIES = 50;
const MAP_CACHE_TTL_MS = 10 * 60 * 1000;
const MAP_CACHE_TIMESTAMP_HEADER = "X-BankrotAI-Cached-At";

async function pruneMapCache(cache: Cache) {
  const requests = await cache.keys();
  const entries = await Promise.all(requests.map(async (request) => {
    const response = await cache.match(request);
    return {
      request,
      cachedAt: Number(response?.headers.get(MAP_CACHE_TIMESTAMP_HEADER) || 0),
    };
  }));
  entries.sort((left, right) => right.cachedAt - left.cachedAt);
  await Promise.all(entries.slice(MAP_CACHE_MAX_ENTRIES).map(({ request }) => cache.delete(request)));
}

export async function clearMapCache() {
  if ("caches" in window) await window.caches.delete(MAP_CACHE_NAME);
}

export async function fetchMapLotsSWR(
  query: MapViewportQuery,
  onCached?: (value: MapLotsResponse) => void,
  signal?: AbortSignal,
  networkAttempts = 2,
) {
  const url = makeUrl("/api/map/lots", query);
  const request = new Request(url, { credentials: "same-origin" });
  const cache = "caches" in window ? await window.caches.open(MAP_CACHE_NAME) : null;
  let cachedResponse = await cache?.match(request);
  let cachedValue: MapLotsResponse | null = null;
  if (cachedResponse) {
    const cachedAt = Number(cachedResponse.headers.get(MAP_CACHE_TIMESTAMP_HEADER) || 0);
    if (!cachedAt || Date.now() - cachedAt > MAP_CACHE_TTL_MS) {
      await cache?.delete(request);
      cachedResponse = undefined;
    }
  }
  if (cachedResponse) {
    try {
      cachedValue = await cachedResponse.clone().json() as MapLotsResponse;
      onCached?.(cachedValue);
    } catch {
      await cache?.delete(request);
    }
  }

  const started = performance.now();
  try {
    const response = await fetchWithReadRetry(url, {
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...(cachedResponse?.headers.get("ETag")
          ? { "If-None-Match": cachedResponse.headers.get("ETag") as string }
          : {}),
      },
      cache: "no-cache",
      signal,
    }, networkAttempts);
    if (response.status === 304 && cachedValue) {
      return { data: cachedValue, networkMs: performance.now() - started, fromCache: true };
    }
    if (!response.ok) {
      const text = await response.text();
      throw new ApiError(
        RETRYABLE_READ_STATUSES.has(response.status)
          ? TEMPORARY_UNAVAILABLE_MESSAGE
          : text || `HTTP ${response.status}`,
        response.status,
      );
    }
    const cacheCopy = response.clone();
    const data = await response.json() as MapLotsResponse;
    if (cache) {
      const headers = new Headers(cacheCopy.headers);
      headers.set(MAP_CACHE_TIMESTAMP_HEADER, String(Date.now()));
      await cache.put(request, new Response(await cacheCopy.blob(), {
        status: cacheCopy.status,
        statusText: cacheCopy.statusText,
        headers,
      }));
      await pruneMapCache(cache);
    }
    return { data, networkMs: performance.now() - started, fromCache: false };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (cachedValue) {
      return { data: cachedValue, networkMs: performance.now() - started, fromCache: true };
    }
    throw error;
  }
}
export const searchCadastre = (query: string) => requestJson<Record<string, unknown>>("/api/cadastre/search", { query });
export const setReviewStatus = (lotId: number, status: string | null) =>
  requestJson(`/api/lots/${lotId}/review-status`, undefined, { method: "PUT", body: JSON.stringify({ status }) });

export const toggleWatchlist = (lotId: number) => requestJson<{ watchlisted: boolean }>(`/api/lots/${lotId}/watchlist`, undefined, { method: "POST" });
export const fetchWatchlist = () => requestJson<LotsResponse>("/api/watchlist");
export const fetchNotes = (lotId: number) => requestJson<Array<{ id: number; content: string; created_at: string }>>(`/api/lots/${lotId}/notes`);
export const addNote = (lotId: number, content: string) => requestJson(`/api/lots/${lotId}/notes`, undefined, { method: "POST", body: JSON.stringify({ content }) });

export const fetchParticipation = (lotId: number) => requestJson<Participation>(`/api/lots/${lotId}/participation`);
export const saveParticipation = (lotId: number, value: Omit<Participation, "lot_id" | "source_lot_id">) =>
  requestJson<Participation>(`/api/lots/${lotId}/participation`, undefined, { method: "PUT", body: JSON.stringify(value) });

export const calculateMaxBid = (lotId: number, value: Record<string, string | number | null>) =>
  requestJson<{ scenarios: Record<string, Record<string, number>>; warning: string }>(`/api/lots/${lotId}/max-bid`, undefined, { method: "POST", body: JSON.stringify(value) });
export const fetchMaxBidScenarios = (lotId: number) => requestJson<MaxBidScenario[]>(`/api/lots/${lotId}/max-bid-scenarios`);

export const fetchDocuments = (lotId: number) => requestJson<LotDocument[]>(`/api/lots/${lotId}/documents`);
export const compareDocuments = (lotId: number, fromId: number, toId: number) =>
  requestJson(`/api/lots/${lotId}/documents-compare`, undefined, { method: "POST", body: JSON.stringify({ from_version_id: fromId, to_version_id: toId }) });

export const fetchQuality = () => requestJson<QualitySnapshot>("/api/quality");
export const fetchSources = () => requestJson<SourceHealth[]>("/api/sources");
export const fetchDiagnostics = () => requestJson<Record<string, unknown>>("/api/diagnostics");
export const fetchCapabilities = () => requestJson<{ curated_mode: boolean; region_sync: boolean; bulk_torgi_sync: boolean; background_jobs: boolean }>("/api/capabilities");
export const fetchRegions = () => requestJson<RegionOption[]>("/api/regions");
export const fetchSavedSearches = () => requestJson<Array<{ id: number; name: string; query: LotQuery }>>("/api/saved-searches");
export const saveSearch = (name: string, query: LotQuery) => requestJson("/api/saved-searches", undefined, { method: "POST", body: JSON.stringify({ name, query }) });
export const mergeLots = (primaryId: number, secondaryId: number, reason = "") => requestJson(`/api/lots/${primaryId}/merge`, undefined, { method: "POST", body: JSON.stringify({ secondary_lot_id: secondaryId, reason }) });
export const splitLot = (lotId: number, reason = "") => requestJson(`/api/lots/${lotId}/split`, undefined, { method: "POST", body: JSON.stringify({ reason }) });
export const syncRegion = (citySlug: string, force = false) => requestJson<{ status: string; dispatchMode: string }>(`/api/regions/${encodeURIComponent(citySlug)}/sync`, { force }, { method: "POST" });
