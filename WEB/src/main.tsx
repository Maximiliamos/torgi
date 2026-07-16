import React from "react";
import { createRoot } from "react-dom/client";
import {
  AlertCircle,
  ArrowDownAZ,
  ArrowDownUp,
  Banknote,
  Building2,
  ExternalLink,
  FileSearch,
  Filter,
  Loader2,
  MapPin,
  RefreshCcw,
  Search,
  ShieldAlert,
  Sparkles,
  X
} from "lucide-react";
import {
  fetchLotDetail,
  fetchLots,
  fetchStats,
  LotDetail,
  LotListItem,
  LotQuery,
  SortMode,
  StatsResponse
} from "./lib/api";
import "./styles.css";

const CATEGORY_OPTIONS = [
  ["land", "Земля"],
  ["car", "Авто"],
  ["apartment", "Квартиры"],
  ["house", "Дома"],
  ["commercial_room", "Помещения"],
  ["commercial_building", "Здания"],
  ["commercial_building_with_land", "Здания с землей"],
  ["receivable", "Дебиторка"],
  ["other", "Прочее"]
] as const;

const STATUS_OPTIONS = [
  ["active", "Активные"],
  ["scheduled", "Запланированные"],
  ["unknown", "Без статуса"]
] as const;

const SORT_OPTIONS: { value: SortMode; label: string }[] = [
  { value: "recommended", label: "Рекомендации" },
  { value: "discount", label: "Дисконт" },
  { value: "price_asc", label: "Цена ↑" },
  { value: "price_desc", label: "Цена ↓" },
  { value: "newest", label: "Новые" }
];

const REGION_OPTIONS = [
  { value: "yaroslavl", label: "Ярославская область" },
  { value: "76", label: "Ярославль, код 76" },
  { value: "84", label: "TBankrot, код 84" }
];

const initialQuery: LotQuery = {
  city_slug: "yaroslavl",
  page: 1,
  per_page: 18,
  search: "",
  categories: [],
  statuses: ["active", "scheduled"],
  min_discount: 0,
  max_discount: 100,
  sort: "recommended"
};

function formatMoney(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0
  }).format(value);
}

function formatNumber(value: number | null | undefined, suffix = "") {
  if (value === null || value === undefined) return "—";
  return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(value)}${suffix}`;
}

function categoryLabel(value: string) {
  return CATEGORY_OPTIONS.find(([id]) => id === value)?.[1] || value;
}

function statusLabel(value: string) {
  return STATUS_OPTIONS.find(([id]) => id === value)?.[1] || value;
}

function toggleValue(list: string[], value: string) {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

function Kpi({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: string; tone: string }) {
  return (
    <section className={`kpi ${tone}`}>
      <div className="kpiIcon">{icon}</div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </section>
  );
}

function FilterChip({
  active,
  label,
  onClick
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button className={`chip ${active ? "active" : ""}`} type="button" onClick={onClick}>
      {label}
    </button>
  );
}

function LotRow({ lot, selected, onOpen }: { lot: LotListItem; selected: boolean; onOpen: () => void }) {
  return (
    <button className={`lotRow ${selected ? "selected" : ""}`} type="button" onClick={onOpen}>
      <span className="lotMain">
        <strong>{lot.title}</strong>
        <span>{lot.address || lot.description || "Адрес не указан"}</span>
      </span>
      <span className="lotMeta">
        <span>{categoryLabel(lot.category)}</span>
        <span>{statusLabel(lot.auction_status)}</span>
      </span>
      <span className="lotNumber">{formatMoney(lot.current_price)}</span>
      <span className="lotNumber accent">{formatNumber(lot.discount_percent, "%")}</span>
      <span className="lotNumber">{formatNumber(lot.rating)}</span>
    </button>
  );
}

function DetailPanel({
  detail,
  loading,
  onClose
}: {
  detail: LotDetail | null;
  loading: boolean;
  onClose: () => void;
}) {
  return (
    <aside className="detailPanel" aria-live="polite">
      <div className="detailHeader">
        <div>
          <span className="eyebrow">Карточка лота</span>
          <h2>{detail?.title || (loading ? "Загрузка" : "Выберите лот")}</h2>
        </div>
        <button className="iconButton" type="button" onClick={onClose} title="Закрыть">
          <X size={18} />
        </button>
      </div>
      {loading && (
        <div className="panelState">
          <Loader2 className="spin" size={22} />
          <span>Загрузка данных</span>
        </div>
      )}
      {!loading && !detail && (
        <div className="panelState">
          <FileSearch size={22} />
          <span>Откройте строку в таблице</span>
        </div>
      )}
      {!loading && detail && (
        <div className="detailContent">
          <div className="detailGrid">
            <div>
              <span>Текущая цена</span>
              <strong>{formatMoney(detail.current_price)}</strong>
            </div>
            <div>
              <span>Предварительная AI-гипотеза</span>
              <strong>{formatMoney(detail.market_price)}</strong>
            </div>
            <div>
              <span>Дисконт</span>
              <strong>{formatNumber(detail.discount_percent, "%")}</strong>
            </div>
            <div>
              <span>Риск</span>
              <strong>{formatNumber(detail.risk_score, "/10")}</strong>
            </div>
          </div>

          <section className="detailSection">
            <h3>Описание</h3>
            <p>{detail.description || "Описание отсутствует."}</p>
          </section>

          <section className="detailSection">
            <h3>Параметры</h3>
            <dl>
              <div><dt>Категория</dt><dd>{categoryLabel(detail.category)}</dd></div>
              <div><dt>Статус</dt><dd>{statusLabel(detail.auction_status)}</dd></div>
              <div><dt>Адрес</dt><dd>{detail.address || "—"}</dd></div>
              <div><dt>Кадастр</dt><dd>{detail.cadastral_number || "—"}</dd></div>
              <div><dt>Площадь здания</dt><dd>{formatNumber(detail.building_area, " м²")}</dd></div>
              <div><dt>Площадь земли</dt><dd>{formatNumber(detail.land_area, " м²")}</dd></div>
              <div><dt>Этажей</dt><dd>{formatNumber(detail.floors)}</dd></div>
            </dl>
          </section>

          <section className="detailSection">
            <h3>Предварительный AI-анализ</h3>
            <p>
              Не является независимой оценкой имущества. Требуется проверка оценщиком, юристом и техническим специалистом.
            </p>
            <p>{detail.ai_recommendation || "Предварительный AI-анализ еще не проводился."}</p>
          </section>

          {detail.geo && (
            <section className="detailSection">
              <h3>Геоданные</h3>
              <p>
                {detail.geo.source}, {detail.geo.confidence}: {detail.geo.centroid_lat.toFixed(6)},{" "}
                {detail.geo.centroid_lon.toFixed(6)}
              </p>
            </section>
          )}

          <div className="detailActions">
            {detail.lot_url && (
              <a href={detail.lot_url} target="_blank" rel="noreferrer">
                <ExternalLink size={16} />
                Источник
              </a>
            )}
          </div>
        </div>
      )}
    </aside>
  );
}

export function App() {
  const [query, setQuery] = React.useState<LotQuery>(initialQuery);
  const [lots, setLots] = React.useState<LotListItem[]>([]);
  const [stats, setStats] = React.useState<StatsResponse | null>(null);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [selectedId, setSelectedId] = React.useState<number | null>(null);
  const [detail, setDetail] = React.useState<LotDetail | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);

  const loadData = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [lotData, statsData] = await Promise.all([
        fetchLots(query),
        fetchStats(query.city_slug)
      ]);
      setLots(lotData.items);
      setTotal(lotData.total);
      setStats(statsData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить данные");
    } finally {
      setLoading(false);
    }
  }, [query]);

  React.useEffect(() => {
    const timer = window.setTimeout(loadData, 250);
    return () => window.clearTimeout(timer);
  }, [loadData]);

  const openDetail = async (id: number) => {
    setSelectedId(id);
    setDetail(null);
    setDetailLoading(true);
    try {
      setDetail(await fetchLotDetail(id, query.city_slug));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось открыть лот");
    } finally {
      setDetailLoading(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / query.per_page));

  return (
    <main className="appShell">
      <header className="topBar">
        <div>
          <span className="eyebrow">BankrotAI Web</span>
          <h1>Лоты и предварительный AI-анализ</h1>
        </div>
        <button className="primaryButton" type="button" onClick={loadData}>
          <RefreshCcw size={16} />
          Обновить
        </button>
      </header>

      <section className="kpiGrid">
        <Kpi icon={<Building2 size={20} />} label="Всего лотов" value={formatNumber(stats?.total_lots)} tone="teal" />
        <Kpi icon={<FileSearch size={20} />} label="Активные" value={formatNumber(stats?.active_lots)} tone="indigo" />
        <Kpi icon={<Sparkles size={20} />} label="С AI-анализом" value={formatNumber(stats?.appraised_lots)} tone="violet" />
        <Kpi icon={<Banknote size={20} />} label="Средний дисконт" value={formatNumber(stats?.average_discount, "%")} tone="amber" />
      </section>

      <section className="workspace">
        <aside className="filters">
          <div className="filterTitle">
            <Filter size={17} />
            <strong>Фильтры</strong>
          </div>

          <label className="field">
            <span>Регион</span>
            <select
              value={query.city_slug}
              onChange={(event) => setQuery({ ...query, city_slug: event.target.value, page: 1 })}
            >
              {REGION_OPTIONS.map((region) => (
                <option value={region.value} key={region.value}>{region.label}</option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Поиск</span>
            <div className="searchBox">
              <Search size={16} />
              <input
                value={query.search}
                onChange={(event) => setQuery({ ...query, search: event.target.value, page: 1 })}
                placeholder="Название, адрес, объект"
              />
            </div>
          </label>

          <label className="field">
            <span>Сортировка</span>
            <select
              value={query.sort}
              onChange={(event) => setQuery({ ...query, sort: event.target.value as SortMode, page: 1 })}
            >
              {SORT_OPTIONS.map((option) => (
                <option value={option.value} key={option.value}>{option.label}</option>
              ))}
            </select>
          </label>

          <div className="chipGroup">
            <span>Категории</span>
            <div>
              {CATEGORY_OPTIONS.map(([value, label]) => (
                <FilterChip
                  key={value}
                  label={label}
                  active={query.categories.includes(value)}
                  onClick={() => setQuery({ ...query, categories: toggleValue(query.categories, value), page: 1 })}
                />
              ))}
            </div>
          </div>

          <div className="chipGroup">
            <span>Статусы</span>
            <div>
              {STATUS_OPTIONS.map(([value, label]) => (
                <FilterChip
                  key={value}
                  label={label}
                  active={query.statuses.includes(value)}
                  onClick={() => setQuery({ ...query, statuses: toggleValue(query.statuses, value), page: 1 })}
                />
              ))}
            </div>
          </div>
        </aside>

        <section className="lotArea">
          <div className="lotToolbar">
            <div>
              <strong>{formatNumber(total)} найдено</strong>
              <span>Страница {query.page} из {totalPages}</span>
            </div>
            <div className="toolbarIcons">
              <ArrowDownAZ size={17} />
              <ArrowDownUp size={17} />
            </div>
          </div>

          {error && (
            <div className="errorBox">
              <AlertCircle size={18} />
              <span>{error}</span>
            </div>
          )}

          <div className="tableHead">
            <span>Лот</span>
            <span>Тип и статус</span>
            <span>Цена</span>
            <span>Дисконт</span>
            <span>Рейтинг</span>
          </div>

          <div className="lotList">
            {loading && (
              <div className="panelState">
                <Loader2 className="spin" size={22} />
                <span>Загрузка лотов</span>
              </div>
            )}
            {!loading && lots.length === 0 && (
              <div className="emptyState">
                <ShieldAlert size={24} />
                <strong>Лоты не найдены</strong>
                <span>Измените фильтры или регион.</span>
              </div>
            )}
            {!loading && lots.map((lot) => (
              <LotRow
                key={lot.id}
                lot={lot}
                selected={selectedId === lot.id}
                onOpen={() => openDetail(lot.id)}
              />
            ))}
          </div>

          <div className="pagination">
            <button
              type="button"
              disabled={query.page <= 1}
              onClick={() => setQuery({ ...query, page: Math.max(1, query.page - 1) })}
            >
              Назад
            </button>
            <span>{query.page} / {totalPages}</span>
            <button
              type="button"
              disabled={query.page >= totalPages}
              onClick={() => setQuery({ ...query, page: Math.min(totalPages, query.page + 1) })}
            >
              Вперед
            </button>
          </div>
        </section>

        <DetailPanel detail={detail} loading={detailLoading} onClose={() => {
          setSelectedId(null);
          setDetail(null);
        }} />
      </section>

      <footer className="statusLine">
        <MapPin size={15} />
        <span>API: {import.meta.env.VITE_API_BASE_URL || "same-origin /api"}</span>
      </footer>
    </main>
  );
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<App />);
}
