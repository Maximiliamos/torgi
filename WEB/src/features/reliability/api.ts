import { requestJson } from "../../lib/api";
import type { OperationsProgress, SourceHealth } from "../../lib/api";

export type ReliabilitySource = SourceHealth & {
  canonical_id: string;
  display_name_ru: string;
  aliases: string[];
  configured: boolean;
  scheduled: boolean;
  registered: boolean;
  historical_only: boolean;
  paused: boolean;
  overall_status: "healthy" | "degraded" | "failed" | "paused" | "not_checked" | string;
  current_error: string | null;
  last_historical_error: string | null;
  retry_overdue: boolean;
  pilot?: boolean;
};

export type ReliabilitySourcesResponse = {
  generated_at: string;
  configured_total: number;
  scheduled_total?: number;
  registered_total: number;
  monitored_total: number;
  paused_total: number;
  healthy: number;
  degraded: number;
  failed: number;
  not_checked: number;
  stale?: number;
  complete_fresh?: number;
  items: ReliabilitySource[];
};

export type ReliabilitySummary = {
  schema_version: number;
  generated_at: string;
  system: { status: "ok" | "degraded" | "incident" | string; reasons: string[]; generated_at?: string; deployment_sha: string; app_version?: string; schema_revision?: string };
  sources: Omit<ReliabilitySourcesResponse, "items">;
  geo: OperationsProgress["geocoding"] & { scope_description_ru?: string; population?: number; current_geocoded?: number; actionable?: number; bad_input?: number };
  map: {
    checked_at?: string | null;
    ok: boolean;
    delivery_status?: "ok" | "degraded" | "failed" | "not_checked";
    db_ok?: boolean;
    dataset_ok?: boolean;
    s3_manifest_ok?: boolean | null;
    public_ok?: boolean | null;
    mismatch_counts?: { missing: number; extra: number; tile_count: number };
    dataset_version?: string | null;
    dataset_status?: string | null;
    point_count?: number | null;
    published_at?: string | null;
    point_count_matches?: boolean | null;
    tile_count_matches?: boolean | null;
    missing_from_dataset_count?: number | null;
    extra_in_dataset_count?: number | null;
    pending_missing_count?: number | null;
    pending_extra_count?: number | null;
    public_manifest?: { checked?: boolean; ok?: boolean | null; [key: string]: unknown };
  };
  integrity: {
    checked?: boolean;
    status?: "checked" | "stale" | "not_checked" | "error";
    checked_at?: string | null;
    source_without_processed: number;
    processed_without_source: number;
    current_dataset_count: number;
    tile_count_mismatches: number;
    active_and_archived?: number;
    date_invariants?: number;
    critical_count: number;
    warning_count: number;
  };
  host: NonNullable<NonNullable<OperationsProgress["summary"]>["host"]> & {
    read_only?: boolean;
    api?: { status: string };
    database?: { status: string };
    queue?: { status: string };
    disk?: { status?: "ok" | "warning" | "critical" | "unknown"; checked_at?: string | null };
    backup?: { created_at?: string | null; size_bytes?: number | null; restore_verification?: string | null; threshold_hours?: number };
    runner_diagnostic?: { checked_at?: string | null; status?: string };
  };
  data_quality: {
    population: number;
    current_population?: number;
    missing_address: number;
    missing_cadastre: number;
    missing_price: number;
    unknown_status: number;
    missing_region: number;
    missing_url: number;
    non_positive_price: number;
    percentages: Record<string, number>;
  };
};

export const fetchReliabilitySummary = () =>
  requestJson<ReliabilitySummary>("/api/reliability/summary");

export const fetchReliabilitySources = () =>
  requestJson<ReliabilitySourcesResponse>("/api/reliability/sources");
