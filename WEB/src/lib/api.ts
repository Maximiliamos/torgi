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

export type MapLot = {
  id: number;
  external_id: string;
  title: string;
  address: string | null;
  category: string;
  status: string;
  review_status: string | null;
  current_price: number | null;
  lat: number;
  lon: number;
  geometry: GeoJSON.GeoJsonObject | null;
  confidence: string;
  source: string;
  lot_url: string | null;
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
  const response = await fetch(makeUrl(path, params), {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
    credentials: "same-origin"
  });
  if (!response.ok) {
    const text = await response.text();
    let message = text || `HTTP ${response.status}`;
    try { message = JSON.parse(text).detail || message; } catch { /* plain text */ }
    throw new Error(message);
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

export const fetchMapLots = (citySlug?: string, includeArchived = false) => requestJson<{ items: MapLot[]; total: number }>("/api/map/lots", { city_slug: citySlug, include_archived: includeArchived });
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
