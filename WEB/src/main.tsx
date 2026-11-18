import React from "react";
import { createRoot } from "react-dom/client";
import {
  Activity, AlertCircle, Banknote, Bookmark, Building2, Calculator, CheckCircle2,
  ExternalLink, FileSearch, Files, Heart, ListFilter, Loader2, LogOut, Map,
  NotebookPen, RefreshCcw, Search, ShieldCheck, Sparkles, Star, X
} from "lucide-react";
import {
  addNote, ApiError, AuthUser, calculateMaxBid, compareDocuments, fetchCapabilities, fetchCurrentUser, fetchDiagnostics, fetchDocuments,
  fetchLotDetail, fetchLots, fetchMaxBidScenarios, fetchNotes, fetchParticipation, fetchProcedure, fetchRegions, fetchServerTime,
  fetchSavedSearches, fetchStats, fetchTBankrotAuthStatus, fetchWatchlist, importOnlineLot, login, logout, LotDetail, LotDocument, LotListItem, LotQuery, mergeLots,
  clearMapCache, fetchOperationsProgress, fetchReliabilitySources, fetchReliabilitySummary, MainView, MaxBidScenario, OnlineLot, OperationsProgress, Participation, pauseGeocoding, probeSourceHealth, Procedure, RegionOption, ReliabilitySource, ReliabilitySummary, resumeGeocoding, saveParticipation, searchOnline,
  saveSearch, SearchSource, SortMode, splitLot, StatsResponse, toggleWatchlist
} from "./lib/api";
import { MapView } from "./features/map/MapView";
import { TBankrotView } from "./features/tbankrot/TBankrotView";
import "./styles.css";

const CATEGORIES = [
  ["land", "Земля"], ["car", "Авто"], ["apartment", "Квартиры"], ["house", "Дома"],
  ["living", "Жилые помещения"], ["real_estate", "Недвижимость"],
  ["commercial_room", "Помещения"], ["commercial_building", "Здания"],
  ["commercial_building_with_land", "Здания с землёй"], ["receivable", "Дебиторка"], ["other", "Прочее"]
] as const;
const STATUSES = [["active", "Активные"], ["scheduled", "Запланированные"], ["unknown", "Без статуса"]] as const;
const initialQuery: LotQuery = {
  city_slug: "yaroslavl", page: 1, per_page: 18, search: "", categories: [],
  statuses: ["active", "scheduled"], min_discount: 0, max_discount: 100, sort: "recommended"
};

const sameValues = (left: string[], right: string[]) =>
  left.length === right.length && left.every((value) => right.includes(value));

export function registryActiveFilterCount(query: LotQuery, watchlistOnly = false) {
  return [
    watchlistOnly,
    query.city_slug !== initialQuery.city_slug,
    query.search.trim().length > 0,
    query.sort !== initialQuery.sort,
    query.min_price != null,
    query.max_price != null,
    query.min_risk != null,
    query.max_risk != null,
    !sameValues(query.categories, initialQuery.categories),
    !sameValues(query.statuses, initialQuery.statuses),
  ].filter(Boolean).length;
}

const money = (value?: number | null) => value == null ? "—" : new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 0 }).format(value);
const number = (value?: number | null, suffix = "") => value == null ? "—" : `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(value)}${suffix}`;
const label = (items: readonly (readonly [string, string])[], value: string) => items.find(([id]) => id === value)?.[1] || value;
const toggle = (items: string[], value: string) => items.includes(value) ? items.filter((item) => item !== value) : [...items, value];

function State({ children, error = false }: { children: React.ReactNode; error?: boolean }) {
  return <div className={error ? "errorBox" : "panelState"}>{error ? <AlertCircle size={18} /> : <Loader2 className="spin" size={20} />}<span>{children}</span></div>;
}

function DetailPanel({ detail, loading, onClose, onOpenDeal }: { detail: LotDetail | null; loading: boolean; onClose: () => void; onOpenDeal: (id: number) => void }) {
  const [message, setMessage] = React.useState("");
  const [note, setNote] = React.useState("");
  const [procedure, setProcedure] = React.useState<Procedure | null>(null);
  React.useEffect(() => { setMessage(""); setProcedure(null); if (detail) fetchProcedure(detail.id).then(setProcedure).catch(() => setProcedure(null)); }, [detail]);
  return <aside className="detailPanel">
    <div className="detailHeader"><div><span className="eyebrow">Карточка лота</span><h2>{detail?.title || "Выберите лот"}</h2>{detail && <div className="detailContext"><span>ID {detail.id}</span><span>{detail.source_system}</span><span>{detail.auction_status}</span></div>}</div><button className="iconButton" aria-label="Закрыть карточку" onClick={onClose}><X size={18} /></button></div>
    {loading && <State>Загрузка карточки</State>}
    {!loading && detail && <div className="detailContent">
      <div className="detailGrid"><div><span>Цена</span><strong>{money(detail.current_price)}</strong></div><div><span>Рыночная</span><strong>{money(detail.market_price)}</strong></div><div><span>Дисконт</span><strong>{number(detail.discount_percent, "%")}</strong></div><div><span>Риск</span><strong>{number(detail.risk_score, "/10")}</strong></div></div>
      <section className="detailSection"><h3>Описание</h3><p>{detail.description || "Описание отсутствует"}</p></section>
      <section className="detailSection"><h3>Объект</h3><dl>
        <div><dt>Источник</dt><dd>{detail.source_system}</dd></div><div><dt>Категория</dt><dd>{label(CATEGORIES, detail.category)}</dd></div>
        <div><dt>Адрес</dt><dd>{detail.address || "—"}</dd></div><div><dt>Кадастр</dt><dd>{detail.cadastral_number || "—"}</dd></div>
        <div><dt>Площадь</dt><dd>{number(detail.building_area || detail.area, " м²")}</dd></div><div><dt>Участок</dt><dd>{number(detail.land_area, " м²")}</dd></div>
      </dl></section>
      <section className="detailSection"><h3>AI-анализ</h3><p>{detail.ai_recommendation || "Оценка ещё не выполнена. Все расчёты требуют проверки специалистом."}</p></section>
      {procedure && <section className="detailSection"><h3>Процедура торгов</h3><dl>{Object.entries(procedure).filter(([, value]) => value != null && value !== "" && !Array.isArray(value)).slice(0, 12).map(([key, value]) => <div key={key}><dt>{key.split("_").join(" ")}</dt><dd>{String(value)}</dd></div>)}</dl></section>}
      {detail.geo && <section className="detailSection"><h3>Геоданные</h3><p>{detail.geo.source}, {detail.geo.confidence}: {detail.geo.centroid_lat.toFixed(6)}, {detail.geo.centroid_lon.toFixed(6)}</p></section>}
      <div className="detailActions">
        <button onClick={async () => { const result = await toggleWatchlist(detail.id); setMessage(result.watchlisted ? "Добавлено в избранное" : "Удалено из избранного"); }}><Heart size={16} /> Избранное</button>
        <button onClick={() => onOpenDeal(detail.id)}><Calculator size={16} /> Сделка</button>
        <button onClick={async () => { const secondary = Number(window.prompt("ID дублирующего лота")); if (!secondary) return; const reason = window.prompt("Основание объединения") || "Ручная проверка"; await mergeLots(detail.id, secondary, reason); setMessage(`Лоты ${detail.id} и ${secondary} объединены`); }}><Files size={16} /> Объединить дубль</button>
        {detail.duplicate_of_id && <button onClick={async () => { const reason = window.prompt("Основание разделения") || "Ручная проверка"; await splitLot(detail.id, reason); setMessage("Лот отделён от дубля"); }}><X size={16} /> Разделить</button>}
        {(detail.lot_url || detail.source_url) && <a href={detail.lot_url || detail.source_url || "#"} target="_blank" rel="noreferrer"><ExternalLink size={16} /> Источник</a>}
      </div>
      <div className="inlineForm"><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Добавить заметку" /><button onClick={async () => { if (!note.trim()) return; await addNote(detail.id, note); setNote(""); setMessage("Заметка сохранена"); }}><NotebookPen size={15} /></button></div>
      {message && <div className="successBox"><CheckCircle2 size={16} />{message}</div>}
    </div>}
  </aside>;
}

function RegistryView({ refreshToken, onOpenDeal }: { refreshToken: number; onOpenDeal: (id: number) => void }) {
  const [query, setQuery] = React.useState(initialQuery);
  const [lots, setLots] = React.useState<LotListItem[]>([]);
  const [stats, setStats] = React.useState<StatsResponse | null>(null);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [detail, setDetail] = React.useState<LotDetail | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [savedSearches, setSavedSearches] = React.useState<Array<{ id: number; name: string; query: LotQuery }>>([]);
  const [watchlistOnly, setWatchlistOnly] = React.useState(false);
  const activeFilterCount = registryActiveFilterCount(query, watchlistOnly);
  const resetFilters = React.useCallback(() => {
    setQuery({ ...initialQuery, categories: [...initialQuery.categories], statuses: [...initialQuery.statuses] });
    setWatchlistOnly(false);
  }, []);
  const load = React.useCallback(async () => {
    setLoading(true); setError(null);
    try { const [list, summary] = await Promise.all([watchlistOnly ? fetchWatchlist() : fetchLots(query), fetchStats(query.city_slug)]); setLots(list.items); setTotal(list.total); setStats(summary); }
    catch (err) { setError(err instanceof Error ? err.message : "Ошибка загрузки"); }
    finally { setLoading(false); }
  }, [query, watchlistOnly]);
  React.useEffect(() => { const id = window.setTimeout(load, 220); return () => window.clearTimeout(id); }, [load, refreshToken]);
  React.useEffect(() => { fetchSavedSearches().then(setSavedSearches).catch(() => setSavedSearches([])); }, []);
  const open = async (id: number) => { setDetailLoading(true); try { setDetail(await fetchLotDetail(id, query.city_slug)); } catch (err) { setError(String(err)); } finally { setDetailLoading(false); } };
  const pages = Math.max(1, Math.ceil(total / query.per_page));
  return <>
    <section className="kpiGrid"><Kpi icon={<Building2 />} label="Всего лотов" value={number(stats?.total_lots)} /><Kpi icon={<FileSearch />} label="Активные" value={number(stats?.active_lots)} /><Kpi icon={<Sparkles />} label="С AI-анализом" value={number(stats?.appraised_lots)} /><Kpi icon={<Banknote />} label="Средний дисконт" value={number(stats?.average_discount, "%")} /></section>
    <section className="workspace">
      <aside className="filters"><div className="filterTitle"><h3><ListFilter size={17} /> Фильтры{activeFilterCount > 0 && <span className="filterCount">{activeFilterCount}</span>}</h3>{activeFilterCount > 0 && <button className="filterResetButton" onClick={resetFilters}><X size={14} />Сбросить</button>}</div>
        <label className="field"><span>Регион реестра</span><select value={query.city_slug} onChange={(e) => setQuery({ ...query, city_slug: e.target.value, page: 1 })}><option value="yaroslavl">Ярославская область</option><option value="76">Регион 76</option><option value="84">Т‑Банкрот 84</option></select></label>
        <label className="field"><span>Поиск</span><div className="searchBox"><Search size={16} /><input value={query.search} onChange={(e) => setQuery({ ...query, search: e.target.value, page: 1 })} placeholder="Название, адрес, кадастр" />{query.search && <button type="button" className="searchClear" aria-label="Очистить поиск" onClick={() => setQuery({ ...query, search: "", page: 1 })}><X size={14} /></button>}</div></label>
        <label className="field"><span>Сортировка</span><select value={query.sort} onChange={(e) => setQuery({ ...query, sort: e.target.value as SortMode, page: 1 })}><option value="recommended">Рекомендации</option><option value="discount">Дисконт</option><option value="price_asc">Цена ↑</option><option value="price_desc">Цена ↓</option><option value="newest">Новые</option></select></label>
        <label className="field"><span>Сохранённые фильтры</span><select value="" onChange={(e) => { const saved = savedSearches.find((item) => item.id === Number(e.target.value)); if (saved) setQuery({ ...saved.query, page: 1 }); }}><option value="">Выберите фильтр</option>{savedSearches.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <button className="secondaryButton" onClick={async () => { const name = window.prompt("Название фильтра", `Поиск ${new Date().toLocaleDateString("ru-RU")}`); if (!name) return; await saveSearch(name, query); setSavedSearches(await fetchSavedSearches()); }}>Сохранить фильтр</button>
        <button className={`secondaryButton ${watchlistOnly ? "active" : ""}`} onClick={() => setWatchlistOnly((value) => !value)}><Heart size={15} />{watchlistOnly ? "Все лоты" : "Избранное"}</button>
        <div className="rangeGrid"><label className="field"><span>Цена от</span><input type="number" value={query.min_price ?? ""} onChange={(e) => setQuery({ ...query, min_price: e.target.value ? Number(e.target.value) : undefined, page: 1 })} /></label><label className="field"><span>Цена до</span><input type="number" value={query.max_price ?? ""} onChange={(e) => setQuery({ ...query, max_price: e.target.value ? Number(e.target.value) : undefined, page: 1 })} /></label><label className="field"><span>Риск от</span><input type="number" min="0" max="10" value={query.min_risk ?? ""} onChange={(e) => setQuery({ ...query, min_risk: e.target.value ? Number(e.target.value) : undefined, page: 1 })} /></label><label className="field"><span>Риск до</span><input type="number" min="0" max="10" value={query.max_risk ?? ""} onChange={(e) => setQuery({ ...query, max_risk: e.target.value ? Number(e.target.value) : undefined, page: 1 })} /></label></div>
        <div className="chipGroup"><span>Категории</span><div>{CATEGORIES.map(([id, text]) => <button key={id} className={`chip ${query.categories.includes(id) ? "active" : ""}`} onClick={() => setQuery({ ...query, categories: toggle(query.categories, id), page: 1 })}>{text}</button>)}</div></div>
        <div className="chipGroup"><span>Статусы</span><div>{STATUSES.map(([id, text]) => <button key={id} className={`chip ${query.statuses.includes(id) ? "active" : ""}`} onClick={() => setQuery({ ...query, statuses: toggle(query.statuses, id), page: 1 })}>{text}</button>)}</div></div>
      </aside>
      <section className="lotArea"><div className="lotToolbar"><div><strong>{total} найдено</strong><span>Страница {query.page} из {pages}</span></div>{loading && lots.length > 0 && <span className="refreshingBadge" role="status"><Loader2 className="spin" size={14} />Обновляем</span>}</div>{error && <State error>{error}</State>}
        <div className="tableHead"><span>Лот</span><span>Тип и статус</span><span>Цена</span><span>Дисконт</span><span>Рейтинг</span></div>
        <div className="lotList">{loading && lots.length === 0 && <State>Загрузка лотов</State>}{!loading && lots.length === 0 && !error && <div className="emptyState" role="status"><FileSearch size={20} /><strong>По заданным фильтрам лотов нет</strong><span>Измените условия поиска или сбросьте фильтры.</span>{activeFilterCount > 0 && <button className="secondaryButton" onClick={resetFilters}>Сбросить фильтры</button>}</div>}{lots.map((lot) => <button className={detail?.id === lot.id ? "lotRow selected" : "lotRow"} key={lot.id} onClick={() => open(lot.id)}><span className="lotMain"><strong>{lot.title}</strong><small>{lot.address || lot.description || "Адрес не указан"}</small></span><span className="lotMeta">{label(CATEGORIES, lot.category)}<small>{lot.auction_status}</small></span><b>{money(lot.current_price)}</b><b>{number(lot.discount_percent, "%")}</b><b>{number(lot.rating)}</b></button>)}</div>
        <div className="pagination"><button disabled={loading || query.page <= 1} onClick={() => setQuery({ ...query, page: query.page - 1 })}>Назад</button><span>{query.page} / {pages}</span><button disabled={loading || query.page >= pages} onClick={() => setQuery({ ...query, page: query.page + 1 })}>Вперёд</button></div>
      </section>
      <DetailPanel detail={detail} loading={detailLoading} onClose={() => setDetail(null)} onOpenDeal={onOpenDeal} />
    </section>
  </>;
}

function Kpi({ icon, label: text, value }: { icon: React.ReactNode; label: string; value: string }) { return <div className="kpi"><span className="kpiIcon">{icon}</span><div><span>{text}</span><strong>{value}</strong></div></div>; }

function SearchView({ refreshToken }: { refreshToken: number }) {
  const [source, setSource] = React.useState<SearchSource>("torgi-gov");
  const [form, setForm] = React.useState({ search: "", region: "", price_min: "", price_max: "", page: 1, page_size: 100, include_closed: false });
  const [regions, setRegions] = React.useState<RegionOption[]>([]); const [meta, setMeta] = React.useState<Record<string, unknown>>({});
  const [items, setItems] = React.useState<OnlineLot[]>([]); const [loading, setLoading] = React.useState(false); const [error, setError] = React.useState(""); const [imported, setImported] = React.useState<number[]>([]); const [searched, setSearched] = React.useState(false);
  React.useEffect(() => { fetchRegions().then(setRegions).catch(() => setRegions([])); }, []);
  const run = React.useCallback(async (requestedPage = form.page) => { setLoading(true); setSearched(true); setError(""); const request = { ...form, page: requestedPage, price_min: form.price_min || undefined, price_max: form.price_max || undefined }; try { const result = await searchOnline(source, request); setForm((value) => ({ ...value, page: requestedPage })); setItems(result.items); setMeta(result.meta); setImported([]); } catch (err) { setError(err instanceof Error ? err.message : "Ошибка источника"); } finally { setLoading(false); } }, [form, source]);
  React.useEffect(() => { if (refreshToken) void run(); }, [refreshToken]);
  const total = typeof meta.total === "number" ? meta.total : null; const totalPages = typeof meta.total_pages === "number" ? meta.total_pages : null; const hasMore = Boolean(meta.has_more) || Boolean(totalPages && form.page < totalPages);
  return <section className="pageCard" role="region" aria-label="Онлайн-поиск"><div className="sourceTabs">{(["torgi-gov", "tbankrot", "lot-online"] as SearchSource[]).map((id) => <button className={source === id ? "active" : ""} onClick={() => { setSource(id); setItems([]); setMeta({}); setError(""); setSearched(false); setForm((value) => ({ ...value, page: 1 })); }} key={id}>{id === "torgi-gov" ? "ГИС Торги" : id === "tbankrot" ? "Т‑Банкрот" : "РАД / ЛОТ‑ОНЛАЙН"}</button>)}</div>
    <datalist id="auction-regions">{regions.map((region) => <option key={region.code} value={region.name}>{region.code}</option>)}</datalist>
    <form className="searchForm" onSubmit={(event) => { event.preventDefault(); void run(1); }}><label className="field"><span>Поиск</span><input value={form.search} onChange={(e) => setForm({ ...form, search: e.target.value, page: 1 })} placeholder="Название, адрес, кадастровый номер" /></label><label className="field"><span>Регион онлайн-поиска</span><input list="auction-regions" value={form.region} onChange={(e) => setForm({ ...form, region: e.target.value, page: 1 })} placeholder="Выберите или введите 76 / Ярославская область" /></label><label className="field"><span>Категория</span><input value="Вся недвижимость" readOnly aria-readonly="true" /></label><label className="field"><span>Цена от</span><input type="number" value={form.price_min} onChange={(e) => setForm({ ...form, price_min: e.target.value, page: 1 })} /></label><label className="field"><span>Цена до</span><input type="number" value={form.price_max} onChange={(e) => setForm({ ...form, price_max: e.target.value, page: 1 })} /></label><label className="checkField"><input type="checkbox" checked={form.include_closed} onChange={(e) => setForm({ ...form, include_closed: e.target.checked, page: 1 })} />Архивные</label><button type="submit" className="primaryButton" disabled={loading}>{loading ? <Loader2 className="spin" size={16} /> : <Search size={16} />}{loading ? "Ищем…" : "Найти онлайн"}</button></form>
    {error && <State error>{error}</State>}{loading && items.length === 0 && <State>Запрос к {source}</State>}
    {items.length > 0 && <div className="searchSummary"><strong>{total == null ? `${items.length} на странице` : `${total} найдено источником`}</strong><span>{loading ? <><Loader2 className="spin" size={13} /> Обновляем результаты</> : <>Страница {form.page}{totalPages ? ` из ${totalPages}` : ""}</>}</span></div>}
    {searched && !loading && !error && items.length === 0 && <div className="emptyState" role="status"><Search size={20} /><strong>Ничего не найдено</strong><span>Попробуйте изменить запрос, регион или диапазон цены.</span></div>}
    <div className="onlineGrid">{items.map((lot, index) => <article className="onlineCard" key={`${lot.source_system}-${lot.external_id}`}><span className="sourceBadge">{lot.source_system}</span><h3>{lot.title}</h3><p>{lot.address || lot.description || "Без адреса"}</p><div><strong>{money(lot.current_price || lot.start_price)}</strong><span>{lot.auction_status}</span></div><div className="onlineActions"><button disabled={imported.includes(index)} onClick={async () => { try { await importOnlineLot(lot); setImported((values) => [...values, index]); } catch (err) { setError(String(err)); } }}>{imported.includes(index) ? "В реестре" : "Импортировать"}</button>{(lot.lot_url || lot.source_url) && <a href={lot.lot_url || lot.source_url || "#"} target="_blank" rel="noreferrer">Источник <ExternalLink size={14} /></a>}</div></article>)}</div>
    {items.length > 0 && <div className="pagination"><button disabled={loading || form.page <= 1} onClick={() => run(form.page - 1)}>Назад</button><span>{form.page}{totalPages ? ` / ${totalPages}` : ""}</span><button disabled={loading || !hasMore} onClick={() => run(form.page + 1)}>Вперёд</button></div>}
  </section>;
}

const checklistLabels: Array<[keyof Omit<Participation, "lot_id" | "source_lot_id" | "notes">, string]> = [["etp_accredited", "Аккредитация на ЭТП"], ["signature_valid", "ЭЦП действительна"], ["application_completed", "Заявка заполнена"], ["deposit_sent", "Задаток отправлен"], ["payment_purpose_verified", "Назначение платежа проверено"], ["deposit_received", "Задаток зачислен"], ["documents_signed", "Документы подписаны"], ["application_accepted", "Заявка принята"]];
function DealView({ selectedLotId }: { selectedLotId: number | null }) {
  const [lotId, setLotId] = React.useState(selectedLotId ? String(selectedLotId) : ""); const id = Number(lotId); const [tab, setTab] = React.useState("calculator"); const [error, setError] = React.useState("");
  const [calc, setCalc] = React.useState({ scenario_name: "Базовый сценарий", conservative_sale_price: "", repair_cost: "0", legal_cost: "0", monthly_holding_cost: "0", holding_months: "6", taxes: "0", sale_commission_percent: "3", target_profit: "0", risk_reserve: "0", annual_capital_cost_percent: "0", intended_bid: "" }); const [result, setResult] = React.useState<Record<string, Record<string, number>> | null>(null);
  const [participation, setParticipation] = React.useState<Participation | null>(null); const [notes, setNotes] = React.useState<Array<{ id: number; content: string; created_at: string }>>([]); const [docs, setDocs] = React.useState<LotDocument[]>([]); const [procedure, setProcedure] = React.useState<Procedure | null>(null); const [scenarios, setScenarios] = React.useState<MaxBidScenario[]>([]); const [comparison, setComparison] = React.useState<Record<string, unknown> | null>(null);
  React.useEffect(() => { if (selectedLotId) setLotId(String(selectedLotId)); }, [selectedLotId]);
  const loadTab = async (next: string) => { setTab(next); setError(""); if (!id) return; try { if (next === "calculator") setScenarios(await fetchMaxBidScenarios(id)); if (next === "participation") setParticipation(await fetchParticipation(id)); if (next === "notes") setNotes(await fetchNotes(id)); if (next === "documents") setDocs(await fetchDocuments(id)); if (next === "procedure") setProcedure(await fetchProcedure(id)); } catch (err) { setError(err instanceof Error ? err.message : "Ошибка"); } };
  return <section className="pageCard dealPage"><div className="dealHeader"><div><h2>Работа со сделкой</h2><p>Процедура, калькулятор, участие, документы и заметки</p></div><label className="field"><span>ID лота из реестра</span><div className="inlineForm"><input type="number" value={lotId} onChange={(e) => setLotId(e.target.value)} placeholder="Например, 125" /><button disabled={!id} onClick={() => loadTab(tab)}><RefreshCcw size={15} /></button></div></label></div><div className="sourceTabs"><button className={tab === "procedure" ? "active" : ""} onClick={() => loadTab("procedure")}><FileSearch size={15} />Процедура</button><button className={tab === "calculator" ? "active" : ""} onClick={() => loadTab("calculator")}><Calculator size={15} />Ставка</button><button className={tab === "participation" ? "active" : ""} onClick={() => loadTab("participation")}><ShieldCheck size={15} />Участие</button><button className={tab === "documents" ? "active" : ""} onClick={() => loadTab("documents")}><Files size={15} />Документы</button><button className={tab === "notes" ? "active" : ""} onClick={() => loadTab("notes")}><NotebookPen size={15} />Заметки</button></div>{error && <State error>{error}</State>}
    {tab === "procedure" && <div className="procedureGrid">{procedure ? Object.entries(procedure).filter(([, value]) => value != null && value !== "").map(([key, value]) => <div key={key}><span>{key.split("_").join(" ")}</span><strong>{Array.isArray(value) ? value.join(", ") : String(value)}</strong></div>) : <p>Укажите ID и загрузите процедуру.</p>}</div>}
    {tab === "calculator" && <div className="toolGrid"><div className="formCard">{Object.entries(calc).map(([key, value]) => <label className="field" key={key}><span>{key === "scenario_name" ? "Название сценария" : key.split("_").join(" ")}</span><input type={key === "scenario_name" ? "text" : "number"} value={value} onChange={(e) => setCalc({ ...calc, [key]: e.target.value })} /></label>)}<button className="primaryButton" disabled={!id || !calc.conservative_sale_price} onClick={async () => { try { const payload = Object.fromEntries(Object.entries(calc).map(([key, value]) => [key, key === "scenario_name" ? value : value === "" ? null : Number(value)])); const response = await calculateMaxBid(id, payload); setResult(response.scenarios); setScenarios(await fetchMaxBidScenarios(id)); } catch (err) { setError(String(err)); } }}>Рассчитать и сохранить</button></div><div className="resultCard"><h3>Максимальная ставка</h3>{result ? Object.entries(result).map(([name, values]) => <article key={name}><strong>{name}</strong><span>{money(values.max_bid)}</span><small>Затраты: {money(values.total_costs)} · Прибыль: {money(values.expected_profit)}</small></article>) : <p>Заполните консервативную цену продажи.</p>}<h3>Сохранённые сценарии</h3>{scenarios.map((scenario) => <article key={scenario.id}><strong>{scenario.name}</strong><small>{new Date(scenario.created_at).toLocaleString("ru-RU")}</small></article>)}</div></div>}
    {tab === "participation" && <div className="formCard checklist">{participation ? <>{checklistLabels.map(([key, text]) => <label key={key}><input type="checkbox" checked={participation[key]} onChange={(e) => setParticipation({ ...participation, [key]: e.target.checked })} />{text}</label>)}<textarea value={participation.notes || ""} onChange={(e) => setParticipation({ ...participation, notes: e.target.value })} placeholder="Комментарии" /><button className="primaryButton" onClick={async () => { const value = Object.fromEntries([...checklistLabels.map(([key]) => [key, participation[key]]), ["notes", participation.notes]]) as Omit<Participation, "lot_id" | "source_lot_id">; setParticipation(await saveParticipation(id, value)); }}>Сохранить контроль участия</button></> : <p>Укажите ID и откройте вкладку повторно.</p>}</div>}
    {tab === "documents" && <div className="documentList">{docs.length ? docs.map((doc) => <article key={doc.id}><Files /><div><strong>{doc.filename}</strong><span>{doc.versions.length} версий</span>{doc.versions.length >= 2 && <button className="secondaryButton" onClick={async () => setComparison(await compareDocuments(id, doc.versions[doc.versions.length - 1].id, doc.versions[0].id) as Record<string, unknown>)}>Сравнить последнюю с первой</button>}</div>{doc.source_url ? <a href={doc.source_url} target="_blank" rel="noreferrer"><ExternalLink /></a> : null}</article>) : <p>Документы для выбранного лота не загружены.</p>}{comparison && <pre className="diagnostics">{JSON.stringify(comparison, null, 2)}</pre>}</div>}
    {tab === "notes" && <div className="notesList">{notes.map((item) => <article key={item.id}><NotebookPen /><div><p>{item.content}</p><small>{new Date(item.created_at).toLocaleString("ru-RU")}</small></div></article>)}</div>}
  </section>;
}


const reliabilityLabels: Record<string, string> = {
  healthy: "Норма", success: "Успешно", completed: "Завершено", partial: "Частично",
  failed: "Ошибка", paused: "На паузе", fresh: "Актуально", delayed: "С задержкой",
  stale: "Устарело", missing: "Нет снимка", closed: "Соединение разрешено",
  open: "Соединение отключено", half_open: "Проверка восстановления",
  ready: "Подготовлена", running: "Выполняется", queued: "В очереди",
  actionable: "Требует обработки", deferred_no_match: "Соответствие не найдено",
  deferred_validation: "Не прошло проверку", network_wait: "Ожидание сети",
};
const reliabilityText = (value: string | null | undefined) =>
  value ? (reliabilityLabels[value] || "Неизвестно") : "Нет данных";
const reliabilityCount = (value: number | null | undefined) =>
  typeof value === "number" ? value.toLocaleString("ru-RU") : "—";
const reliabilityDate = (value: string | null | undefined) =>
  value ? new Date(value).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" }) + " МСК" : "Нет данных";
function ReliabilityView({ refreshToken, role }: { refreshToken: number; role: string }) {
  const [summary, setSummary] = React.useState<ReliabilitySummary | null>(null);
  const [sources, setSources] = React.useState<ReliabilitySource[]>([]);
  const [operations, setOperations] = React.useState<OperationsProgress | null>(null);
  const [backgroundJobs, setBackgroundJobs] = React.useState(false);
  const [error, setError] = React.useState("");
  const [action, setAction] = React.useState("");
  const [sourceFilter, setSourceFilter] = React.useState("all");
  const [sourceQuery, setSourceQuery] = React.useState("");
  const [expandedSources, setExpandedSources] = React.useState<string[]>([]);

  const load = React.useCallback(async () => {
    const results = await Promise.allSettled([
      fetchReliabilitySummary(),
      fetchReliabilitySources(),
      fetchOperationsProgress(),
      fetchCapabilities(),
    ]);
    if (results[0].status === "fulfilled") setSummary(results[0].value);
    if (results[1].status === "fulfilled") setSources(results[1].value.items);
    if (results[2].status === "fulfilled") setOperations(results[2].value);
    if (results[3].status === "fulfilled") setBackgroundJobs(Boolean(results[3].value.background_jobs));
    const failures = results.filter((item) => item.status === "rejected");
    setError(failures.length ? `Не удалось обновить ${failures.length} из 4 блоков. Последние успешные данные сохранены.` : "");
  }, []);

  React.useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load, refreshToken]);

  const geo = summary?.geo;
  const map = summary?.map;
  const host = summary?.host;
  const sourceCounts = summary?.sources;
  const quality = summary?.data_quality;
  const integrity = summary?.integrity;
  const systemStatus = summary?.system.status || "unknown";
  const systemTitle = systemStatus === "ok" ? "Система работает стабильно"
    : systemStatus === "incident" ? "Требуется вмешательство"
    : systemStatus === "degraded" ? "Система работает с ограничениями" : "Нет свежих данных";
  const systemTone = systemStatus === "ok" ? "ok" : systemStatus === "incident" ? "bad" : "";
  const diskFree = host?.disk?.free_gb;
  const configuredTotal = sourceCounts?.configured_total || 0;
  const stableSources = sourceCounts?.healthy || 0;
  const degradedSources = sourceCounts?.degraded || 0;
  const failedSources = sourceCounts?.failed || 0;
  const pausedSources = sourceCounts?.paused_total || 0;
  const stablePercent = configuredTotal ? Math.round(stableSources / configuredTotal * 100) : 0;
  const degradedPercent = configuredTotal ? Math.round(degradedSources / configuredTotal * 100) : 0;
  const failedPercent = configuredTotal ? Math.round(failedSources / configuredTotal * 100) : 0;
  const donutBackground = `conic-gradient(#21a35b 0 ${stablePercent}%, #f2b31b ${stablePercent}% ${stablePercent + degradedPercent}%, #ef4444 ${stablePercent + degradedPercent}% ${stablePercent + degradedPercent + failedPercent}%, #d9e0e7 ${stablePercent + degradedPercent + failedPercent}% 100%)`;

  const attentionSources = sources.filter((item) => item.configured && item.overall_status !== "healthy");
  const filteredSources = sources.filter((item) => {
    if (sourceFilter === "problems" && item.overall_status === "healthy") return false;
    if (sourceFilter === "healthy" && item.overall_status !== "healthy") return false;
    if (sourceFilter === "configured" && !item.configured) return false;
    return [item.display_name_ru, item.canonical_id, ...item.aliases].join(" ").toLowerCase().includes(sourceQuery.trim().toLowerCase());
  });

  const runAction = async (label: string, operation: () => Promise<unknown>) => {
    setAction(label);
    try { await operation(); await load(); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setAction(""); }
  };
  const downloadDiagnostics = async () => {
    try {
      const data = await fetchDiagnostics();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "bankrotai-diagnostics.json"; anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  };
  const qualityPercent = (key: string) => quality?.percentages?.[key] != null ? `${quality.percentages[key].toFixed(1)}%` : "—";

  return <section className="operationsDashboard reliabilityDashboard">
    <section className="pageCard reliabilityHero">
      <div className={`reliabilitySystemStatus ${systemTone}`}>
        <ShieldCheck size={28} />
        <div><h2>{systemTitle}</h2><p>{summary?.system.reasons[0] || "Критических замечаний по доступным проверкам нет."}</p></div>
      </div>
      <div className="reliabilityHeroKpis">
        <div><span>Источники</span><strong>{stableSources} / {configuredTotal || "—"}</strong><small>стабильны сейчас</small></div>
        <div><span>GEO</span><strong>{geo ? `${geo.percent.toFixed(1)}%` : "—"}</strong><small>{reliabilityCount(geo?.geocoded)} с координатами</small></div>
        <div><span>Карта</span><strong>{reliabilityCount(map?.point_count)}</strong><small>{map?.ok ? "набор данных согласован" : "требует проверки"}</small></div>
        <div><span>Актуальные лоты</span><strong>{reliabilityCount(quality?.population)}</strong><small>рабочая выборка</small></div>
      </div>
      <div className="reliabilityHeroMeta">
        <span>Production</span>
        <small>Обновлено: {reliabilityDate(summary?.generated_at)}</small>
        {summary?.system.deployment_sha && <small>Версия: {summary.system.deployment_sha.slice(0, 8)}</small>}
      </div>
    </section>

    <section className="reliabilityTopGrid">
      <div className="pageCard">
        <h2>Общая надёжность источников</h2>
        <div className="reliabilitySourceOverview">
          <div className="reliabilityDonut" style={{ background: donutBackground }}><div><strong>{stablePercent}%</strong><span>стабильны</span></div></div>
          <div className="reliabilityLegend">
            <p><i className="dot ok" /><span>Работают стабильно</span><strong>{stableSources}</strong></p>
            <p><i className="dot warn" /><span>Требуют внимания</span><strong>{degradedSources}</strong></p>
            <p><i className="dot bad" /><span>С ошибками</span><strong>{failedSources}</strong></p>
            <p><i className="dot muted" /><span>На паузе / без проверки</span><strong>{pausedSources + (sourceCounts?.not_checked || 0)}</strong></p>
            <small>Настроенных источников: {configuredTotal}</small>
          </div>
        </div>
      </div>

      <div className="pageCard">
        <div className="reliabilitySectionTitle"><h2>Актуальные проблемы <span>({attentionSources.length + (integrity?.critical_count || 0)})</span></h2><small>Сначала критичные</small></div>
        <div className="reliabilityProblemList">
          {integrity?.critical_count ? <article><span className="healthBadge bad">Критично</span><div><strong>Целостность карты / набора данных</strong><small>Есть расхождения, требующие проверки перед публикацией.</small></div></article> : null}
          {attentionSources.slice(0, 6).map((item) => <article key={item.canonical_id}>
            <span className={`healthBadge ${item.overall_status === "failed" ? "bad" : ""}`}>{item.overall_status === "failed" ? "Высокий" : "Средний"}</span>
            <div><strong>{item.display_name_ru}</strong><small>{item.current_error || `Данные: ${reliabilityText(item.freshness_status)} · снимок: ${reliabilityText(item.coverage_status)}`}</small></div>
          </article>)}
          {!attentionSources.length && !integrity?.critical_count && <p className="reliabilityEmpty">Актуальных проблем по настроенным источникам не обнаружено.</p>}
        </div>
      </div>
    </section>

    <section className="pageCard">
      <div className="reliabilitySectionTitle"><div><h2>Источники данных ({configuredTotal})</h2><small>Настроенные, подключённые и исторические источники разделены backend-моделью.</small></div></div>
      <div className="reliabilityFilters">
        <div className="reliabilityTabs">
          <button className={sourceFilter === "all" ? "active" : ""} onClick={() => setSourceFilter("all")}>Все <b>{sources.length}</b></button>
          <button className={sourceFilter === "configured" ? "active" : ""} onClick={() => setSourceFilter("configured")}>Настроенные <b>{configuredTotal}</b></button>
          <button className={sourceFilter === "healthy" ? "active" : ""} onClick={() => setSourceFilter("healthy")}>Стабильные <b>{stableSources}</b></button>
          <button className={sourceFilter === "problems" ? "active" : ""} onClick={() => setSourceFilter("problems")}>С проблемами <b>{attentionSources.length}</b></button>
        </div>
        <label className="reliabilitySearch"><Search size={15} /><input value={sourceQuery} onChange={(e) => setSourceQuery(e.target.value)} placeholder="Поиск по источнику…" /></label>
      </div>
      <div className="reliabilitySourceTable" role="table" aria-label="Состояние источников">
        <div className="reliabilitySourceRow reliabilitySourceHead" role="row">
          <span>Источник</span><span>Статус</span><span>Актуальность</span><span>Полный снимок</span><span>Записей</span><span>Последний успех</span><span>Сообщение</span>
        </div>
        {filteredSources.map((source) => {
          const expanded = expandedSources.includes(source.canonical_id);
          return <React.Fragment key={source.canonical_id}>
            <button className="reliabilitySourceRow reliabilitySourceButton" role="row" aria-expanded={expanded} onClick={() => setExpandedSources((prev) => expanded ? prev.filter((id) => id !== source.canonical_id) : [...prev, source.canonical_id])}>
              <span><strong>{source.display_name_ru}</strong><small>{source.canonical_id}</small></span>
              <span><em className={`statusPill ${source.overall_status}`}>{source.overall_status === "healthy" ? "Стабильный" : source.overall_status === "failed" ? "Ошибка" : source.overall_status === "paused" ? "На паузе" : source.overall_status === "not_checked" ? "Не проверен" : "Частичный"}</em></span>
              <span>{reliabilityText(source.freshness_status)}</span>
              <span>{reliabilityText(source.coverage_status)}</span>
              <span>{reliabilityCount(source.items_seen)}</span>
              <span>{reliabilityDate(source.last_success_at)}</span>
              <span className="reliabilitySourceMessage">{source.current_error || (source.retry_overdue ? "Повтор просрочен" : "—")}</span>
            </button>
            {expanded && <div className="reliabilitySourceExpanded">
              <span>Соединение: {reliabilityText(source.circuit_state)}</span>
              <span>Добавлено: {reliabilityCount(source.last_items_inserted)}</span>
              <span>Обновлено: {reliabilityCount(source.last_items_updated)}</span>
              <span>Архивировано: {reliabilityCount(source.last_items_archived)}</span>
              {source.aliases.length > 1 && <span>Исторические имена: {source.aliases.join(", ")}</span>}
              {source.last_historical_error && <span>Последняя историческая ошибка: {source.last_historical_error.slice(0, 300)}</span>}
              {role === "admin" && backgroundJobs && source.registered && <button className="secondaryButton" disabled={Boolean(action)} onClick={() => runAction(`probe:${source.canonical_id}`, () => probeSourceHealth(source.canonical_id))}>Проверить источник</button>}
            </div>}
          </React.Fragment>;
        })}
      </div>
    </section>

    <section className="reliabilityBottomGrid">
      <div className="pageCard">
        <h2>GEO · координаты</h2>
        <div className="reliabilityGeoHeadline"><strong>{reliabilityCount(geo?.geocoded)}</strong><span>{geo ? `${geo.percent.toFixed(1)}% покрытия` : "—"}</span></div>
        <div className="reliabilityProgress"><i style={{ width: `${geo?.percent || 0}%` }} /></div>
        <dl className="reliabilityStatsList">
          <div><dt>Требуют обработки</dt><dd>{reliabilityCount(geo?.actionable_remaining)}</dd></div>
          <div><dt>Соответствие не найдено</dt><dd>{reliabilityCount(geo?.deferred_no_match)}</dd></div>
          <div><dt>Не прошло проверку</dt><dd>{reliabilityCount(geo?.deferred_validation)}</dd></div>
          <div><dt>Ожидание сети</dt><dd>{reliabilityCount(geo?.network_wait)}</dd></div>
        </dl>
      </div>

      <div className="pageCard">
        <h2>Полнота и качество данных</h2>
        <div className="reliabilityQualityHeadline"><span>Актуальных лотов</span><strong>{reliabilityCount(quality?.population)}</strong></div>
        <dl className="reliabilityStatsList">
          <div><dt>Нет адреса</dt><dd>{reliabilityCount(quality?.missing_address)} <small>{qualityPercent("missing_address")}</small></dd></div>
          <div><dt>Нет кадастра</dt><dd>{reliabilityCount(quality?.missing_cadastre)} <small>{qualityPercent("missing_cadastre")}</small></dd></div>
          <div><dt>Нет цены</dt><dd>{reliabilityCount(quality?.missing_price)} <small>{qualityPercent("missing_price")}</small></dd></div>
          <div><dt>Неизвестный статус</dt><dd>{reliabilityCount(quality?.unknown_status)} <small>{qualityPercent("unknown_status")}</small></dd></div>
          <div><dt>Проблемы целостности</dt><dd>{reliabilityCount((integrity?.critical_count || 0) + (integrity?.warning_count || 0))}</dd></div>
        </dl>
      </div>

      <div className="pageCard">
        <h2>Инфраструктура</h2>
        <dl className="reliabilityInfra">
          <div><dt>Диск C:</dt><dd><CheckCircle2 size={16} />{typeof diskFree === "number" ? `${diskFree.toFixed(1)} ГБ` : "Нет данных"}</dd></div>
          <div><dt>Резервная копия</dt><dd className={host?.backup_healthy ? "" : "warn"}>{host?.backup_healthy ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}{host?.backup_healthy ? "Проверена" : "Требует внимания"}{host?.backup_age_hours != null ? ` · ${host.backup_age_hours.toFixed(1)} ч назад` : ""}</dd></div>
          <div><dt>Диагностика Runner</dt><dd className={host?.runner_healthy ? "" : "warn"}>{host?.runner_healthy ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}{host?.runner_healthy ? "Актуальна" : "Нет свежей проверки"}</dd></div>
          <div><dt>Карта</dt><dd className={map?.ok ? "" : "warn"}>{map?.ok ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}{map?.ok ? "Набор согласован" : "Требует внимания"}</dd></div>
        </dl>
        <div className="operationsActions reliabilityInfraActions">
          <button className="secondaryButton" onClick={() => void downloadDiagnostics()}>Скачать диагностику</button>
          {role === "admin" && backgroundJobs && <>
            <button className="secondaryButton" disabled={Boolean(action) || geo?.paused === true} onClick={() => runAction("pause", pauseGeocoding)}>Пауза GEO</button>
            <button className="secondaryButton" disabled={Boolean(action) || geo?.paused !== true} onClick={() => runAction("resume", resumeGeocoding)}>Возобновить GEO</button>
          </>}
        </div>
      </div>
    </section>

    <section className="pageCard">
      <div className="reliabilitySectionTitle"><h2>Журнал операций</h2><small>Последние синхронизации и GEO-задачи</small></div>
      <div className="operationsJournal">{operations?.journal?.map((item, index) => <article key={`${item.kind}-${item.title}-${index}`}><span className={item.status === "completed" || item.status === "success" ? "healthBadge ok" : "healthBadge"}>{reliabilityText(item.status)}</span><div><strong>{item.title}</strong><small>{item.detail}</small>{item.at && <small>{reliabilityDate(item.at)}</small>}</div></article>)}</div>
      {error && <State error>{error}</State>}
    </section>
  </section>;
}

const nav: Array<[MainView, string, React.ReactNode]> = [["search", "Поиск", <Search />], ["registry", "Реестр", <Bookmark />], ["map", "Карта", <Map />], ["deal", "Сделка", <Calculator />], ["tbankrot", "TBankrot", <ShieldCheck />], ["reliability", "Надёжность", <Activity />]];
function ServerClock() {
  const [anchor, setAnchor] = React.useState<{ server: number; local: number; synchronized: boolean } | null>(null);
  const [tick, setTick] = React.useState(Date.now());
  React.useEffect(() => { let active = true; const sync = async () => { try { const value = await fetchServerTime(); if (active) setAnchor({ server: Date.parse(value.moscow), local: Date.now(), synchronized: value.synchronized }); } catch { /* retain the last verified value */ } }; void sync(); const syncTimer = window.setInterval(sync, 300_000); const tickTimer = window.setInterval(() => setTick(Date.now()), 1_000); return () => { active = false; clearInterval(syncTimer); clearInterval(tickTimer); }; }, []);
  const value = anchor ? new Date(anchor.server + tick - anchor.local).toLocaleString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "синхронизация…";
  return <div className="globalClock" aria-label="Текущее московское время"><span>Москва · {value}</span><i className={anchor?.synchronized ? "online" : "fallback"} title={anchor?.synchronized ? "Время проверено по онлайн-источнику" : "Используются системные часы"} /></div>;
}

export function App({ username = "Пользователь", role = "reader", onLogout = () => undefined }: { username?: string; role?: string; onLogout?: () => void }) {
  const [view, setView] = React.useState<MainView>("map"); const [refreshToken, setRefreshToken] = React.useState(0); const [selectedLotId, setSelectedLotId] = React.useState<number | null>(null); const [mapFavorites, setMapFavorites] = React.useState(false); const [favoriteCount, setFavoriteCount] = React.useState(0); const [mapVisited, setMapVisited] = React.useState(true);
  const [tbankrotNeedsAuth, setTbankrotNeedsAuth] = React.useState(false);
  React.useEffect(() => {
    if (role !== "admin") return;
    let cancelled = false;
    const check = () => fetchTBankrotAuthStatus()
      .then((value) => { if (!cancelled) setTbankrotNeedsAuth(value.requires_auth); })
      .catch(() => undefined);
    void check();
    const timer = window.setInterval(check, 30_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [role, refreshToken]);
  const openDeal = (id: number) => { setSelectedLotId(id); setView("deal"); };
  const openView = (next: MainView) => { setMapFavorites(false); if (next === "map") setMapVisited(true); setView(next); };
  const visibleNav = nav.filter(([id]) => id !== "tbankrot" || role === "admin");
  return <main className="appShell">
    <nav className="appRail" aria-label="Основная навигация">
      <button className="appRailLogo" title="BankrotAI" aria-label="BankrotAI"><Building2 /></button>
      {visibleNav.map(([id, text, icon]) => <button key={id} title={id === "tbankrot" && tbankrotNeedsAuth ? "TBankrot — требуется авторизация" : text} aria-label={text} className={view === id && !mapFavorites ? "active" : ""} onClick={() => openView(id)}>{icon}{id === "tbankrot" && tbankrotNeedsAuth && <span className="sourceAuthAlert" aria-label="Требуется авторизация" />}</button>)}
      <button title="Интересные лоты" aria-label={`Интересные лоты: ${favoriteCount}`} className={mapFavorites ? "active favorite" : "favorite"} onClick={() => { setMapVisited(true); setView("map"); setMapFavorites(true); }}><Star />{favoriteCount > 0 && <span>{favoriteCount}</span>}</button>
      <button title="Обновить данные" aria-label="Обновить данные" onClick={() => setRefreshToken((value) => value + 1)}><RefreshCcw /></button>
      <button className="appRailLogout" title={`Выйти: ${username}`} aria-label={`Выйти: ${username}`} onClick={onLogout}><LogOut /></button>
    </nav>
    <section className="appWorkspace">
      {view !== "map" && <header className="pageHeader"><div><span className="eyebrow">BankrotAI Web</span><h1>{visibleNav.find(([id]) => id === view)?.[1]}</h1></div><button className="primaryButton" onClick={() => setRefreshToken((value) => value + 1)}><RefreshCcw size={16} />Обновить</button></header>}
      {view !== "map" && <div className="viewContainer">{view === "search" && <SearchView refreshToken={refreshToken} />}{view === "registry" && <RegistryView refreshToken={refreshToken} onOpenDeal={openDeal} />}{view === "deal" && <DealView selectedLotId={selectedLotId} />}{view === "tbankrot" && <TBankrotView refreshToken={refreshToken} />}{view === "reliability" && <ReliabilityView refreshToken={refreshToken} role={role} />}</div>}
      <div className={view === "map" ? "mapPersistentHost active" : "mapPersistentHost"} aria-hidden={view !== "map"} inert={view !== "map" ? true : undefined}>{mapVisited && <MapView refreshToken={refreshToken} favoritesOnly={mapFavorites} active={view === "map"} onFavoriteCount={setFavoriteCount} statusContent={view === "map" ? <ServerClock /> : undefined} />}</div>
      {view !== "map" && <ServerClock />}
    </section>
  </main>;
}

export function AuthenticatedApp() {
  const [user, setUser] = React.useState<AuthUser | null>(null); const [checking, setChecking] = React.useState(true); const [startupError, setStartupError] = React.useState(""); const [username, setUsername] = React.useState(""); const [password, setPassword] = React.useState(""); const [error, setError] = React.useState("");
  const checkSession = React.useCallback(async () => {
    setChecking(true);
    setStartupError("");
    try {
      setUser(await fetchCurrentUser());
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setUser(null);
      } else {
        setStartupError(err instanceof Error ? err.message : "Сервис временно недоступен");
      }
    } finally {
      setChecking(false);
    }
  }, []);
  React.useEffect(() => { void checkSession(); }, [checkSession]);
  if (checking) return <main className="authScreen"><Loader2 className="spin" /></main>;
  if (startupError) return <main className="authScreen"><section className="authCard" role="alert"><span className="eyebrow">BankrotAI Web</span><h1>Нет связи с сервисом</h1><State error>{startupError}</State><button className="primaryButton" onClick={() => void checkSession()}><RefreshCcw size={16} />Повторить</button></section></main>;
  if (!user) return <main className="authScreen"><form className="authCard" onSubmit={async (event) => { event.preventDefault(); setError(""); try { setUser(await login(username, password)); setPassword(""); } catch (err) { setError(err instanceof Error ? err.message : "Ошибка авторизации"); } }}><span className="eyebrow">BankrotAI Web</span><h1>Вход</h1><label className="field"><span>Логин</span><input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} /></label><label className="field"><span>Пароль</span><input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>{error && <State error>{error}</State>}<button className="primaryButton">Войти</button></form></main>;
  return <App username={user.username} role={user.role} onLogout={async () => { await logout(); await clearMapCache(); setUser(null); }} />;
}

const root = document.getElementById("root"); if (root) createRoot(root).render(<AuthenticatedApp />);
