import { requestJson } from "./api";

export type EtpQaItem = {
  external_id: string;
  title: string;
  url: string;
  cadastral_number: string | null;
  start_price: number | null;
  current_price: number | null;
  status: string;
  detail_level: string;
  geo_status: string;
  lat: number | null;
  lon: number | null;
  unverified: true;
};

export type EtpQaPreview = {
  source: string;
  qa_only: true;
  verified_for_production: false;
  complete_source_inventory: false;
  database_writes: 0;
  archival_enabled: false;
  status: "blocked" | "preview" | "unavailable";
  blocked_reason: string | null;
  pages_fetched: number;
  candidate_count: number;
  mapped_count: number;
  errors: string[];
  items: EtpQaItem[];
};

export const fetchEtpQaSources = () =>
  requestJson<{ items: Array<{ id: string; label: string; blocked: boolean }>; qa_only: true }>(
    "/api/etp/qa-map/sources"
  );

export const fetchEtpQaPreview = (source: string) =>
  requestJson<EtpQaPreview>(`/api/etp/qa-map/${encodeURIComponent(source)}`, {
    pages: 2, per_page: 3, geo_limit: 2,
  });

