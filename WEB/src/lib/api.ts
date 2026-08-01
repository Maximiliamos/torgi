export type SortMode = "recommended" | "price_asc" | "price_desc" | "discount" | "newest";

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
  building_area: number | null;
  land_area: number | null;
  land_area_sotki: number | null;
  area: number | null;
  last_update: string | null;
  needs_human_review: boolean;
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
  source_url: string | null;
  floors: number | null;
  year_built: number | null;
  legal_status: string | null;
  encumbrances: string | null;
  technical_condition: string | null;
  review_status: string | null;
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

export type LotsResponse = {
  items: LotListItem[];
  total: number;
};

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

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");

export function makeUrl(path: string, params?: Record<string, string | number | undefined>) {
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== "") {
      url.searchParams.set(key, String(value));
    }
  });
  return url.toString();
}

export async function requestJson<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const response = await fetch(makeUrl(path, params), {
    headers: { Accept: "application/json" },
    credentials: "same-origin"
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function login(username: string, password: string) {
  const response = await fetch(makeUrl("/api/auth/login"), {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ username, password })
  });
  if (!response.ok) throw new Error(response.status === 401 ? "Неверный логин или пароль" : await response.text());
  return response.json() as Promise<AuthUser>;
}

export function fetchCurrentUser() {
  return requestJson<AuthUser>("/api/auth/me");
}

export async function logout() {
  await fetch(makeUrl("/api/auth/logout"), { method: "POST", credentials: "same-origin" });
}

export function fetchLots(query: LotQuery) {
  return requestJson<LotsResponse>("/api/lots", {
    city_slug: query.city_slug,
    page: query.page,
    per_page: query.per_page,
    search: query.search,
    categories: query.categories.join(","),
    statuses: query.statuses.join(","),
    min_price: query.min_price,
    max_price: query.max_price,
    min_discount: query.min_discount,
    max_discount: query.max_discount,
    min_risk: query.min_risk,
    max_risk: query.max_risk,
    sort: query.sort
  });
}

export function fetchStats(citySlug: string) {
  return requestJson<StatsResponse>("/api/stats", { city_slug: citySlug });
}

export function fetchLotDetail(id: number, citySlug: string) {
  return requestJson<LotDetail>(`/api/lots/${id}`, { city_slug: citySlug });
}
