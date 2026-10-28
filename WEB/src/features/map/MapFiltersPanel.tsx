import React from "react";
import type { RegionOption } from "../../lib/api";

export type MapSourceOption = {
  id: string;
  label: string;
  ingestion_stage: "connected" | "pilot";
  has_database_records: boolean;
};

export type MapFilterState = {
  region: string;
  minPrice: string;
  maxPrice: string;
  sources?: string[];
};
export const EMPTY_MAP_FILTERS: MapFilterState = {
  region: "", minPrice: "", maxPrice: "", sources: [],
};

export function mapFilterUiState(filters: MapFilterState, applied: MapFilterState) {
  const selected = [...(filters.sources ?? [])].sort().join(",");
  const previous = [...(applied.sources ?? [])].sort().join(",");
  return {
    dirty:
      filters.region !== applied.region ||
      filters.minPrice !== applied.minPrice ||
      filters.maxPrice !== applied.maxPrice ||
      selected !== previous,
    appliedCount: [
      applied.region, applied.minPrice, applied.maxPrice,
      (applied.sources ?? []).length > 0 ? "sources" : "",
    ].filter(Boolean).length,
  };
}

export function MapFiltersPanel({
  filters, setFilters, applied, regions, sources,
  onApply, onReset,
}: {
  filters: MapFilterState;
  setFilters: React.Dispatch<React.SetStateAction<MapFilterState>>;
  applied: MapFilterState;
  regions: RegionOption[];
  sources: MapSourceOption[];
  onApply: () => void;
  onReset: () => void;
}) {
  const ui = mapFilterUiState(filters, applied);
  const selected = filters.sources ?? [];
  const toggle = (sourceId: string) => {
    setFilters((prev) => ({
      ...prev,
      sources: (prev.sources ?? []).includes(sourceId)
        ? (prev.sources ?? []).filter((id) => id !== sourceId)
        : [...(prev.sources ?? []), sourceId],
    }));
  };
  return (
    <form className="mapFiltersForm" onSubmit={(event) => { event.preventDefault(); onApply(); }}>
      <fieldset className="mapFiltersBox">
        <legend>Фильтры{ui.appliedCount > 0 ? ` · применено ${ui.appliedCount}` : ""}</legend>
        <label>
          <span>Стартовая цена от</span>
          <input type="number" min="0" value={filters.minPrice}
            onChange={(event) => setFilters((prev) => ({ ...prev, minPrice: event.target.value }))}
            placeholder="Не задано" />
        </label>
        <label>
          <span>Стартовая цена до</span>
          <input type="number" min="0" value={filters.maxPrice}
            onChange={(event) => setFilters((prev) => ({ ...prev, maxPrice: event.target.value }))}
            placeholder="Не задано" />
        </label>
        <label>
          <span>Субъект РФ</span>
          <select value={filters.region}
            onChange={(event) => setFilters((prev) => ({ ...prev, region: event.target.value }))}>
            <option value="">Все регионы</option>
            {regions.map((region) => (
              <option key={region.code} value={region.code}>
                {region.code} — {region.name}
              </option>
            ))}
          </select>
        </label>
        <details className="mapSourcesDropdown">
          <summary>Источники ЭТП: {selected.length ? `выбрано ${selected.length}` : "все"}</summary>
          <div className="mapSourceChoices" role="group" aria-label="Выбор площадок для карты">
            {sources.length ? sources.map((source) => (
              <label key={source.id} className="mapSourceChoice">
                <input type="checkbox" aria-label={source.label}
                  checked={selected.includes(source.id)}
                  onChange={() => toggle(source.id)} />
                <span>{source.label}
                  <small>{source.has_database_records ? "Есть записи в базе" : "Пока нет записей"}
                    {source.ingestion_stage === "pilot" ? " · пилот" : ""}
                  </small>
                </span>
              </label>
            )) : <small>Список площадок недоступен. Повторите загрузку страницы.</small>}
          </div>
          <small>Выбранные площадки объединяются по ИЛИ. Пилотные ЭТП появятся на карте после проверенного импорта.</small>
        </details>
        <div>
          <button type="submit" disabled={!ui.dirty}>{ui.dirty ? "Применить" : "Применено"}</button>
          <button type="button" disabled={ui.appliedCount === 0 && !ui.dirty} onClick={onReset}>
            Сбросить
          </button>
        </div>
        <small className={ui.dirty ? "mapFilterHint mapFilterHint--pending" : "mapFilterHint"}>
          {ui.dirty ? "Есть неприменённые изменения"
            : ui.appliedCount > 0 ? "Фильтры применены к карте"
            : "Показаны все лоты"}
        </small>
      </fieldset>
    </form>
  );
}
