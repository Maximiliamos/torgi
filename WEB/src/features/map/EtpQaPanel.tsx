import React from "react";
import {
  fetchEtpQaPreview,
  fetchEtpQaSources,
} from "../../lib/api";
import type { EtpQaPreview, MapMarkerLot } from "../../lib/api";

export function useEtpQa(isAdmin: boolean) {
  const [qaSource, setQaSource] = React.useState("m-ets.ru");
  const [qaSources, setQaSources] = React.useState<Array<{
    id: string; label: string; blocked: boolean;
  }>>([]);
  const [qaReports, setQaReports] = React.useState<Record<string, EtpQaPreview>>({});
  const [qaLoading, setQaLoading] = React.useState(false);
  const [qaError, setQaError] = React.useState("");
  const [qaSelected, setQaSelected] = React.useState<number | null>(null);

  const qaReport = qaReports[qaSource];
  const qaPoints = React.useMemo<MapMarkerLot[]>(() =>
    (qaReport?.items ?? [])
      .filter((item) => item.lat !== null && item.lon !== null)
      .map((item, index) => ({
        id: index + 1,
        title: item.title,
        address: item.cadastral_number,
        current_price: item.current_price,
        start_price: item.start_price,
        region_code: null,
        status: "qa_unverified",
        is_archived: false,
        review_status: "maybe",
        lat: item.lat as number,
        lon: item.lon as number,
      })), [qaReport]);
  const qaSelectedItem = qaSelected === null
    ? null
    : qaReport?.items.filter((item) => item.lat !== null && item.lon !== null)[qaSelected - 1] ?? null;

  React.useEffect(() => {
    if (!isAdmin) return;
    let alive = true;
    void fetchEtpQaSources()
      .then((data) => { if (alive) setQaSources(data.items); })
      .catch(() => { if (alive) setQaError("Не удалось получить профили ЭТП"); });
    return () => { alive = false; };
  }, [isAdmin]);

  const loadQaSource = React.useCallback(async () => {
    setQaLoading(true);
    setQaError("");
    setQaSelected(null);
    try {
      const data = await fetchEtpQaPreview(qaSource);
      if (!data.qa_only || data.database_writes !== 0 || data.complete_source_inventory !== false) {
        throw new Error("Источник не подтвердил изолированный QA-режим");
      }
      setQaReports((value) => ({ ...value, [qaSource]: data }));
      if (data.mapped_count > 0) setQaSelected(1);
    } catch (error) {
      setQaError(error instanceof Error ? error.message : "Ошибка пробного обхода ЭТП");
    } finally {
      setQaLoading(false);
    }
  }, [qaSource]);

  return {
    qaSource, setQaSource, qaSources, qaReport, qaPoints,
    qaLoading, qaError, setQaError, qaSelected, setQaSelected,
    qaSelectedItem, loadQaSource,
  };
}

export function EtpQaPanel({ qa }: { qa: ReturnType<typeof useEtpQa> }) {
  const {
    qaSource, setQaSource, qaSources, qaReport, qaLoading, qaError,
    setQaError, setQaSelected, qaSelectedItem, loadQaSource,
  } = qa;
  return (
    <section className="mapControlPanel" aria-label="Ручная проверка ЭТП">
      <h2>Тестовая карта ЭТП</h2>
      <p><strong>QA / не проверено.</strong> Эти объекты не внесены в основной каталог и не участвуют в торгах через приложение.</p>
      <label>
        Площадка
        <select aria-label="Тестовая ЭТП" value={qaSource} onChange={(event) => {
          setQaSource(event.target.value); setQaSelected(null); setQaError("");
        }}>
          {qaSources.length ? qaSources.map((item) => (
            <option key={item.id} value={item.id}>{item.label}{item.blocked ? " · недоступна" : ""}</option>
          )) : <option value="m-ets.ru">МЭТС</option>}
        </select>
      </label>
      <button disabled={qaLoading} onClick={() => void loadQaSource()}>
        {qaLoading ? "Проверка источника…" : "Получить лоты и геокодировать"}
      </button>
      {qaReport && <>
        <p>Получено кандидатов: {qaReport.candidate_count}. Точек с подтверждёнными координатами: {qaReport.mapped_count}. Страниц проверено: {qaReport.pages_fetched}.</p>
        {qaReport.blocked_reason && <p role="status">Недоступно: {qaReport.blocked_reason}</p>}
        {qaReport.errors.length > 0 && <p role="status">Ограничения: {qaReport.errors.join(", ")}</p>}
        <div className="mapFavoritesList">
          {qaReport.items.map((item, index) => {
            const mappedIndex = qaReport.items.slice(0, index + 1).filter(x => x.lat !== null && x.lon !== null).length;
            return <div key={item.external_id}>
              <button type="button" disabled={item.lat === null || item.lon === null}
                onClick={() => setQaSelected(mappedIndex)}>
                <strong>{item.title}</strong>
                <small>{item.external_id} · {item.detail_level} · {item.geo_status}</small>
                <small>Начальная: {item.start_price ?? "неизвестна"} · текущая: {item.current_price ?? "неизвестна"}</small>
              </button>
              <a href={item.url} target="_blank" rel="noopener noreferrer">Открыть карточку ЭТП</a>
            </div>;
          })}
          {qaReport.items.length === 0 && <small>Кандидатов нет; проверьте доступность и парсер источника.</small>}
        </div>
        {qaSelectedItem && <p>Выбрана метка: {qaSelectedItem.title} — {qaSelectedItem.cadastral_number}</p>}
      </>}
      {qaError && <p role="alert">{qaError}</p>}
      <small>Координаты — только по точному кадастровому номеру. Нет координат — нет точки; объект остаётся в списке.</small>
    </section>
  );
}
