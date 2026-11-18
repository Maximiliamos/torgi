export type ReliabilityReason = {
  severity: "critical" | "warning" | "info" | string;
  code: string;
  title: string;
  detail: string;
};

export type ReliabilityMetric = {
  count: number;
  percent: number;
};

export type ReliabilitySummary = {
  schema_version: number;
  generated_at: string;
  system: {
    status: "ok" | "degraded" | "incident";
    title_ru: string;
    reasons: ReliabilityReason[];
    deployment_sha: string;
    app_version: string;
    schema_revision: string;
  };
  sources: {
    configured_total: number;
    scheduled_total: number;
    connected_total: number;
    pilot_total: number;
    historical_total: number;
    paused_total: number;
    healthy: number;
    degraded: number;
    failed: number;
    not_checked: number;
    complete_fresh: number;
    stale: number;
    oldest_complete_snapshot_age_seconds: number | null;
    network: {
      circuit_open: boolean;
      circuit_open_until?: string | null;
      degraded_sources?: string[];
      degraded_source_count?: number;
    };
  };
  geo: {
    population: number;
    current_geocoded: number;
    percent: number;
    actionable_remaining: number;
    eligible_now: number;
    waiting_for_retry: number;
    deferred_no_match: number;
    deferred_validation: number;
    deferred_total: number;
    network_wait: number;
    p7_held: number;
    deferred_bad_input: number;
    terminal_failures: number;
    history_lots: number;
    paused: boolean;
    scope_ru: string;
    next_retry_at?: string | null;
  };
  map: {
    status: "ok" | "degraded" | "incident";
    dataset_version: string | null;
    point_count: number | null;
    tile_count: number | null;
    published_at: string | null;
    db_ok: boolean;
    dataset_ok: boolean;
    s3_manifest_checked: boolean;
    s3_manifest_ok: boolean | null;
    public_ok: boolean | null;
    db_eligible_count: number;
    dataset_unique_lot_count: number;
    missing_from_dataset_count: number;
    extra_in_dataset_count: number;
    pending_post_dataset_missing_count: number;
    pending_post_dataset_extra_count: number;
    tile_count_matches: boolean | null;
    point_count_matches: boolean | null;
    manifest_error?: string | null;
  };
  integrity: {
    critical_count: number;
    warning_count: number;
    source_without_processed: number;
    processed_without_source: number;
    active_and_archived_source_lots: number;
    archived_processed_without_timestamp: number;
    first_seen_after_last_seen: number;
    application_start_after_deadline: number;
    archived_before_first_seen: number;
    map_dataset_count: number;
    map_tile_count_mismatch: number;
    map_point_count_mismatch: number;
    map_missing_from_dataset: number;
    map_extra_in_dataset: number;
  };
  host: {
    api: { status: string };
    database: { status: string };
    queue: { status: string; label_ru: string };
    disk: {
      free_gb: number | null;
      critical_below_gb?: number;
      recommended_gb?: number;
      checked_at?: string | null;
      status: "ok" | "warning" | "critical" | "unknown";
    };
    backup: {
      healthy: boolean;
      age_hours: number | null;
      threshold_hours: number;
      restore_verification?: string | null;
      created_at?: string | null;
      size_bytes?: number | null;
    };
    runner_diagnostic: {
      fresh: boolean;
      age_seconds: number | null;
      checked_at?: string | null;
      healthy?: boolean;
      meaning_ru: string;
    };
  };
  data_quality: {
    current_population: number;
    scope_ru: string;
    missing_address: ReliabilityMetric;
    missing_cadastre: ReliabilityMetric;
    missing_price: ReliabilityMetric;
    unknown_status: ReliabilityMetric;
    missing_region: ReliabilityMetric;
    missing_url: ReliabilityMetric;
    non_positive_price: ReliabilityMetric;
    historical_total: number;
  };
  journal: Array<{
    id: number;
    category: "sources" | "geo" | "map" | "infrastructure" | "system" | string;
    severity: string;
    title: string;
    detail: string;
    at: string | null;
  }>;
};

export type ReliabilitySource = {
  canonical_id: string;
  display_name_ru: string;
  aliases: string[];
  role: "configured" | "pilot" | "historical_only";
  configured: boolean;
  scheduled: boolean;
  paused: boolean;
  overall_status: "healthy" | "degraded" | "failed" | "paused" | "not_checked";
  sync_status: string;
  freshness_status: string;
  coverage_status: string;
  circuit_state: string;
  items_seen: number;
  database_records: number;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_complete_success_at: string | null;
  last_failure_at: string | null;
  freshness_age_seconds: number | null;
  complete_snapshot_age_seconds: number | null;
  current_error: string | null;
  current_error_category: string | null;
  last_historical_error: string | null;
  last_historical_error_category: string | null;
  next_retry_at: string | null;
  retry_overdue: boolean;
  retryable: boolean;
  operational_failure: boolean;
  last_probe_at: string | null;
  last_probe_success_at: string | null;
  last_pages_scanned: number;
  last_items_inserted: number;
  last_items_updated: number;
  last_items_unchanged: number;
  last_items_archived: number;
  last_items_failed: number;
  network_fingerprint: Record<string, unknown>;
};
