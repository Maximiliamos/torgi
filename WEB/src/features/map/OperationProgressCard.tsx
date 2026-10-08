/** Operations, source readiness and GEO progress panel (BAT-308). */
import React from "react";
import type { OperationsProgress } from "../../lib/api";

function durationLabel(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return null;
  const minutes = Math.max(0, Math.round(seconds / 60));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours) return `${hours} ч ${rest} мин`;
  return `${rest} мин`;
}

function journalTime(value?: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(parsed);
}

function operationsDateTime(value?: string | null) {
  if (!value) return "нет данных";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "нет данных";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(parsed);
}

export function operationsSourceSummaryLabel(
  summary?: OperationsProgress["summary"],
) {
  if (!summary || summary.sources.total === 0) return "Источники ещё не проверены";
  return summary.sources.ready + "/" + summary.sources.total + " источника готовы";
}

export function OperationProgressCard({
  value, isAdmin, controlBusy, onPause, onResume,
}: {
  value: OperationsProgress; isAdmin: boolean; controlBusy: boolean;
  onPause: () => void; onResume: () => void;
}) {
  const activeSync = value.sync && ["queued", "running"].includes(value.sync.status);
  const activeSources = value.sync?.sources.filter((source) => ["queued", "running"].includes(source.status)) ?? [];
  const completedSources = value.sync?.sources.filter((source) => source.status === "success").length ?? 0;
  const failedSources = value.sync?.sources.filter((source) => source.status === "failed") ?? [];
  const sourceTotal = value.sync?.sources.length ?? 0;
  const batch = value.geocoding.task?.status === "running" ? value.geocoding.task.progress : null;
  const completionTime = value.geocoding.expected_completion_at
    ? new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
      .format(new Date(value.geocoding.expected_completion_at))
    : null;
  const nextRetryTime = value.geocoding.next_retry_at
    ? new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
      .format(new Date(value.geocoding.next_retry_at))
    : null;
  const eligibleNow = value.geocoding.eligible_now ?? value.geocoding.actionable_remaining ?? value.geocoding.remaining;
  const waitingForRetry = value.geocoding.waiting_for_retry ?? 0;
  const p7Held = value.geocoding.p7_held ?? 0;
  const deferredNoMatch = value.geocoding.deferred_no_match ?? 0;
  const deferredValidation = value.geocoding.deferred_validation ?? 0;
  const deferredBadInput = value.geocoding.deferred_bad_input ?? 0;
  const fastDrain = value.geocoding.fast_drain;
  const fastDrainProgress = fastDrain?.progress ?? null;
  const journal = value.journal ?? [];
  const summary = value.summary;
  const pausedSources = summary?.sources.items.filter((source) => source.paused) ?? [];
  if (!summary && !activeSync && !batch && value.geocoding.remaining === 0 && journal.length === 0) return null;
  return (
    <section className="mapOperationProgress" aria-label="Ход обработки данных">
      {summary && (
        <div className="mapOperationsOverview">
          <div className="mapOperationsOverviewHeader">
            <strong>Состояние данных</strong>
            <span data-ready={summary.sources.ready === summary.sources.total && summary.sources.total > 0}>
              {operationsSourceSummaryLabel(summary)}
            </span>
          </div>
          <div className="mapOperationsMetrics">
            <div>
              <small>Источники</small>
              <b>{summary.sources.ready}/{summary.sources.total}</b>
              <span>полный снимок актуален</span>
            </div>
            <div>
              <small>Последнее обновление</small>
              <b>{operationsDateTime(summary.last_update_at)}</b>
              <span>{summary.sources.items.filter((source) => !source.paused && source.last_error_category).length} предупреждений</span>
            </div>
            <div>
              <small>Координаты</small>
              <b>{value.geocoding.percent.toFixed(1)}%</b>
              <span>{value.geocoding.remaining} без координат</span>
            </div>
            <div>
              <small>Карта</small>
              <b>{summary.map?.point_count ?? 0}</b>
              <span>{summary.map?.status === "ready" ? "актуальна" : summary.map?.status ?? "нет dataset"}</span>
            </div>
          </div>
          {pausedSources.length > 0 && (
            <small className="mapOperationsPaused">
              Изолированы от общего обновления: {pausedSources.map((source) => source.source_system).join(", ")}
            </small>
          )}
        </div>
      )}
      {activeSync && (
        <div>
          <strong>Поиск лотов</strong>
          <span>{completedSources} из {sourceTotal} площадок завершено</span>
          {activeSources.slice(0, 2).map((source) => (
            <React.Fragment key={source.source_system}>
              <progress max={100} value={source.percent ?? 0} />
              <small>{source.source_system}: {source.items_seen} лотов{source.current_category ? ` · ${source.current_category}` : ""}</small>
            </React.Fragment>
          ))}
        </div>
      )}
      {value.sync && !activeSync && (
        <div>
          <strong>Последний поиск лотов</strong>
          <span>{completedSources} из {sourceTotal} площадок завершено</span>
          {failedSources.length > 0 && (
            <small className="mapOperationProgressStatus">
              Не завершено: {failedSources.map((source) => source.source_system).join(", ")}
            </small>
          )}
        </div>
      )}
      <div>
        <strong>Координаты найдены — {value.geocoding.percent.toFixed(1)}%</strong>
        <progress max={100} value={value.geocoding.percent} />
        <span>{value.geocoding.geocoded} из {value.geocoding.total} с координатами · без координат {value.geocoding.remaining}</span>
        <small>
          Доступно сейчас: {eligibleNow} · ждут обычного retry: {waitingForRetry} · P7 очередь: {p7Held}
        </small>
        <small>
          Отложено: no-match {deferredNoMatch} · validation {deferredValidation} · без GEO-входа {deferredBadInput} · terminal {value.geocoding.terminal_failures}
        </small>
        {value.geocoding.eta_seconds != null && value.geocoding.eta_seconds > 0 && eligibleNow > 0 && (
          <small>
            Текущая доступная очередь: ≈ {durationLabel(value.geocoding.eta_seconds)}
            {completionTime ? ` · завершение около ${completionTime}` : ""}
            {value.geocoding.rate_per_second ? ` · ${value.geocoding.rate_per_second.toFixed(2)} лота/с` : ""}
          </small>
        )}
        {value.geocoding.drain_eta_seconds != null && value.geocoding.drain_eta_seconds > 0 && p7Held > 0 && (
          <small>
            P7 первичный проход: ≈ {durationLabel(value.geocoding.drain_eta_seconds)}
            {value.geocoding.rate_per_second ? ` · ${value.geocoding.rate_per_second.toFixed(2)} лота/с` : ""}
          </small>
        )}
        {fastDrain && ["queued", "running"].includes(fastDrain.status) && (
          <small className="mapOperationProgressStatus">
            P7 Fast Drain: {fastDrainProgress?.phase ?? fastDrain.status}
            {fastDrainProgress?.processed != null ? ` · обработано ${fastDrainProgress.processed}` : ""}
            {fastDrainProgress?.p7_held != null ? ` · осталось в held ${fastDrainProgress.p7_held}` : ""}
          </small>
        )}
        {eligibleNow === 0 && waitingForRetry > 0 && (
          <small className="mapOperationProgressStatus">
            Сейчас доступных задач нет{nextRetryTime ? ` · следующая попытка после ${nextRetryTime}` : ""}
          </small>
        )}
        {value.geocoding.paused && <small className="mapOperationProgressStatus">Приостановлено: текущий пакет безопасно завершается.</small>}
        {batch && <small>Запросы геокодера: {batch.resolved_queries ?? 0} из {batch.unique_queries ?? batch.queued ?? 0} · из кеша {batch.cache_hits ?? 0}</small>}
        {batch && <small>Текущий пакет: {batch.processed ?? 0} из {batch.queued ?? 0} · успешно {batch.geocoded ?? 0} · ошибок {batch.failed ?? 0}</small>}
        {isAdmin && (
          <div className="mapOperationProgressActions">
            {value.geocoding.paused
              ? <button type="button" disabled={controlBusy} onClick={onResume}>{controlBusy ? "Возобновляем…" : "Возобновить"}</button>
              : <button type="button" disabled={controlBusy} onClick={onPause}>{controlBusy ? "Приостанавливаем…" : "Пауза"}</button>}
          </div>
        )}
      </div>
      {journal.length > 0 && (
        <div className="mapOperationJournal">
          <div className="mapOperationJournalHeader">
            <strong>Журнал</strong>
            <span>последние процессы</span>
          </div>
          {journal.slice(0, 6).map((entry, index) => (
            <div className="mapOperationJournalRow" key={`${entry.kind}-${entry.title}-${entry.at ?? index}`}>
              <time dateTime={entry.at ?? undefined}>{journalTime(entry.at)}</time>
              <i data-status={entry.status} aria-hidden="true" />
              <div>
                <b>{entry.title}</b>
                <small>{entry.detail}{entry.percent != null ? ` · ${entry.percent.toFixed(1)}%` : ""}</small>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
