import React from "react";
import {
  AlertCircle, AlertTriangle, CheckCircle2, ChevronDown, Clock3, Copy, Database,
  Download, HardDrive, Layers3, Map as MapIcon, MapPin, RefreshCcw, Search,
  Server, ShieldCheck, ShieldAlert, XCircle
} from "lucide-react";
import {
  fetchCapabilities, fetchOperationsProgress, fetchQuality, probeSourceHealth,
  type OperationsProgress, type QualitySnapshot,
} from "../../lib/api";
import { fetchReliabilitySources, fetchReliabilitySummary } from "./api";
import type { ReliabilitySource, ReliabilitySummary } from "./api";
import "./reliability.css";

const ru = new Intl.NumberFormat("ru-RU");
const n = (value: number | null | undefined): string => typeof value === "number" && Number.isFinite(value) ? ru.format(value) : "—";
const pct = (value: number | null | undefined): string => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + "%" : "—";
const moscow = (value: string | null | undefined): string => {
  if (!value || Number.isNaN(Date.parse(value))) return "Нет данных";
  return new Date(value).toLocaleString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) + " МСК";
};
const age = (value: string | null | undefined): string => {
  if (!value || Number.isNaN(Date.parse(value))) return "Нет проверки";
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(value)) / 1000));
  if (seconds < 60) return seconds + " сек назад";
  if (seconds < 3600) return Math.floor(seconds / 60) + " мин назад";
  if (seconds < 86400) return Math.floor(seconds / 3600) + " ч назад";
  return Math.floor(seconds / 86400) + " дн назад";
};
const labels: Record<string, string> = {
  healthy: "Норма", degraded: "Ограничения", failed: "Ошибка", paused: "На паузе",
  not_checked: "Нет проверки", fresh: "Актуально", delayed: "Есть задержка",
  stale: "Устарело", missing: "Нет полного снимка", running: "Выполняется",
  queued: "В очереди", partial: "Частично", success: "Успешно", completed: "Завершено",
  closed: "Соединение разрешено", open: "Временно отключён",
  half_open: "Проверка восстановления", network_wait: "Ожидание сети",
};
export const statusLabel = (status: string | null | undefined) => status ? labels[status] || "Неизвестно" : "Нет данных";
export const sourceSeverity = (status: string) => ({ failed: 0, degraded: 1, paused: 2, not_checked: 3, healthy: 4 }[status] ?? 5);
export const sourceNeedsAttention = (source: ReliabilitySource): boolean => source.configured && source.overall_status !== "healthy";

type Filter = "all" | "configured" | "healthy" | "degraded" | "failed" | "paused" | "not_checked";
type JournalFilter = "all" | "errors" | "sources" | "geo";
const qualityItems: Array<[string, string, "bad" | "warn" | "neutral"]> = [
  ["missing_address", "Нет адреса", "bad"],
  ["missing_cadastre", "Нет кадастра", "warn"],
  ["missing_price", "Нет цены", "warn"],
  ["unknown_status", "Неизвестный статус", "warn"],
  ["missing_region", "Нет региона", "warn"],
  ["missing_url", "Нет ссылки на источник", "warn"],
  ["non_positive_price", "Неположительная цена", "bad"],
];

function Dot({ tone }: { tone: string }) {
  return <span aria-hidden="true" className={"rel-dot rel-dot--" + tone} />;
}
function StatCard({ icon, label, value, sub, onClick }: {
  icon: React.ReactNode; label: string; value: string; sub: string; onClick?: () => void;
}) {
  const content = <><span className="rel-statIcon">{icon}</span><span className="rel-statBody"><span>{label}</span><strong>{value}</strong><small>{sub}</small></span></>;
  return onClick ? <button className="rel-stat rel-stat--button" onClick={onClick}>{content}</button> : <div className="rel-stat">{content}</div>;
}
const moveTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

export function ReliabilityView({ refreshToken, role }: { refreshToken: number; role: string }) {
  const [summary, setSummary] = React.useState<ReliabilitySummary | null>(null);
  const [sources, setSources] = React.useState<ReliabilitySource[]>([]);
  const [operations, setOperations] = React.useState<OperationsProgress | null>(null);
  const [history, setHistory] = React.useState<QualitySnapshot | null>(null);
  const [historyChecked, setHistoryChecked] = React.useState(false);
  const [backgroundJobs, setBackgroundJobs] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [refreshing, setRefreshing] = React.useState(false);
  const [failure, setFailure] = React.useState("");
  const [action, setAction] = React.useState("");
  const [sourceFilter, setSourceFilter] = React.useState<Filter>("all");
  const [sourceQuery, setSourceQuery] = React.useState("");
  const [sortBy, setSortBy] = React.useState("severity");
  const [expanded, setExpanded] = React.useState<string[]>([]);
  const [showAllProblems, setShowAllProblems] = React.useState(false);
  const [journalFilter, setJournalFilter] = React.useState<JournalFilter>("all");
  const historyFetchedAt = React.useRef(0);

  const load = React.useCallback(async () => {
    setRefreshing(true);
    const readHistory = historyFetchedAt.current === 0 || Date.now() - historyFetchedAt.current >= 300_000;
    const results = await Promise.allSettled([
      fetchReliabilitySummary(), fetchReliabilitySources(), fetchOperationsProgress(), fetchCapabilities(),
      readHistory ? fetchQuality() : Promise.resolve(null),
    ]);
    if (results[0].status === "fulfilled") setSummary(results[0].value);
    if (results[1].status === "fulfilled") setSources(results[1].value.items);
    if (results[2].status === "fulfilled") setOperations(results[2].value);
    if (results[3].status === "fulfilled") setBackgroundJobs(Boolean(results[3].value.background_jobs));
    if (readHistory) {
      if (results[4].status === "fulfilled" && results[4].value) {
        setHistory(results[4].value);
        historyFetchedAt.current = Date.now();
      }
      setHistoryChecked(true);
    }
    const failed = results.map((result, i) => result.status === "rejected" ? ["Состояние", "Источники", "Журнал", "Доступность операций", "Историческая база"][i] : null).filter(Boolean);
    setFailure(failed.length ? "Не удалось обновить: " + failed.join(", ") + ". Последние полученные значения сохранены." : "");
    setLoading(false);
    setRefreshing(false);
  }, []);

  React.useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load, refreshToken]);

  const geo = summary?.geo;
  const map = summary?.map;
  const integrity = summary?.integrity;
  const host = summary?.host;
  const quality = summary?.data_quality;
  const configured = summary?.sources.configured_total ?? null;
  const stable = summary?.sources.healthy ?? null;
  const degraded = summary?.sources.degraded ?? 0;
  const failed = summary?.sources.failed ?? 0;
  const paused = summary?.sources.paused_total ?? 0;
  const notChecked = summary?.sources.not_checked ?? 0;
  const count = configured ?? 0;
  const stableRatio = count ? (stable ?? 0) / count * 100 : 0;
  const partialRatio = count ? degraded / count * 100 : 0;
  const failedRatio = count ? failed / count * 100 : 0;
  const sourceDonut = "conic-gradient(#2bb950 0 " + stableRatio + "%, #ffc928 " + stableRatio + "% " + (stableRatio + partialRatio) + "%, #f34d52 " + (stableRatio + partialRatio) + "% " + (stableRatio + partialRatio + failedRatio) + "%, #cdd4e1 " + (stableRatio + partialRatio + failedRatio) + "% 100%)";
  const health = summary?.system.status;
  const statusTone = health === "ok" ? "ok" : health === "incident" ? "bad" : health === "degraded" ? "warn" : "unknown";
  const systemTitle = health === "ok" ? "Система работает стабильно" : health === "incident" ? "Требуется вмешательство" : health === "degraded" ? "Система работает с ограничениями" : "Нет свежей проверки";
  const attention = sources.filter(sourceNeedsAttention).sort((a, b) =>
    sourceSeverity(a.overall_status) - sourceSeverity(b.overall_status)
    || Date.parse(a.last_success_at || "1970-01-01") - Date.parse(b.last_success_at || "1970-01-01"));
  const problems = [
    ...(integrity?.critical_count ? [{ id: "map", name: "Карта и целостность", message: "Обнаружены несоответствия набора карты", severity: "bad", at: map?.published_at || null }] : []),
    ...(map && !map.ok && !integrity?.critical_count ? [{ id: "map-delivery", name: "Доставка карты", message: "Публичная доставка не подтверждена", severity: "warn", at: map.published_at || null }] : []),
    ...(host?.backup_healthy === false ? [{ id: "backup", name: "Резервная копия", message: "Нет подтверждённого успешного восстановления", severity: "bad", at: null }] : []),
    ...attention.map(s => ({
      id: s.canonical_id, name: s.display_name_ru, message: s.current_error || (s.retry_overdue ? "Повторная попытка просрочена" : "Полный снимок: " + statusLabel(s.coverage_status) + "; данные: " + statusLabel(s.freshness_status)),
      severity: s.overall_status === "failed" ? "bad" : "warn",
      at: s.last_failure_at || s.last_attempt_at,
    })),
    ...(host?.runner_healthy === false ? [{ id: "runner", name: "Диагностика Runner", message: "Нет свежей проверки; это не означает, что Runner выключен", severity: "warn", at: null }] : []),
  ];
  const filteredSources = sources.filter(s => {
    if (sourceFilter === "configured" && !s.configured) return false;
    if (!["all", "configured"].includes(sourceFilter) && s.overall_status !== sourceFilter) return false;
    return [s.display_name_ru, s.canonical_id, ...s.aliases].join(" ").toLocaleLowerCase("ru-RU").includes(sourceQuery.trim().toLocaleLowerCase("ru-RU"));
  }).sort((a, b) => sortBy === "name" ? a.display_name_ru.localeCompare(b.display_name_ru, "ru")
    : sortBy === "latest" ? (Date.parse(b.last_attempt_at || "") || 0) - (Date.parse(a.last_attempt_at || "") || 0)
    : sourceSeverity(a.overall_status) - sourceSeverity(b.overall_status) || a.display_name_ru.localeCompare(b.display_name_ru, "ru"));
  const filterTabs: Array<[Filter, string, number]> = [
    ["all", "Все", sources.length], ["configured", "Настроенные", count],
    ["healthy", "Стабильные", sources.filter(s => s.overall_status === "healthy").length],
    ["degraded", "Частичные", sources.filter(s => s.overall_status === "degraded").length],
    ["failed", "С ошибками", sources.filter(s => s.overall_status === "failed").length],
    ["paused", "На паузе", sources.filter(s => s.overall_status === "paused").length],
  ];
  const journal = (operations?.journal || []).filter(item =>
    journalFilter === "all" || (journalFilter === "errors" && ["failed", "error"].includes(item.status))
    || (journalFilter === "sources" && item.kind === "sync") || (journalFilter === "geo" && item.kind === "geocoding"));
  const exportPayload = () => ({
    schema_version: 1, generated_at: summary?.generated_at || null,
    system: summary?.system || null,
    source_counts: summary?.sources || null,
    sources: sources.map(s => ({
      canonical_id: s.canonical_id, display_name_ru: s.display_name_ru, aliases: s.aliases,
      configured: s.configured, overall_status: s.overall_status, freshness_status: s.freshness_status,
      coverage_status: s.coverage_status, last_success_at: s.last_success_at,
      last_complete_success_at: s.last_complete_success_at, retry_overdue: s.retry_overdue,
      current_error: s.current_error?.slice(0, 300) || null,
    })),
    geo: geo ? { total: geo.total, geocoded: geo.geocoded, percent: geo.percent, actionable: geo.actionable_remaining, deferred_no_match: geo.deferred_no_match, deferred_validation: geo.deferred_validation, network_wait: geo.network_wait } : null,
    map: map ? { dataset_version: map.dataset_version, point_count: map.point_count, ok: map.ok, published_at: map.published_at, mismatch: { missing: map.missing_from_dataset_count, extra: map.extra_in_dataset_count } } : null,
    quality, integrity,
    infrastructure: host ? { disk_free_gb: host.disk?.free_gb, backup_age_hours: host.backup_age_hours, backup_healthy: host.backup_healthy, runner_age_seconds: host.runner_age_seconds, runner_diagnostic_healthy: host.runner_healthy } : null,
    journal: journal.map(item => ({ kind: item.kind, title: item.title, status: item.status, at: item.at })),
  });
  const download = () => {
    const blob = new Blob([JSON.stringify(exportPayload(), null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "bankrotai-reliability-report.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(JSON.stringify(exportPayload(), null, 2)); }
    catch { setFailure("Не удалось скопировать отчёт. Используйте кнопку экспорта JSON."); }
  };
  const probe = async (source: ReliabilitySource) => {
    setAction(source.canonical_id);
    try { await probeSourceHealth(source.canonical_id); await load(); }
    catch { setFailure("Проверка источника не выполнена. Проверьте права доступа и настройки API."); }
    finally { setAction(""); }
  };

  return <section className="rel-dashboard" aria-label="Надёжность BankrotAI">
    <header className="rel-header">
      <div className="rel-heading"><ShieldCheck size={36} /><div><h1>Надёжность</h1><p>Состояние сбора, обработки и актуальности данных по всем источникам</p></div></div>
      <div className="rel-headerControls">
        <span className="rel-env"><Dot tone="ok" /> Production</span>
        <span className="rel-updated">Обновлено: <b>{moscow(summary?.generated_at)}</b><small>Автообновление каждые 30 сек</small></span>
        <button className="rel-action" disabled={refreshing} onClick={() => void load()}><RefreshCcw size={16} className={refreshing ? "rel-spin" : ""} />{refreshing ? "Обновляем…" : "Обновить"}</button>
        <button className="rel-action" disabled={!summary} onClick={download}><Download size={16} />Экспорт отчёта</button>
      </div>
    </header>
    {failure && <div className="rel-warning" role="status"><AlertCircle size={16} />{failure}</div>}
    {loading && !summary ? <div className="rel-loading" role="status"><span /><span /><span />Загружаем результаты проверки…</div> : <>
      <div className="rel-hero">
        <div className={"rel-system rel-system--" + statusTone}>
          {statusTone === "ok" ? <ShieldCheck size={34} /> : statusTone === "bad" ? <ShieldAlert size={34} /> : <AlertTriangle size={34} />}
          <div><strong>{systemTitle}</strong><p>{summary?.system.reasons[0] || (statusTone === "ok" ? "По подтверждённым проверкам замечаний нет" : "Проверка ещё не завершена")}</p></div>
        </div>
        <div className="rel-heroStats">
          <StatCard icon={<Layers3 />} label="Источники" value={n(stable) + " / " + n(configured)} sub="в штатном режиме · настроенные" onClick={() => moveTo("rel-sources")} />
          <StatCard icon={<MapPin />} label="GEO" value={pct(geo?.percent)} sub={n(geo?.geocoded) + " с координатами"} onClick={() => moveTo("rel-geo")} />
          <StatCard icon={<MapIcon />} label="Карта" value={n(map?.point_count)} sub={map?.ok ? "публичная карта проверена" : "доставка требует проверки"} onClick={() => moveTo("rel-map")} />
          <StatCard icon={<Database />} label="Актуальные лоты" value={n(quality?.population)} sub="текущая рабочая выборка" onClick={() => moveTo("rel-quality")} />
        </div>
      </div>
      <div className="rel-mainGrid">
        <section className="rel-card">
          <h2><Layers3 size={19} />Общая надёжность источников</h2>
          <div className="rel-chartRow">
            <div className="rel-donut" style={{ background: count ? sourceDonut : "#e4e8f0" }} aria-label={pct(count ? stableRatio : null) + " настроенных источников стабильны"}>
              <div><strong>{pct(count ? stableRatio : null)}</strong><span>работают без замечаний</span></div>
            </div>
            <div className="rel-legend">
              <div><Dot tone="ok" /><span>Работают стабильно</span><b>{n(stable)}</b></div>
              <div><Dot tone="warn" /><span>С ограничениями</span><b>{n(degraded)}</b></div>
              <div><Dot tone="bad" /><span>С ошибками</span><b>{n(failed)}</b></div>
              <div><Dot tone="neutral" /><span>Пауза / нет проверки</span><b>{n(paused + notChecked)}</b></div>
              <small>Настроено: {n(configured)} · всего в реестре: {n(sources.length)}. Исторические и пилотные источники не влияют на знаменатель.</small>
            </div>
          </div>
        </section>
        <section className="rel-card" id="rel-attention">
          <div className="rel-cardTitle"><h2><AlertTriangle size={19} />Актуальные проблемы <em>({problems.length})</em></h2><button className="rel-link" onClick={() => setShowAllProblems(v => !v)}>{showAllProblems ? "Свернуть" : "Все проблемы"}</button></div>
          <div className="rel-issues">
            {(showAllProblems ? problems : problems.slice(0, 5)).map(p => <div className="rel-issue" key={p.id}>
              <span className="rel-issueName">{p.name}</span><span title={p.message} className="rel-issueMessage">{p.message}</span>
              <span className="rel-issueDate" title={moscow(p.at)}>{age(p.at)}</span>
              <span className={"rel-pill rel-pill--" + p.severity}>{p.severity === "bad" ? "Высокий" : "Средний"}</span>
            </div>)}
            {!problems.length && <p className="rel-empty">Актуальных проблем по доступным проверкам не обнаружено.</p>}
          </div>
        </section>
      </div>
      <section className="rel-card" id="rel-sources">
        <div className="rel-cardTitle"><h2><Layers3 size={19} />Источники данных <span>({sources.length})</span></h2><small>Настроенные, подключённые и исторические · строка на один источник</small></div>
        <div className="rel-toolbar">
          <div className="rel-tabs" role="group" aria-label="Фильтр источников">
            {filterTabs.map(([key, title, value]) => <button key={key} type="button" aria-pressed={sourceFilter === key} className={"rel-tab rel-tab--" + key + (sourceFilter === key ? " is-active" : "")} onClick={() => setSourceFilter(key)}>{title} <b>{n(value)}</b></button>)}
          </div>
          <label className="rel-search"><Search size={17} /><input aria-label="Поиск по источнику" placeholder="Поиск по источнику…" value={sourceQuery} onChange={e => setSourceQuery(e.target.value)} /></label>
          <label className="rel-sort"><span className="srOnly">Сортировка источников</span><select value={sortBy} onChange={e => setSortBy(e.target.value)}><option value="severity">Сначала с проблемами</option><option value="latest">По последнему проходу</option><option value="name">По названию</option></select></label>
        </div>
        <div className="rel-tableScroll"><div className="rel-table" role="table" aria-label="Состояние источников">
          <div role="row" className="rel-tableHead"><span>Источник</span><span>Статус</span><span>Актуальность</span><span>Полный снимок</span><span>За проход</span><span>Последний проход</span><span>Изменения</span><span>Текущая проблема</span><span /></div>
          {filteredSources.map(source => {
            const open = expanded.includes(source.canonical_id);
            const tone = source.overall_status === "healthy" ? "ok" : source.overall_status === "failed" ? "bad" : source.overall_status === "degraded" ? "warn" : "neutral";
            const message = source.current_error || (source.retry_overdue ? "Повтор просрочен" : "—");
            return <React.Fragment key={source.canonical_id}>
              <button type="button" className="rel-tableRow" role="row" aria-expanded={open} onClick={() => setExpanded(prev => open ? prev.filter(id => id !== source.canonical_id) : [...prev, source.canonical_id])}>
                <span className="rel-sourceName"><i aria-hidden="true">{source.display_name_ru.slice(0, 1).toUpperCase()}</i><strong>{source.display_name_ru}</strong><small>{source.canonical_id}</small></span>
                <span><span className={"rel-pill rel-pill--" + tone}><Dot tone={tone} />{statusLabel(source.overall_status)}</span></span>
                <span><span className={"rel-pill rel-pill--" + (source.freshness_status === "fresh" ? "ok" : source.freshness_status === "stale" || source.freshness_status === "failed" ? "bad" : "warn")}>{statusLabel(source.freshness_status)}</span></span>
                <span><span className={"rel-pill rel-pill--" + (source.coverage_status === "fresh" ? "ok" : "warn")}>{statusLabel(source.coverage_status)}</span></span>
                <span className="rel-mono">{n(source.items_seen)}</span>
                <span title={moscow(source.last_attempt_at)}>{age(source.last_attempt_at)}</span>
                <span className="rel-mono">+{n(source.last_items_inserted)} / Δ{n(source.last_items_updated)}</span>
                <span className="rel-ellipsis" title={message}>{message}</span><ChevronDown className={open ? "rel-flip" : ""} size={17} />
              </button>
              {open && <div className="rel-rowDetails"><div><b>Последний успех</b><span>{moscow(source.last_success_at)}</span></div><div><b>Полный снимок</b><span>{moscow(source.last_complete_success_at)}</span></div><div><b>Повтор</b><span>{source.retry_overdue ? "Просрочен: " : ""}{moscow(source.next_retry_at)}</span></div><div><b>Соединение</b><span>{statusLabel(source.circuit_state)}</span></div><div><b>Добавлено / обновлено / архивировано</b><span>{n(source.last_items_inserted)} / {n(source.last_items_updated)} / {n(source.last_items_archived)}</span></div><div><b>Исторические имена</b><span>{source.aliases.length ? source.aliases.join(", ") : "Нет"}</span></div>{source.last_historical_error && <p>Историческая ошибка: {source.last_historical_error.slice(0, 300)}</p>}{role === "admin" && backgroundJobs && source.registered && <button className="rel-action" disabled={Boolean(action)} onClick={() => void probe(source)}><RefreshCcw size={14} />Проверить источник</button>}</div>}
            </React.Fragment>;
          })}
          {!filteredSources.length && <p className="rel-empty">Источники по заданным условиям не найдены.</p>}
        </div></div>
        <p className="rel-footnote">«За проход» — число просмотренных записей последней синхронизации, не объём базы. Текущая ошибка не включает сбои, закрытые более поздним успешным проходом.</p>
      </section>
      <div className="rel-bottomGrid">
        <section className="rel-card" id="rel-geo">
          <h2><MapPin size={19} />GEO · координаты</h2>
          <div className="rel-geoNumbers"><div><strong>{n(geo?.geocoded)}</strong><small>с текущими координатами</small></div><div><strong>{pct(geo?.percent)}</strong><small>покрытие выборки</small></div></div>
          <div className="rel-progress" role="progressbar" aria-label="Покрытие GEO" aria-valuenow={geo?.percent ?? 0} aria-valuemin={0} aria-valuemax={100}><span style={{ width: String(Math.min(100, Math.max(0, geo?.percent || 0))) + "%" }} /></div>
          <p className="rel-footnote">Объектов в текущей GEO-выборке: {n(geo?.total)}</p>
          <div className="rel-kv"><div><Dot tone="warn" />Требуют обработки <b>{n(geo?.actionable_remaining)}</b></div><div><Dot tone="warn" />Соответствие не найдено <b>{n(geo?.deferred_no_match)}</b></div><div><Dot tone="bad" />Не прошло проверку <b>{n(geo?.deferred_validation)}</b></div><div><Dot tone="neutral" />Ожидание сети <b>{n(geo?.network_wait)}</b></div><div><Dot tone="neutral" />Отложено очередью <b>{n(geo?.p7_held)}</b></div><div><Dot tone="neutral" />Недостаточно данных <b>{n(geo?.deferred_bad_input)}</b></div></div>
        </section>
        <section className="rel-card" id="rel-quality">
          <h2><Database size={19} />Полнота и качество данных</h2>
          <div className="rel-qualityIntro"><span>Актуальных публикаций</span><strong>{n(quality?.population)}</strong></div>
          <div className="rel-kv">{qualityItems.map(([key, label, tone]) => <div key={key}><Dot tone={tone} />{label}<b>{n(quality?.[key as keyof typeof quality] as number | undefined)} <small>{quality?.percentages?.[key] == null ? "—" : pct(quality.percentages[key])}</small></b></div>)}</div>
          <details className="rel-history"><summary>Историческая база <ChevronDown size={15} /></summary>{historyChecked && history ? <div className="rel-kv"><div>Всего записей <b>{n(history.total_lots)}</b></div><div>Активные по статусу (история) <b>{n(history.active_lots)}</b></div><div>Архивные <b>{n(history.archived_lots)}</b></div><div>Дубликаты <b>{n(history.duplicate_lots)}</b></div></div> : <p>Историческая проверка недоступна</p>}</details>
          <p className="rel-footnote">Проценты рассчитаны только от текущей рабочей выборки. Историческая база не влияет на этот знаменатель.</p>
        </section>
        <section className="rel-card" id="rel-infra">
          <h2><Server size={19} />Инфраструктура</h2>
          <div className="rel-infra">
            <div><HardDrive size={18} /><span>Свободно на диске</span><b className={host?.disk?.free_gb == null ? "rel-textNeutral" : host.disk.free_gb < 15 ? "rel-textBad" : host.disk.free_gb < 25 ? "rel-textWarn" : "rel-textOk"}>{host?.disk?.free_gb == null ? "Нет данных" : host.disk.free_gb.toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + " ГБ"}<small>Норма от 25 ГБ · критично ниже 15 ГБ</small></b></div>
            <div><CheckCircle2 size={18} /><span>Резервная копия</span><b className={host?.backup_healthy ? "rel-textOk" : "rel-textWarn"}>{host?.backup_healthy ? "Восстановление проверено" : "Требует проверки"}<small>{host?.backup_age_hours == null ? "Возраст неизвестен" : pct(null).replace("—", host.backup_age_hours.toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + " ч назад")}</small></b></div>
            <div><Clock3 size={18} /><span>Диагностика Runner</span><b className={host?.runner_healthy ? "rel-textOk" : "rel-textWarn"}>{host?.runner_healthy ? "Свежая проверка" : "Нет свежей проверки"}<small>{host?.runner_age_seconds == null ? "Возраст неизвестен" : n(Math.round(host.runner_age_seconds / 60)) + " мин назад"} · не статус процесса</small></b></div>
            <div><Layers3 size={18} /><span>Источники</span><b className={failed || degraded ? "rel-textWarn" : "rel-textOk"}>{n(stable)} из {n(configured)} без замечаний<small>Данные планового сбора</small></b></div>
          </div>
          <button className="rel-link" onClick={() => moveTo("rel-map")}>Детальная проверка доставки карты</button>
        </section>
      </div>
      <section className="rel-card" id="rel-map">
        <div className="rel-cardTitle"><h2><MapIcon size={19} />Карта: доставка и целостность</h2><span className={"rel-pill rel-pill--" + (map?.ok ? "ok" : "warn")}>{map?.ok ? "Проверена" : "Не подтверждена"}</span></div>
        <div className="rel-delivery">
          {[
            ["База данных", integrity?.current_dataset_count === 1, "Текущих наборов: " + n(integrity?.current_dataset_count)],
            ["Набор карты", map?.dataset_status === "ready" && map?.point_count_matches === true && map?.tile_count_matches === true, "Версия: " + (map?.dataset_version || "нет")],
            ["Публичный манифест", map?.public_manifest?.checked && map?.public_manifest?.ok === true, map?.public_manifest?.checked ? "Сверены версия и размеры" : "Не проверялся"],
            ["Согласованность", map?.ok, "Отсутствуют: " + n(map?.missing_from_dataset_count) + " · лишние: " + n(map?.extra_in_dataset_count)],
          ].map(([name, checked, detail]) => <div key={String(name)}><span>{checked ? <CheckCircle2 size={19} className="rel-textOk" /> : <XCircle size={19} className="rel-textWarn" />}</span><b>{name}</b><small>{detail}</small></div>)}
        </div>
        <p className="rel-footnote">Опубликовано: {moscow(map?.published_at)} · После публикации ожидают следующей сборки: {n(map?.pending_missing_count)} новых и {n(map?.pending_extra_count)} исключаемых объектов. Проверка манифеста кешируется 5 минут.</p>
      </section>
      <section className="rel-card" id="rel-journal">
        <div className="rel-cardTitle"><h2><Clock3 size={19} />Журнал операций</h2><div className="rel-tabs" role="group" aria-label="Фильтр журнала">{([["all", "Все"], ["errors", "Ошибки"], ["sources", "Источники"], ["geo", "GEO"]] as Array<[JournalFilter, string]>).map(([key, value]) => <button key={key} className={"rel-tab" + (journalFilter === key ? " is-active" : "")} aria-pressed={journalFilter === key} onClick={() => setJournalFilter(key)}>{value}</button>)}</div></div>
        <div className="rel-journal">{journal.slice(0, 60).map((item, i) => <div key={item.kind + String(i)}><span className={"rel-pill rel-pill--" + (item.status === "failed" ? "bad" : item.status === "success" || item.status === "completed" ? "ok" : "warn")}>{statusLabel(item.status)}</span><strong>{item.title}</strong><span>{item.detail}</span><small>{moscow(item.at)}</small></div>)}{!journal.length && <p className="rel-empty">Событий для выбранного фильтра нет.</p>}</div>
      </section>
      <footer className="rel-footer"><span>Версия: {summary?.system.deployment_sha?.slice(0, 8) || "Неизвестно"} · Время всех проверок — Москва</span><button className="rel-action" disabled={!summary} onClick={() => void copy()}><Copy size={15} />Копировать отчёт</button></footer>
    </>}
  </section>;
}
