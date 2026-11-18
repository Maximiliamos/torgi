import React from "react";
import { createRoot } from "react-dom/client";
import {
  Activity, AlertCircle, AlertTriangle, Banknote, Bookmark, Building2, Calculator, CheckCircle2,
  ChevronDown, Copy, Database, Download, ExternalLink, FileSearch, Files, HardDrive, Heart,
  ListFilter, Loader2, LogOut, Map, MapPinned, NotebookPen, RefreshCcw, Search, Server,
  ShieldCheck, Sparkles, Star, Wifi, X
} from "lucide-react";
import {
  addNote, ApiError, AuthUser, calculateMaxBid, compareDocuments, fetchCapabilities, fetchCurrentUser, fetchDiagnostics, fetchDocuments,
  fetchLotDetail, fetchLots, fetchMaxBidScenarios, fetchNotes, fetchParticipation, fetchProcedure, fetchRegions, fetchReliabilitySources,
  fetchReliabilitySummary, fetchSavedSearches, fetchServerTime, fetchStats, fetchTBankrotAuthStatus, fetchWatchlist, importOnlineLot,
  login, logout, LotDetail, LotDocument, LotListItem, LotQuery, mergeLots, clearMapCache, MainView, MaxBidScenario, OnlineLot,
  Participation, pauseGeocoding, probeSourceHealth, Procedure, RegionOption, ReliabilitySource, ReliabilitySummary, resumeGeocoding,
  saveParticipation, searchOnline, saveSearch, SearchSource, SortMode, splitLot, StatsResponse, toggleWatchlist
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


const RELIABILITY_STATUS_LABELS: Record<string, string> = {
  healthy: "Норма",
  degraded: "Требует внимания",
  failed: "Ошибка",
  paused: "На паузе",
  not_checked: "Не проверен",
  fresh: "Актуально",
  delayed: "Есть задержка",
  stale: "Устарело",
  missing: "Полного снимка нет",
  partial: "Частично выполнено",
  success: "Успешно",
  running: "Выполняется",
  queued: "В очереди",
  closed: "Соединение разрешено",
  open: "Временно отключён",
  half_open: "Проверка восстановления",
};

const RELIABILITY_ERROR_LABELS: Record<string, string> = {
  timeout: "Тайм-аут",
  upstream_network: "Ошибка сети источника",
  unknown_upstream: "Ошибка источника",
  upstream_error: "Ошибка источника",
  validation: "Ошибка проверки данных",
  parser_contract: "Изменился формат источника",
  coverage_guard: "Неполный охват источника",
  authentication: "Требуется авторизация",
  rate_limit: "Лимит запросов",
  http_429: "Лимит запросов",
  access_limited: "Доступ ограничен",
  database_integrity: "Ошибка целостности БД",
  internal_error: "Внутренняя ошибка",
  tls: "Ошибка защищённого соединения",
};

const RELIABILITY_JOURNAL_LABELS: Record<string, string> = {
  sources: "Источники",
  geo: "GEO",
  map: "Карта",
  infrastructure: "Инфраструктура",
  system: "Система",
};

const reliabilityNumber = (value?: number | null) =>
  value == null ? "—" : new Intl.NumberFormat("ru-RU").format(value);

const reliabilityPercent = (value?: number | null) =>
  value == null ? "—" : `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(value)}%`;

function reliabilityAge(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return "нет данных";
  const value = Math.max(0, seconds);
  if (value < 60) return `${Math.round(value)} сек назад`;
  if (value < 3600) return `${Math.round(value / 60)} мин назад`;
  if (value < 86_400) {
    const hours = value / 3600;
    return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(hours)} ч назад`;
  }
  const days = value / 86_400;
  return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(days)} дн. назад`;
}

function reliabilityDateAge(value?: string | null) {
  if (!value) return "нет данных";
  const stamp = Date.parse(value);
  if (!Number.isFinite(stamp)) return "нет данных";
  return reliabilityAge((Date.now() - stamp) / 1000);
}

function reliabilitySourceTone(status: string) {
  if (status === "healthy") return "ok";
  if (status === "failed") return "bad";
  if (status === "degraded") return "warn";
  return "neutral";
}

function reliabilitySystemTone(status?: string) {
  if (status === "ok") return "ok";
  if (status === "incident") return "bad";
  return "warn";
}

function ReliabilityView({ refreshToken, role }: { refreshToken: number; role: string }) {
  const [summary, setSummary] = React.useState<ReliabilitySummary | null>(null);
  const [sources, setSources] = React.useState<ReliabilitySource[]>([]);
  const [backgroundJobs, setBackgroundJobs] = React.useState(false);
  const [error, setError] = React.useState("");
  const [action, setAction] = React.useState("");
  const [sourceFilter, setSourceFilter] = React.useState<"attention" | "all" | "healthy" | "paused" | "pilot">("attention");
  const [sourceSearch, setSourceSearch] = React.useState("");
  const [journalFilter, setJournalFilter] = React.useState<"all" | "errors" | "sources" | "geo" | "map" | "infrastructure">("all");
  const [notice, setNotice] = React.useState("");

  const load = React.useCallback(async () => {
    try {
      const [nextSummary, nextSources, capabilities] = await Promise.all([
        fetchReliabilitySummary(),
        fetchReliabilitySources(),
        fetchCapabilities(),
      ]);
      setSummary(nextSummary);
      setSources(nextSources);
      setBackgroundJobs(Boolean(capabilities.background_jobs));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  React.useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load, refreshToken]);

  const runAction = async (label: string, operation: () => Promise<unknown>) => {
    setAction(label);
    setNotice("");
    try {
      await operation();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setAction("");
    }
  };

  const downloadDiagnostics = async () => {
    setAction("diagnostics-download");
    try {
      const payload = await fetchDiagnostics();
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `bankrotai-diagnostics-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setNotice("Диагностика скачана");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setAction("");
    }
  };

  const copyDiagnostics = async () => {
    setAction("diagnostics-copy");
    try {
      const payload = await fetchDiagnostics();
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      setNotice("Диагностика скопирована в буфер обмена");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setAction("");
    }
  };

  const filteredSources = React.useMemo(() => {
    const query = sourceSearch.trim().toLocaleLowerCase("ru-RU");
    return sources.filter((source) => {
      const matchesSearch = !query || [
        source.display_name_ru,
        source.canonical_id,
        ...source.aliases,
      ].some((value) => value.toLocaleLowerCase("ru-RU").includes(query));
      if (!matchesSearch) return false;
      if (sourceFilter === "healthy") return source.overall_status === "healthy";
      if (sourceFilter === "paused") return source.overall_status === "paused";
      if (sourceFilter === "pilot") return source.role === "pilot";
      if (sourceFilter === "attention") return !["healthy", "paused"].includes(source.overall_status);
      return true;
    });
  }, [sourceFilter, sourceSearch, sources]);

  const filteredJournal = React.useMemo(() => {
    const rows = summary?.journal || [];
    return rows.filter((item) => {
      if (journalFilter === "all") return true;
      if (journalFilter === "errors") return ["error", "critical"].includes(item.severity.toLocaleLowerCase("ru-RU"));
      return item.category === journalFilter;
    });
  }, [journalFilter, summary?.journal]);

  const reasonTargets: Record<string, string> = {
    sources: "reliability-sources",
    coverage: "reliability-sources",
    geo_paused: "reliability-geo",
    geo_tail: "reliability-geo",
    map_delivery: "reliability-map",
    map_unverified: "reliability-map",
    database_integrity: "reliability-integrity",
    backup: "reliability-infrastructure",
    disk: "reliability-infrastructure",
    disk_warning: "reliability-infrastructure",
    runner_diagnostic: "reliability-infrastructure",
  };

  if (!summary && !error) return <section className="reliabilityPage"><State>Загрузка состояния системы</State></section>;

  const systemTone = reliabilitySystemTone(summary?.system.status);
  const sourceSummary = summary?.sources;
  const geo = summary?.geo;
  const map = summary?.map;
  const host = summary?.host;
  const dataQuality = summary?.data_quality;
  const integrity = summary?.integrity;
  const infrastructureDegraded = Boolean(
    host && (
      host.disk.status !== "ok"
      || !host.backup.healthy
      || !host.runner_diagnostic.fresh
      || host.api.status !== "ok"
      || host.database.status !== "ok"
    )
  );

  const qualityMetrics = dataQuality ? [
    ["Без адреса", dataQuality.missing_address],
    ["Без кадастрового номера", dataQuality.missing_cadastre],
    ["Без цены", dataQuality.missing_price],
    ["Неизвестный статус", dataQuality.unknown_status],
    ["Без региона", dataQuality.missing_region],
    ["Без ссылки на источник", dataQuality.missing_url],
    ["Неположительная цена", dataQuality.non_positive_price],
  ] as const : [];

  const mapSteps = map ? [
    ["База данных", map.db_ok],
    ["Набор карты", map.dataset_ok],
    ["S3 manifest", map.s3_manifest_ok],
    ["Публичная выдача", map.public_ok],
  ] as const : [];

  return <section className="reliabilityPage">
    {summary && <section className={`reliabilityHero reliabilityHero--${systemTone}`}>
      <div className="reliabilityHeroMain">
        <div className={`reliabilityStatusIcon reliabilityStatusIcon--${systemTone}`}>
          {summary.system.status === "ok" ? <CheckCircle2 /> : summary.system.status === "incident" ? <AlertCircle /> : <AlertTriangle />}
        </div>
        <div>
          <div className="reliabilityHeroMeta">
            <span>Production</span>
            <span>Обновлено {reliabilityDateAge(summary.generated_at)}</span>
          </div>
          <h2>{summary.system.title_ru}</h2>
          <div className="reliabilityHeroReasons">
            {summary.system.reasons.slice(0, 2).map((reason) => <p key={reason.code}><strong>{reason.title}.</strong> {reason.detail}</p>)}
            {!summary.system.reasons.length && <p>Критических отклонений по обязательным проверкам не обнаружено.</p>}
          </div>
        </div>
      </div>
      <div className="reliabilityVersion">
        <span>Версия</span>
        <strong>{summary.system.deployment_sha ? summary.system.deployment_sha.slice(0, 8) : summary.system.app_version || "—"}</strong>
        <small>Схема {summary.system.schema_revision || "—"}</small>
      </div>
    </section>}

    {summary && <section className="reliabilityKpiGrid" aria-label="Ключевые показатели надёжности">
      <article data-health={sourceSummary && sourceSummary.failed === 0 && sourceSummary.degraded === 0 ? "ok" : "warn"}>
        <div className="reliabilityKpiIcon"><Wifi /></div>
        <span>Источники</span>
        <strong>{sourceSummary ? `${sourceSummary.complete_fresh} / ${sourceSummary.configured_total}` : "—"}</strong>
        <small>{sourceSummary ? `${sourceSummary.configured_total} настроено · ${sourceSummary.failed} с ошибкой · ${sourceSummary.paused_total} на паузе` : "—"}</small>
      </article>
      <article data-health={sourceSummary?.oldest_complete_snapshot_age_seconds != null && sourceSummary.oldest_complete_snapshot_age_seconds <= 36 * 3600 ? "ok" : "warn"}>
        <div className="reliabilityKpiIcon"><Activity /></div>
        <span>Свежесть данных</span>
        <strong>{sourceSummary && sourceSummary.complete_fresh < sourceSummary.configured_total
          ? "Есть неполные данные"
          : sourceSummary?.oldest_complete_snapshot_age_seconds != null
            ? reliabilityAge(sourceSummary.oldest_complete_snapshot_age_seconds)
            : "Нет полного снимка"}</strong>
        <small>{sourceSummary
          ? `${sourceSummary.complete_fresh} из ${sourceSummary.configured_total} настроенных источников имеют актуальный полный снимок`
          : "нет данных"}</small>
      </article>
      <article data-health={geo && geo.percent >= 90 ? "ok" : "warn"}>
        <div className="reliabilityKpiIcon"><MapPinned /></div>
        <span>GEO</span>
        <strong>{reliabilityPercent(geo?.percent)}</strong>
        <small>{geo ? `${reliabilityNumber(geo.current_geocoded)} / ${reliabilityNumber(geo.population)} с текущими координатами` : "—"}</small>
      </article>
      <article data-health={map?.status === "ok" ? "ok" : map?.status === "incident" ? "bad" : "warn"}>
        <div className="reliabilityKpiIcon"><Map /></div>
        <span>Карта</span>
        <strong>{reliabilityNumber(map?.point_count)}</strong>
        <small>{map?.dataset_version || "нет текущего набора"} · {map?.status === "ok" ? "доставка подтверждена" : "требует проверки"}</small>
      </article>
      <article data-health={integrity?.critical_count === 0 ? integrity.warning_count === 0 ? "ok" : "warn" : "bad"}>
        <div className="reliabilityKpiIcon"><ShieldCheck /></div>
        <span>Целостность</span>
        <strong>{integrity ? `${integrity.critical_count} критических` : "—"}</strong>
        <small>{integrity ? `${integrity.warning_count} классов предупреждений` : "—"}</small>
      </article>
      <article data-health={infrastructureDegraded ? "warn" : "ok"}>
        <div className="reliabilityKpiIcon"><Server /></div>
        <span>Инфраструктура</span>
        <strong>{infrastructureDegraded ? "Есть ограничения" : "Норма"}</strong>
        <small>API · база · очередь · диагностика runner</small>
      </article>
      <article data-health={host?.backup.healthy ? "ok" : "bad"}>
        <div className="reliabilityKpiIcon"><Database /></div>
        <span>Резервная копия</span>
        <strong>{host?.backup.age_hours != null ? reliabilityAge(host.backup.age_hours * 3600) : "Нет свежей проверки"}</strong>
        <small>{host?.backup.healthy ? "восстановление подтверждено" : "требуется свежий backup и restore test"}</small>
      </article>
      <article data-health={host?.disk.status === "ok" ? "ok" : host?.disk.status === "critical" ? "bad" : "warn"}>
        <div className="reliabilityKpiIcon"><HardDrive /></div>
        <span>Диск C:</span>
        <strong>{host?.disk.free_gb != null ? `${number(host.disk.free_gb)} ГБ` : "Нет данных"}</strong>
        <small>норма ≥ 25 ГБ · предупреждение 15–25 ГБ</small>
      </article>
    </section>}

    {summary && <section className="pageCard reliabilityAttention">
      <div className="reliabilitySectionHeading">
        <div><span className="eyebrow">Приоритет</span><h2>Требует внимания</h2></div>
        <span className={`healthBadge ${summary.system.reasons.length ? "warn" : "ok"}`}>
          {summary.system.reasons.length ? summary.system.reasons.length : "Нет проблем"}
        </span>
      </div>
      {summary.system.reasons.length ? <div className="reliabilityAttentionList">
        {summary.system.reasons.map((reason) => <article key={reason.code} data-severity={reason.severity}>
          {reason.severity === "critical" ? <AlertCircle /> : <AlertTriangle />}
          <div><strong>{reason.title}</strong><span>{reason.detail}</span></div>
          {reasonTargets[reason.code] && <a href={`#${reasonTargets[reason.code]}`}>Открыть</a>}
        </article>)}
      </div> : <div className="reliabilityEmpty"><CheckCircle2 /><span>Актуальных проблем, влияющих на общий статус, нет.</span></div>}
    </section>}

    <section id="reliability-sources" className="pageCard reliabilitySourcesCard">
      <div className="reliabilitySectionHeading">
        <div><span className="eyebrow">Сбор данных</span><h2>Источники</h2></div>
        <small>{reliabilityNumber(filteredSources.length)} из {reliabilityNumber(sources.length)}</small>
      </div>
      <div className="reliabilitySourceToolbar">
        <input aria-label="Поиск источника" value={sourceSearch} onChange={(event) => setSourceSearch(event.target.value)} placeholder="Поиск по названию или домену" />
        <div className="reliabilityFilterTabs" role="group" aria-label="Фильтр источников">
          {([
            ["attention", "Требуют внимания"],
            ["all", "Все"],
            ["healthy", "Актуальные"],
            ["paused", "На паузе"],
            ["pilot", "Пилотные"],
          ] as const).map(([id, title]) => <button key={id} className={sourceFilter === id ? "active" : ""} onClick={() => setSourceFilter(id)}>{title}</button>)}
        </div>
      </div>
      <div className="reliabilitySourceHeader" aria-hidden="true">
        <span>Источник</span><span>Состояние</span><span>Последний успех</span><span>Полный снимок</span><span>Записей</span><span>Текущая проблема</span><span />
      </div>
      <div className="reliabilitySourceList">
        {filteredSources.map((source) => <details className="reliabilitySourceRow" key={source.canonical_id}>
          <summary>
            <div className="reliabilitySourceIdentity">
              <strong>{source.display_name_ru}</strong>
              <small>{source.canonical_id}{source.role === "pilot" ? " · пилот" : source.role === "historical_only" ? " · исторический" : ""}</small>
            </div>
            <span className={`healthBadge ${reliabilitySourceTone(source.overall_status)}`}>{RELIABILITY_STATUS_LABELS[source.overall_status] || "Неизвестно"}</span>
            <span>{reliabilityDateAge(source.last_success_at)}</span>
            <span>{RELIABILITY_STATUS_LABELS[source.coverage_status] || "Неизвестно"}{source.complete_snapshot_age_seconds != null ? <small>{reliabilityAge(source.complete_snapshot_age_seconds)}</small> : null}</span>
            <span>{reliabilityNumber(source.database_records)}</span>
            <span className={source.current_error ? "reliabilitySourceProblem" : "reliabilitySourceProblem muted"}>
              {source.current_error
                ? (RELIABILITY_ERROR_LABELS[source.current_error_category || ""] || "Ошибка источника")
                : source.retry_overdue ? "Повтор просрочен" : "Нет актуальной ошибки"}
            </span>
            <ChevronDown className="reliabilityChevron" />
          </summary>
          <div className="reliabilitySourceDetails">
            <div><span>Свежесть</span><strong>{RELIABILITY_STATUS_LABELS[source.freshness_status] || "Неизвестно"}</strong></div>
            <div><span>Защита соединения</span><strong>{RELIABILITY_STATUS_LABELS[source.circuit_state] || "Неизвестно"}</strong></div>
            <div><span>Последний проход</span><strong>{reliabilityNumber(source.last_pages_scanned)} стр.</strong><small>+{reliabilityNumber(source.last_items_inserted)} · обновлено {reliabilityNumber(source.last_items_updated)} · архив {reliabilityNumber(source.last_items_archived)} · ошибки карточек {reliabilityNumber(source.last_items_failed)}</small></div>
            <div><span>Следующая попытка</span><strong>{source.next_retry_at ? (source.retry_overdue ? `Просрочена · ${reliabilityDateAge(source.next_retry_at)}` : new Date(source.next_retry_at).toLocaleString("ru-RU")) : "Не запланирована"}</strong></div>
            {source.aliases.length > 0 && <div><span>Исторические названия</span><strong>{source.aliases.join(", ")}</strong></div>}
            {source.current_error && <div className="reliabilityDetailError"><span>Текущая ошибка</span><strong>{source.current_error}</strong></div>}
            {!source.current_error && source.last_historical_error && <div><span>Последняя историческая ошибка</span><strong>{source.last_historical_error}</strong><small>Закрыта более поздним успешным проходом.</small></div>}
            {role === "admin" && backgroundJobs && source.role !== "historical_only" && <div className="reliabilitySourceActions"><button className="secondaryButton" disabled={Boolean(action)} onClick={() => runAction(`probe:${source.canonical_id}`, () => probeSourceHealth(source.canonical_id))}>Проверить источник</button></div>}
          </div>
        </details>)}
        {!filteredSources.length && <div className="reliabilityEmpty"><Search /><span>Источники по выбранному фильтру не найдены.</span></div>}
      </div>
    </section>

    {summary && <div className="reliabilityTwoColumn">
      <section id="reliability-geo" className="pageCard reliabilityGeoCard">
        <div className="reliabilitySectionHeading"><div><span className="eyebrow">Геоданные</span><h2>GEO</h2></div><span className={`healthBadge ${geo?.paused ? "neutral" : "warn"}`}>{geo?.paused ? "На паузе" : "Активно"}</span></div>
        <p className="reliabilityScope">{geo?.scope_ru}</p>
        <div className="reliabilityGeoProgress"><span style={{ width: `${Math.min(100, Math.max(0, geo?.percent || 0))}%` }} /></div>
        <div className="reliabilityGeoHeadline"><strong>{reliabilityPercent(geo?.percent)}</strong><span>{reliabilityNumber(geo?.current_geocoded)} из {reliabilityNumber(geo?.population)} с текущими координатами</span></div>
        <div className="reliabilityMiniGrid">
          <div><span>Требуют обработки</span><strong>{reliabilityNumber(geo?.actionable_remaining)}</strong></div>
          <div><span>Соответствие не найдено</span><strong>{reliabilityNumber(geo?.deferred_no_match)}</strong></div>
          <div><span>Не прошло проверку</span><strong>{reliabilityNumber(geo?.deferred_validation)}</strong></div>
          <div><span>Ожидание сети</span><strong>{reliabilityNumber(geo?.network_wait)}</strong></div>
          <div><span>Недостаточно данных</span><strong>{reliabilityNumber(geo?.deferred_bad_input)}</strong></div>
          <div><span>Исторически имели GEO-снимок</span><strong>{reliabilityNumber(geo?.history_lots)}</strong></div>
        </div>
        {role === "admin" && <div className="reliabilityInlineActions">
          {backgroundJobs ? <>
            <button className="secondaryButton" disabled={Boolean(action) || geo?.paused === true} onClick={() => runAction("pause", pauseGeocoding)}>Поставить GEO на паузу</button>
            <button className="secondaryButton" disabled={Boolean(action) || geo?.paused !== true} onClick={() => runAction("resume", resumeGeocoding)}>Возобновить GEO</button>
          </> : <small className="operationsReadOnlyNote">Операционные действия недоступны в этом read-only deployment.</small>}
        </div>}
      </section>

      <section id="reliability-map" className="pageCard reliabilityMapCard">
        <div className="reliabilitySectionHeading"><div><span className="eyebrow">Публикация</span><h2>Карта</h2></div><span className={`healthBadge ${map?.status === "ok" ? "ok" : map?.status === "incident" ? "bad" : "warn"}`}>{map?.status === "ok" ? "Доставка подтверждена" : map?.status === "incident" ? "Нарушение" : "Требует проверки"}</span></div>
        <div className="reliabilityMapHeadline"><strong>{reliabilityNumber(map?.point_count)}</strong><span>объектов · {map?.dataset_version || "нет версии"}</span></div>
        <div className="reliabilityDeliveryChain">
          {mapSteps.map(([title, ok]) => <div key={title} data-state={ok === true ? "ok" : ok === false ? "bad" : "unknown"}><span>{ok === true ? "✓" : ok === false ? "!" : "?"}</span><strong>{title}</strong></div>)}
        </div>
        <div className="reliabilityMapStats">
          <div><span>Ожидается по БД</span><strong>{reliabilityNumber(map?.db_eligible_count)}</strong></div>
          <div><span>В наборе карты</span><strong>{reliabilityNumber(map?.dataset_unique_lot_count)}</strong></div>
          <div><span>Не хватает</span><strong>{reliabilityNumber(map?.missing_from_dataset_count)}</strong></div>
          <div><span>Лишние</span><strong>{reliabilityNumber(map?.extra_in_dataset_count)}</strong></div>
        </div>
        {map?.manifest_error && <div className="reliabilityInlineError"><AlertCircle /><span>{map.manifest_error}</span></div>}
        <small className="reliabilityFootnote">Проверяется цепочка БД → набор карты → S3 manifest → публичная выдача. Проверка manifest кешируется на сервере.</small>
      </section>
    </div>}

    {summary && <div className="reliabilityTwoColumn reliabilityTwoColumn--wide">
      <section className="pageCard">
        <div className="reliabilitySectionHeading"><div><span className="eyebrow">Текущая выборка</span><h2>Полнота и качество данных</h2></div><strong>{reliabilityNumber(dataQuality?.current_population)} объектов</strong></div>
        <p className="reliabilityScope">{dataQuality?.scope_ru}</p>
        <div className="reliabilityQualityGrid">
          {qualityMetrics.map(([title, metric]) => <article key={title} data-health={metric.percent === 0 ? "ok" : metric.percent < 5 ? "warn" : "bad"}>
            <span>{title}</span><strong>{reliabilityNumber(metric.count)}</strong><small>{reliabilityPercent(metric.percent)}</small>
          </article>)}
        </div>
        <div className="reliabilityHistoryLine"><span>Историческая база</span><strong>{reliabilityNumber(dataQuality?.historical_total)} записей</strong></div>
      </section>

      <section id="reliability-infrastructure" className="pageCard">
        <div className="reliabilitySectionHeading"><div><span className="eyebrow">Хост</span><h2>Инфраструктура</h2></div><span className={`healthBadge ${infrastructureDegraded ? "warn" : "ok"}`}>{infrastructureDegraded ? "Есть ограничения" : "Норма"}</span></div>
        <div className="reliabilityInfrastructureList">
          <article><Database /><div><span>API и база данных</span><strong>{host?.api.status === "ok" && host?.database.status === "ok" ? "Готовы" : "Требуют внимания"}</strong></div></article>
          <article><Server /><div><span>Очередь</span><strong>{host?.queue.label_ru || "Нет данных"}</strong></div></article>
          <article><HardDrive /><div><span>Диск C:</span><strong>{host?.disk.free_gb != null ? `${number(host.disk.free_gb)} ГБ` : "Нет данных"}</strong><small>{host?.disk.status === "warning" ? "Ниже рекомендуемых 25 ГБ" : host?.disk.status === "critical" ? "Критически мало места" : "Запас диска в норме"}</small></div></article>
          <article><ShieldCheck /><div><span>Backup</span><strong>{host?.backup.healthy ? "Проверен восстановлением" : "Требует внимания"}</strong><small>{host?.backup.age_hours != null ? reliabilityAge(host.backup.age_hours * 3600) : "нет свежего snapshot"}</small></div></article>
          <article><Activity /><div><span>Диагностика runner</span><strong>{host?.runner_diagnostic.fresh ? "Свежая" : "Устарела"}</strong><small>{host?.runner_diagnostic.age_seconds != null ? reliabilityAge(host.runner_diagnostic.age_seconds) : "не записана"} · это не статус online/offline</small></div></article>
        </div>
      </section>
    </div>}

    {summary && <section id="reliability-integrity" className="pageCard reliabilityIntegrityCard">
      <div className="reliabilitySectionHeading"><div><span className="eyebrow">Связи и инварианты</span><h2>Целостность данных</h2></div><span className={`healthBadge ${integrity?.critical_count ? "bad" : integrity?.warning_count ? "warn" : "ok"}`}>{integrity?.critical_count ? "Есть критические нарушения" : integrity?.warning_count ? "Есть предупреждения" : "Норма"}</span></div>
      <div className="reliabilityIntegrityGrid">
        <div><span>Source без Processed</span><strong>{reliabilityNumber(integrity?.source_without_processed)}</strong></div>
        <div><span>Processed без Source</span><strong>{reliabilityNumber(integrity?.processed_without_source)}</strong></div>
        <div><span>Source одновременно active + archived</span><strong>{reliabilityNumber(integrity?.active_and_archived_source_lots)}</strong></div>
        <div><span>Архив без времени архивации</span><strong>{reliabilityNumber(integrity?.archived_processed_without_timestamp)}</strong></div>
        <div><span>Некорректный порядок дат</span><strong>{reliabilityNumber((integrity?.first_seen_after_last_seen || 0) + (integrity?.application_start_after_deadline || 0) + (integrity?.archived_before_first_seen || 0))}</strong></div>
        <div><span>Расхождения карты</span><strong>{reliabilityNumber((integrity?.map_missing_from_dataset || 0) + (integrity?.map_extra_in_dataset || 0) + (integrity?.map_tile_count_mismatch || 0) + (integrity?.map_point_count_mismatch || 0))}</strong></div>
      </div>
    </section>}

    {summary && <section id="reliability-journal" className="pageCard reliabilityJournalCard">
      <div className="reliabilitySectionHeading">
        <div><span className="eyebrow">Последние события</span><h2>Журнал</h2></div>
        <small>{reliabilityNumber(filteredJournal.length)} событий</small>
      </div>
      <div className="reliabilityFilterTabs reliabilityJournalFilters" role="group" aria-label="Фильтр журнала">
        {([
          ["all", "Все"],
          ["errors", "Ошибки"],
          ["sources", "Источники"],
          ["geo", "GEO"],
          ["map", "Карта"],
          ["infrastructure", "Инфраструктура"],
        ] as const).map(([id, title]) => <button key={id} className={journalFilter === id ? "active" : ""} onClick={() => setJournalFilter(id)}>{title}</button>)}
      </div>
      <div className="reliabilityJournalList">
        {filteredJournal.slice(0, 50).map((item) => <article key={item.id} data-severity={item.severity}>
          <span className={`reliabilityJournalDot reliabilityJournalDot--${["error", "critical"].includes(item.severity.toLocaleLowerCase("ru-RU")) ? "bad" : item.severity.toLocaleLowerCase("ru-RU") === "warning" ? "warn" : "neutral"}`} />
          <div><strong>{RELIABILITY_JOURNAL_LABELS[item.category] || "Система"}</strong><span>{item.detail}</span></div>
          <time>{item.at ? new Date(item.at).toLocaleString("ru-RU") : "—"}</time>
        </article>)}
        {!filteredJournal.length && <div className="reliabilityEmpty"><Activity /><span>Событий по выбранному фильтру нет.</span></div>}
      </div>
    </section>}

    {role === "admin" && <section className="pageCard reliabilityDiagnosticsCard">
      <div className="reliabilitySectionHeading"><div><span className="eyebrow">Поддержка</span><h2>Диагностика</h2></div></div>
      <p>Диагностика не выводится огромным JSON на экран. Её можно скачать или скопировать для анализа.</p>
      <div className="reliabilityInlineActions">
        <button className="secondaryButton" disabled={Boolean(action)} onClick={() => void downloadDiagnostics()}><Download size={15} />Скачать diagnostics.json</button>
        <button className="secondaryButton" disabled={Boolean(action)} onClick={() => void copyDiagnostics()}><Copy size={15} />Копировать</button>
      </div>
      {notice && <div className="successBox"><CheckCircle2 size={16} />{notice}</div>}
    </section>}

    {error && <State error>{error}</State>}
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
