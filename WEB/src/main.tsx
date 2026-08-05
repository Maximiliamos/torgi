import React from "react";
import { createRoot } from "react-dom/client";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import {
  Activity, AlertCircle, Banknote, Bookmark, Building2, Calculator, CheckCircle2,
  ExternalLink, FileSearch, Files, Heart, ListFilter, Loader2, LogOut, Map,
  MapPin, NotebookPen, RefreshCcw, Search, ShieldCheck, Sparkles, X
} from "lucide-react";
import {
  addNote, AuthUser, calculateMaxBid, fetchCurrentUser, fetchDiagnostics, fetchDocuments,
  fetchLotDetail, fetchLots, fetchMapLots, fetchNotes, fetchParticipation, fetchQuality,
  fetchSavedSearches, fetchSources, fetchStats, importOnlineLot, login, logout, LotDetail, LotListItem, LotQuery,
  MainView, MapLot, OnlineLot, Participation, saveParticipation, searchCadastre, searchOnline,
  saveSearch, SearchSource, setReviewStatus, SortMode, SourceHealth, StatsResponse, toggleWatchlist
} from "./lib/api";
import "./styles.css";

const CATEGORIES = [
  ["land", "Земля"], ["car", "Авто"], ["apartment", "Квартиры"], ["house", "Дома"],
  ["commercial_room", "Помещения"], ["commercial_building", "Здания"],
  ["commercial_building_with_land", "Здания с землёй"], ["receivable", "Дебиторка"], ["other", "Прочее"]
] as const;
const STATUSES = [["active", "Активные"], ["scheduled", "Запланированные"], ["unknown", "Без статуса"]] as const;
const initialQuery: LotQuery = {
  city_slug: "yaroslavl", page: 1, per_page: 18, search: "", categories: [],
  statuses: ["active", "scheduled"], min_discount: 0, max_discount: 100, sort: "recommended"
};

const money = (value?: number | null) => value == null ? "—" : new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 0 }).format(value);
const number = (value?: number | null, suffix = "") => value == null ? "—" : `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(value)}${suffix}`;
const label = (items: readonly (readonly [string, string])[], value: string) => items.find(([id]) => id === value)?.[1] || value;
const toggle = (items: string[], value: string) => items.includes(value) ? items.filter((item) => item !== value) : [...items, value];

function State({ children, error = false }: { children: React.ReactNode; error?: boolean }) {
  return <div className={error ? "errorBox" : "panelState"}>{error ? <AlertCircle size={18} /> : <Loader2 className="spin" size={20} />}<span>{children}</span></div>;
}

function DetailPanel({ detail, loading, onClose }: { detail: LotDetail | null; loading: boolean; onClose: () => void }) {
  const [message, setMessage] = React.useState("");
  const [note, setNote] = React.useState("");
  return <aside className="detailPanel">
    <div className="detailHeader"><div><span className="eyebrow">Карточка лота</span><h2>{detail?.title || "Выберите лот"}</h2></div><button className="iconButton" onClick={onClose}><X size={18} /></button></div>
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
      {detail.geo && <section className="detailSection"><h3>Геоданные</h3><p>{detail.geo.source}, {detail.geo.confidence}: {detail.geo.centroid_lat.toFixed(6)}, {detail.geo.centroid_lon.toFixed(6)}</p></section>}
      <div className="detailActions">
        <button onClick={async () => { const result = await toggleWatchlist(detail.id); setMessage(result.watchlisted ? "Добавлено в избранное" : "Удалено из избранного"); }}><Heart size={16} /> Избранное</button>
        {(detail.lot_url || detail.source_url) && <a href={detail.lot_url || detail.source_url || "#"} target="_blank" rel="noreferrer"><ExternalLink size={16} /> Источник</a>}
      </div>
      <div className="inlineForm"><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Добавить заметку" /><button onClick={async () => { if (!note.trim()) return; await addNote(detail.id, note); setNote(""); setMessage("Заметка сохранена"); }}><NotebookPen size={15} /></button></div>
      {message && <div className="successBox"><CheckCircle2 size={16} />{message}</div>}
    </div>}
  </aside>;
}

function RegistryView({ refreshToken }: { refreshToken: number }) {
  const [query, setQuery] = React.useState(initialQuery);
  const [lots, setLots] = React.useState<LotListItem[]>([]);
  const [stats, setStats] = React.useState<StatsResponse | null>(null);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [detail, setDetail] = React.useState<LotDetail | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [savedSearches, setSavedSearches] = React.useState<Array<{ id: number; name: string; query: LotQuery }>>([]);
  const load = React.useCallback(async () => {
    setLoading(true); setError(null);
    try { const [list, summary] = await Promise.all([fetchLots(query), fetchStats(query.city_slug)]); setLots(list.items); setTotal(list.total); setStats(summary); }
    catch (err) { setError(err instanceof Error ? err.message : "Ошибка загрузки"); }
    finally { setLoading(false); }
  }, [query]);
  React.useEffect(() => { const id = window.setTimeout(load, 220); return () => window.clearTimeout(id); }, [load, refreshToken]);
  React.useEffect(() => { fetchSavedSearches().then(setSavedSearches).catch(() => setSavedSearches([])); }, []);
  const open = async (id: number) => { setDetailLoading(true); try { setDetail(await fetchLotDetail(id, query.city_slug)); } catch (err) { setError(String(err)); } finally { setDetailLoading(false); } };
  const pages = Math.max(1, Math.ceil(total / query.per_page));
  return <>
    <section className="kpiGrid"><Kpi icon={<Building2 />} label="Всего лотов" value={number(stats?.total_lots)} /><Kpi icon={<FileSearch />} label="Активные" value={number(stats?.active_lots)} /><Kpi icon={<Sparkles />} label="С AI-анализом" value={number(stats?.appraised_lots)} /><Kpi icon={<Banknote />} label="Средний дисконт" value={number(stats?.average_discount, "%")} /></section>
    <section className="workspace">
      <aside className="filters"><h3><ListFilter size={17} /> Фильтры</h3>
        <label className="field"><span>Регион</span><select value={query.city_slug} onChange={(e) => setQuery({ ...query, city_slug: e.target.value, page: 1 })}><option value="yaroslavl">Ярославская область</option><option value="76">Регион 76</option><option value="84">Т‑Банкрот 84</option></select></label>
        <label className="field"><span>Поиск</span><div className="searchBox"><Search size={16} /><input value={query.search} onChange={(e) => setQuery({ ...query, search: e.target.value, page: 1 })} placeholder="Название, адрес, кадастр" /></div></label>
        <label className="field"><span>Сортировка</span><select value={query.sort} onChange={(e) => setQuery({ ...query, sort: e.target.value as SortMode, page: 1 })}><option value="recommended">Рекомендации</option><option value="discount">Дисконт</option><option value="price_asc">Цена ↑</option><option value="price_desc">Цена ↓</option><option value="newest">Новые</option></select></label>
        <label className="field"><span>Сохранённые фильтры</span><select value="" onChange={(e) => { const saved = savedSearches.find((item) => item.id === Number(e.target.value)); if (saved) setQuery({ ...saved.query, page: 1 }); }}><option value="">Выберите фильтр</option>{savedSearches.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <button className="secondaryButton" onClick={async () => { const name = window.prompt("Название фильтра", `Поиск ${new Date().toLocaleDateString("ru-RU")}`); if (!name) return; await saveSearch(name, query); setSavedSearches(await fetchSavedSearches()); }}>Сохранить фильтр</button>
        <div className="chipGroup"><span>Категории</span><div>{CATEGORIES.map(([id, text]) => <button key={id} className={`chip ${query.categories.includes(id) ? "active" : ""}`} onClick={() => setQuery({ ...query, categories: toggle(query.categories, id), page: 1 })}>{text}</button>)}</div></div>
        <div className="chipGroup"><span>Статусы</span><div>{STATUSES.map(([id, text]) => <button key={id} className={`chip ${query.statuses.includes(id) ? "active" : ""}`} onClick={() => setQuery({ ...query, statuses: toggle(query.statuses, id), page: 1 })}>{text}</button>)}</div></div>
      </aside>
      <section className="lotArea"><div className="lotToolbar"><div><strong>{total} найдено</strong><span>Страница {query.page} из {pages}</span></div></div>{error && <State error>{error}</State>}
        <div className="tableHead"><span>Лот</span><span>Тип и статус</span><span>Цена</span><span>Дисконт</span><span>Рейтинг</span></div>
        <div className="lotList">{loading && <State>Загрузка лотов</State>}{!loading && lots.map((lot) => <button className="lotRow" key={lot.id} onClick={() => open(lot.id)}><span className="lotMain"><strong>{lot.title}</strong><small>{lot.address || lot.description || "Адрес не указан"}</small></span><span className="lotMeta">{label(CATEGORIES, lot.category)}<small>{lot.auction_status}</small></span><b>{money(lot.current_price)}</b><b>{number(lot.discount_percent, "%")}</b><b>{number(lot.rating)}</b></button>)}</div>
        <div className="pagination"><button disabled={query.page <= 1} onClick={() => setQuery({ ...query, page: query.page - 1 })}>Назад</button><span>{query.page} / {pages}</span><button disabled={query.page >= pages} onClick={() => setQuery({ ...query, page: query.page + 1 })}>Вперёд</button></div>
      </section>
      <DetailPanel detail={detail} loading={detailLoading} onClose={() => setDetail(null)} />
    </section>
  </>;
}

function Kpi({ icon, label: text, value }: { icon: React.ReactNode; label: string; value: string }) { return <div className="kpi"><span className="kpiIcon">{icon}</span><div><span>{text}</span><strong>{value}</strong></div></div>; }

function SearchView({ refreshToken }: { refreshToken: number }) {
  const [source, setSource] = React.useState<SearchSource>("torgi-gov");
  const [form, setForm] = React.useState({ search: "", region: "", category: "", price_min: "", price_max: "", page: 1, include_closed: false });
  const [items, setItems] = React.useState<OnlineLot[]>([]); const [loading, setLoading] = React.useState(false); const [error, setError] = React.useState(""); const [imported, setImported] = React.useState<number[]>([]);
  const run = React.useCallback(async () => { setLoading(true); setError(""); try { const result = await searchOnline(source, { ...form, price_min: form.price_min || undefined, price_max: form.price_max || undefined }); setItems(result.items); } catch (err) { setError(err instanceof Error ? err.message : "Ошибка источника"); } finally { setLoading(false); } }, [form, source]);
  React.useEffect(() => { if (refreshToken) void run(); }, [refreshToken]);
  return <section className="pageCard"><div className="sourceTabs">{(["torgi-gov", "tbankrot", "lot-online"] as SearchSource[]).map((id) => <button className={source === id ? "active" : ""} onClick={() => { setSource(id); setItems([]); }} key={id}>{id === "torgi-gov" ? "ГИС Торги" : id === "tbankrot" ? "Т‑Банкрот" : "РАД / ЛОТ‑ОНЛАЙН"}</button>)}</div>
    <div className="searchForm"><label className="field"><span>Поиск</span><input value={form.search} onChange={(e) => setForm({ ...form, search: e.target.value })} placeholder="Название, адрес, кадастровый номер" /></label><label className="field"><span>Регион</span><input value={form.region} onChange={(e) => setForm({ ...form, region: e.target.value })} placeholder="Код или название" /></label><label className="field"><span>Категория</span><input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} placeholder="Код категории" /></label><label className="field"><span>Цена от</span><input type="number" value={form.price_min} onChange={(e) => setForm({ ...form, price_min: e.target.value })} /></label><label className="field"><span>Цена до</span><input type="number" value={form.price_max} onChange={(e) => setForm({ ...form, price_max: e.target.value })} /></label><label className="checkField"><input type="checkbox" checked={form.include_closed} onChange={(e) => setForm({ ...form, include_closed: e.target.checked })} />Архивные</label><button className="primaryButton" onClick={run}><Search size={16} />Найти онлайн</button></div>
    {error && <State error>{error}</State>}{loading && <State>Запрос к {source}</State>}
    <div className="onlineGrid">{items.map((lot, index) => <article className="onlineCard" key={`${lot.source_system}-${lot.external_id}`}><span className="sourceBadge">{lot.source_system}</span><h3>{lot.title}</h3><p>{lot.address || lot.description || "Без адреса"}</p><div><strong>{money(lot.current_price || lot.start_price)}</strong><span>{lot.auction_status}</span></div><div className="onlineActions"><button disabled={imported.includes(index)} onClick={async () => { try { await importOnlineLot(lot); setImported((values) => [...values, index]); } catch (err) { setError(String(err)); } }}>{imported.includes(index) ? "В реестре" : "Импортировать"}</button>{(lot.lot_url || lot.source_url) && <a href={lot.lot_url || lot.source_url || "#"} target="_blank" rel="noreferrer">Источник <ExternalLink size={14} /></a>}</div></article>)}</div>
  </section>;
}

function LeafletMap({ lots, selectedCadastre }: { lots: MapLot[]; selectedCadastre: Record<string, unknown> | null }) {
  const host = React.useRef<HTMLDivElement>(null); const mapRef = React.useRef<L.Map | null>(null); const layerRef = React.useRef<L.LayerGroup | null>(null);
  React.useEffect(() => { if (!host.current || mapRef.current) return; const map = L.map(host.current).setView([57.6261, 39.8845], 9); L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map); mapRef.current = map; return () => { map.remove(); mapRef.current = null; }; }, []);
  React.useEffect(() => { const map = mapRef.current; if (!map) return; layerRef.current?.remove(); const group = L.layerGroup().addTo(map); layerRef.current = group; const points: L.LatLngExpression[] = []; lots.forEach((lot) => { const color = lot.review_status === "approved" ? "#169b75" : lot.review_status === "rejected" ? "#dc3f4d" : lot.review_status === "maybe" ? "#e5a313" : "#315b78"; const point: L.LatLngExpression = [lot.lat, lot.lon]; points.push(point); L.circleMarker(point, { radius: 8, color: "#fff", weight: 2, fillColor: color, fillOpacity: .95 }).bindPopup(`<b>${lot.title.split("<").join("&lt;")}</b><br>${money(lot.current_price)}<br>${lot.address || ""}`).addTo(group); }); if (selectedCadastre && typeof selectedCadastre.lat === "number" && typeof selectedCadastre.lon === "number") { const p: L.LatLngExpression = [selectedCadastre.lat, selectedCadastre.lon]; points.push(p); L.circleMarker(p, { radius: 11, color: "#7c3aed", weight: 4 }).bindPopup(String(selectedCadastre.cadastral_number || selectedCadastre.address || "Кадастровый объект")).addTo(group); } if (points.length) map.fitBounds(L.latLngBounds(points), { padding: [30, 30], maxZoom: 15 }); }, [lots, selectedCadastre]);
  return <div className="mapCanvas" ref={host} />;
}

function MapView({ refreshToken }: { refreshToken: number }) {
  const [lots, setLots] = React.useState<MapLot[]>([]); const [error, setError] = React.useState(""); const [cadQuery, setCadQuery] = React.useState(""); const [cad, setCad] = React.useState<Record<string, unknown> | null>(null);
  const load = React.useCallback(async () => { try { setLots((await fetchMapLots("yaroslavl")).items); } catch (err) { setError(err instanceof Error ? err.message : "Ошибка карты"); } }, []);
  React.useEffect(() => { void load(); }, [load, refreshToken]);
  const review = async (lotId: number, status: string) => { await setReviewStatus(lotId, status); setLots((items) => items.map((lot) => lot.id === lotId ? { ...lot, review_status: status } : lot)); };
  return <section className="mapLayout"><aside className="mapSidebar"><h2>Карта лотов</h2><p>{lots.length} объектов с подтверждёнными координатами</p><label className="field"><span>Кадастровый номер или адрес</span><input value={cadQuery} onChange={(e) => setCadQuery(e.target.value)} /></label><button className="primaryButton" onClick={async () => { try { setCad(await searchCadastre(cadQuery)); } catch (err) { setError(String(err)); } }}><MapPin size={16} />Найти объект</button>{cad && <pre className="cadInfo">{JSON.stringify(cad, null, 2)}</pre>}{error && <State error>{error}</State>}<h3>Статус проверки</h3><div className="legend"><span><i className="green" />Одобрено</span><span><i className="amber" />Возможно</span><span><i className="red" />Отклонено</span><span><i />Не проверено</span></div><div className="mapReviewList">{lots.slice(0, 8).map((lot) => <article key={lot.id}><strong>{lot.title}</strong><div><button title="Одобрить" onClick={() => review(lot.id, "approved")}>✓</button><button title="Возможно" onClick={() => review(lot.id, "maybe")}>?</button><button title="Отклонить" onClick={() => review(lot.id, "rejected")}>×</button></div></article>)}</div></aside><LeafletMap lots={lots} selectedCadastre={cad} /></section>;
}

const checklistLabels: Array<[keyof Omit<Participation, "lot_id" | "source_lot_id" | "notes">, string]> = [["etp_accredited", "Аккредитация на ЭТП"], ["signature_valid", "ЭЦП действительна"], ["application_completed", "Заявка заполнена"], ["deposit_sent", "Задаток отправлен"], ["payment_purpose_verified", "Назначение платежа проверено"], ["deposit_received", "Задаток зачислен"], ["documents_signed", "Документы подписаны"], ["application_accepted", "Заявка принята"]];
function DealView() {
  const [lotId, setLotId] = React.useState(""); const id = Number(lotId); const [tab, setTab] = React.useState("calculator"); const [error, setError] = React.useState("");
  const [calc, setCalc] = React.useState({ scenario_name: "Базовый сценарий", conservative_sale_price: "", repair_cost: "0", legal_cost: "0", monthly_holding_cost: "0", holding_months: "6", taxes: "0", sale_commission_percent: "3", target_profit: "0", risk_reserve: "0", annual_capital_cost_percent: "0", intended_bid: "" }); const [result, setResult] = React.useState<Record<string, Record<string, number>> | null>(null);
  const [participation, setParticipation] = React.useState<Participation | null>(null); const [notes, setNotes] = React.useState<Array<{ id: number; content: string; created_at: string }>>([]); const [docs, setDocs] = React.useState<Array<Record<string, unknown>>>([]);
  const loadTab = async (next: string) => { setTab(next); setError(""); if (!id) return; try { if (next === "participation") setParticipation(await fetchParticipation(id)); if (next === "notes") setNotes(await fetchNotes(id)); if (next === "documents") setDocs(await fetchDocuments(id)); } catch (err) { setError(err instanceof Error ? err.message : "Ошибка"); } };
  return <section className="pageCard dealPage"><div className="dealHeader"><div><h2>Работа со сделкой</h2><p>Калькулятор, участие, документы и заметки</p></div><label className="field"><span>ID лота из реестра</span><input type="number" value={lotId} onChange={(e) => setLotId(e.target.value)} placeholder="Например, 125" /></label></div><div className="sourceTabs"><button className={tab === "calculator" ? "active" : ""} onClick={() => loadTab("calculator")}><Calculator size={15} />Ставка</button><button className={tab === "participation" ? "active" : ""} onClick={() => loadTab("participation")}><ShieldCheck size={15} />Участие</button><button className={tab === "documents" ? "active" : ""} onClick={() => loadTab("documents")}><Files size={15} />Документы</button><button className={tab === "notes" ? "active" : ""} onClick={() => loadTab("notes")}><NotebookPen size={15} />Заметки</button></div>{error && <State error>{error}</State>}
    {tab === "calculator" && <div className="toolGrid"><div className="formCard">{Object.entries(calc).map(([key, value]) => <label className="field" key={key}><span>{key === "scenario_name" ? "Название сценария" : key.split("_").join(" ")}</span><input type={key === "scenario_name" ? "text" : "number"} value={value} onChange={(e) => setCalc({ ...calc, [key]: e.target.value })} /></label>)}<button className="primaryButton" disabled={!id || !calc.conservative_sale_price} onClick={async () => { try { const payload = Object.fromEntries(Object.entries(calc).map(([key, value]) => [key, key === "scenario_name" ? value : value === "" ? null : Number(value)])); const response = await calculateMaxBid(id, payload); setResult(response.scenarios); } catch (err) { setError(String(err)); } }}>Рассчитать и сохранить</button></div><div className="resultCard"><h3>Максимальная ставка</h3>{result ? Object.entries(result).map(([name, values]) => <article key={name}><strong>{name}</strong><span>{money(values.max_bid)}</span><small>Затраты: {money(values.total_costs)} · Прибыль: {money(values.expected_profit)}</small></article>) : <p>Заполните консервативную цену продажи.</p>}</div></div>}
    {tab === "participation" && <div className="formCard checklist">{participation ? <>{checklistLabels.map(([key, text]) => <label key={key}><input type="checkbox" checked={participation[key]} onChange={(e) => setParticipation({ ...participation, [key]: e.target.checked })} />{text}</label>)}<textarea value={participation.notes || ""} onChange={(e) => setParticipation({ ...participation, notes: e.target.value })} placeholder="Комментарии" /><button className="primaryButton" onClick={async () => { const value = Object.fromEntries([...checklistLabels.map(([key]) => [key, participation[key]]), ["notes", participation.notes]]) as Omit<Participation, "lot_id" | "source_lot_id">; setParticipation(await saveParticipation(id, value)); }}>Сохранить контроль участия</button></> : <p>Укажите ID и откройте вкладку повторно.</p>}</div>}
    {tab === "documents" && <div className="documentList">{docs.length ? docs.map((doc) => <article key={String(doc.id)}><Files /><div><strong>{String(doc.filename)}</strong><span>{Array.isArray(doc.versions) ? `${doc.versions.length} версий` : "Без версий"}</span></div>{doc.source_url ? <a href={String(doc.source_url)} target="_blank" rel="noreferrer"><ExternalLink /></a> : null}</article>) : <p>Документы для выбранного лота не загружены.</p>}</div>}
    {tab === "notes" && <div className="notesList">{notes.map((item) => <article key={item.id}><NotebookPen /><div><p>{item.content}</p><small>{new Date(item.created_at).toLocaleString("ru-RU")}</small></div></article>)}</div>}
  </section>;
}

function ReliabilityView({ refreshToken }: { refreshToken: number }) {
  const [quality, setQuality] = React.useState<Record<string, number>>({}); const [sources, setSources] = React.useState<SourceHealth[]>([]); const [diagnostics, setDiagnostics] = React.useState<Record<string, unknown> | null>(null); const [error, setError] = React.useState("");
  const load = React.useCallback(async () => { try { const [q, s] = await Promise.all([fetchQuality(), fetchSources()]); setQuality(q); setSources(s); } catch (err) { setError(String(err)); } }, []); React.useEffect(() => { void load(); }, [load, refreshToken]);
  return <section className="reliabilityGrid"><div className="pageCard"><h2>Полнота данных</h2><div className="metricGrid">{Object.entries(quality).map(([key, value]) => <div key={key}><span>{key.split("_").join(" ")}</span><strong>{value}</strong></div>)}</div></div><div className="pageCard"><h2>Состояние источников</h2><div className="sourceList">{sources.map((source) => <article key={source.source_system}><i className={source.status === "ok" || source.status === "healthy" ? "ok" : "warn"} /><div><strong>{source.source_system}</strong><span>{source.status} · {source.items_seen} записей</span>{source.last_error && <small>{source.last_error}</small>}</div></article>)}</div><button className="secondaryButton" onClick={async () => setDiagnostics(await fetchDiagnostics())}>Экспорт диагностики</button>{diagnostics && <pre className="diagnostics">{JSON.stringify(diagnostics, null, 2)}</pre>}{error && <State error>{error}</State>}</div></section>;
}

const nav: Array<[MainView, string, React.ReactNode]> = [["search", "Поиск", <Search />], ["registry", "Реестр", <Bookmark />], ["map", "Карта", <Map />], ["deal", "Сделка", <Calculator />], ["reliability", "Надёжность", <Activity />]];
export function App() {
  const [view, setView] = React.useState<MainView>("registry"); const [refreshToken, setRefreshToken] = React.useState(0);
  return <main className="appShell"><header className="topBar"><div><span className="eyebrow">BankrotAI Web</span><h1>{nav.find(([id]) => id === view)?.[1]}</h1></div><button className="primaryButton" onClick={() => setRefreshToken((value) => value + 1)}><RefreshCcw size={16} />Обновить</button></header><nav className="mainNav">{nav.map(([id, text, icon]) => <button key={id} className={view === id ? "active" : ""} onClick={() => setView(id)}>{icon}{text}</button>)}</nav><div className="viewContainer">{view === "search" && <SearchView refreshToken={refreshToken} />}{view === "registry" && <RegistryView refreshToken={refreshToken} />}{view === "map" && <MapView refreshToken={refreshToken} />}{view === "deal" && <DealView />}{view === "reliability" && <ReliabilityView refreshToken={refreshToken} />}</div><footer className="statusLine"><MapPin size={14} /> API same-origin · защищённая сессия</footer></main>;
}

export function AuthenticatedApp() {
  const [user, setUser] = React.useState<AuthUser | null>(null); const [checking, setChecking] = React.useState(true); const [username, setUsername] = React.useState(""); const [password, setPassword] = React.useState(""); const [error, setError] = React.useState("");
  React.useEffect(() => { fetchCurrentUser().then(setUser).catch(() => setUser(null)).finally(() => setChecking(false)); }, []);
  if (checking) return <main className="authScreen"><Loader2 className="spin" /></main>;
  if (!user) return <main className="authScreen"><form className="authCard" onSubmit={async (event) => { event.preventDefault(); setError(""); try { setUser(await login(username, password)); setPassword(""); } catch (err) { setError(err instanceof Error ? err.message : "Ошибка авторизации"); } }}><span className="eyebrow">BankrotAI Web</span><h1>Вход</h1><label className="field"><span>Логин</span><input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} /></label><label className="field"><span>Пароль</span><input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>{error && <State error>{error}</State>}<button className="primaryButton">Войти</button></form></main>;
  return <><button className="logoutButton" onClick={async () => { await logout(); setUser(null); }}><LogOut size={15} />{user.username}</button><App /></>;
}

const root = document.getElementById("root"); if (root) createRoot(root).render(<AuthenticatedApp />);
