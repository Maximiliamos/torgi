import React from "react";
import { Check, ChevronLeft, ChevronRight, ExternalLink, MapPin, RefreshCcw, Search, X } from "lucide-react";

import {
  fetchCapabilities,
  fetchMapLots,
  fetchRegions,
  MapLot,
  RegionOption,
  searchCadastre,
  setReviewStatus,
  syncRegion,
} from "../../lib/api";

const money = (value?: number | null) => value == null
  ? "—"
  : new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 0 }).format(value);

function safeScriptJson(value: unknown) {
  return JSON.stringify(value).split("<").join("\\u003c");
}

function safeExternalUrl(value: string | null) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function MapState({ children, error = false }: { children: React.ReactNode; error?: boolean }) {
  return <div className={error ? "mapDesktopMessage mapDesktopMessage--error" : "mapDesktopMessage"}>{children}</div>;
}

function YandexDesktopMap({
  lots,
  selectedCadastre,
  showCadastre,
  selectedLotId,
  onSelect,
  onReview,
}: {
  lots: MapLot[];
  selectedCadastre: Record<string, unknown> | null;
  showCadastre: boolean;
  selectedLotId: number | null;
  onSelect: (id: number) => void;
  onReview: (id: number, status: string) => void;
}) {
  const frame = React.useRef<HTMLIFrameElement>(null);
  const channel = React.useMemo(() => crypto.randomUUID(), []);

  React.useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.origin !== "null" || event.data?.channel !== channel) return;
      if (event.data?.type === "bankrotai-select") onSelect(Number(event.data.lotId));
      if (event.data?.type === "bankrotai-review") onReview(Number(event.data.lotId), String(event.data.status));
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [channel, onReview, onSelect]);

  const html = React.useMemo(() => `<!doctype html><html><head><meta charset="utf-8"><script src="https://api-maps.yandex.ru/2.1/?lang=ru_RU"></script><style>
html,body,#map{height:100%;margin:0}body{font:13px Arial,sans-serif;overflow:hidden}.hint{position:absolute;z-index:5;left:12px;top:12px;background:#fff;border:1px solid #cbd2dc;border-radius:4px;padding:9px 12px;color:#42526b;box-shadow:0 2px 8px #0002}.map-count{position:absolute;z-index:5;left:12px;bottom:12px;background:#fff;border:1px solid #d6dbe2;border-radius:4px;padding:7px 10px;color:#4b5565;box-shadow:0 2px 8px #0002}
</style></head><body><div id="map"></div><div id="hint" class="hint">Загрузка Яндекс.Карт…</div><div class="map-count">Метки: ${lots.length}</div><script>
const lots=${safeScriptJson(lots)};const cad=${safeScriptJson(selectedCadastre)};const showCad=${showCadastre ? "true" : "false"};const selectedId=${selectedLotId == null ? "null" : Number(selectedLotId)};const channel=${safeScriptJson(channel)};
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));}
function ended(l){return l.is_archived||['closed','completed','cancelled','canceled','failed','annulled','archive','archived'].includes(String(l.status||'').toLowerCase());}
function color(l){if(ended(l))return '#111111';return l.review_status==='approved'?'#24a269':l.review_status==='maybe'?'#e0aa16':l.review_status==='rejected'?'#d94b4b':'#7d8795';}
function icon(l){const c=color(l),selected=Number(l.id)===selectedId;return '<svg xmlns="http://www.w3.org/2000/svg" width="42" height="52" viewBox="0 0 38 48"><path d="M19 1C9.1 1 1 9.1 1 19c0 13.2 18 28 18 28s18-14.8 18-28C37 9.1 28.9 1 19 1z" fill="'+c+'" stroke="'+(selected?'#f1a800':'white')+'" stroke-width="'+(selected?'3':'2')+'"/><path d="M12 23V14h14v9M10 23h18M15 18h2m4 0h2" fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round"/></svg>';}
function opts(l){return{iconLayout:'default#image',iconImageHref:'data:image/svg+xml;charset=UTF-8,'+encodeURIComponent(icon(l)),iconImageSize:[42,52],iconImageOffset:[-21,-52]};}
function send(type,lotId,status){parent.postMessage({type,lotId,status,channel},'*');}
window.bankrotaiLotIds=lots.map(l=>Number(l.id));window.bankrotaiSelectLot=id=>send('bankrotai-select',Number(id));
function convert(coords){if(!Array.isArray(coords))return coords;if(coords.length===2&&typeof coords[0]==='number')return[coords[1],coords[0]];return coords.map(convert);}
function init(){const map=new ymaps.Map('map',{center:[57.6261,39.8845],zoom:7,controls:['zoomControl','typeSelector','fullscreenControl','geolocationControl']});const cluster=new ymaps.Clusterer({preset:'islands#invertedDarkBlueClusterIcons',groupByCoordinates:false,clusterDisableClickZoom:false});const marks=[];lots.forEach(l=>{if(!Number.isFinite(l.lat)||!Number.isFinite(l.lon))return;const mark=new ymaps.Placemark([l.lat,l.lon],{hintContent:esc(l.title)},opts(l));mark.events.add('click',()=>send('bankrotai-select',Number(l.id)));marks.push(mark);if(showCad&&l.geometry&&l.geometry.type==='Polygon')map.geoObjects.add(new ymaps.Polygon(convert(l.geometry.coordinates),{},{strokeColor:'#2468d8',strokeWidth:2,fillColor:'#2468d822'}));});cluster.add(marks);map.geoObjects.add(cluster);if(cad&&Number.isFinite(cad.lat)&&Number.isFinite(cad.lon)){const selected=new ymaps.Placemark([cad.lat,cad.lon],{balloonContent:esc(cad.cadastral_number||cad.address||'Кадастровый объект')},{preset:'islands#violetDotIcon'});map.geoObjects.add(selected);marks.push(selected);if(showCad&&cad.geometry&&cad.geometry.type==='Polygon')map.geoObjects.add(new ymaps.Polygon(convert(cad.geometry.coordinates),{},{strokeColor:'#7c3aed',strokeWidth:3,fillColor:'#7c3aed22'}));}if(marks.length)map.setBounds(map.geoObjects.getBounds(),{checkZoomRange:true,zoomMargin:42});document.getElementById('hint').style.display='none';}
if(window.ymaps){ymaps.ready(init);}else{document.getElementById('hint').textContent='Яндекс.Карты недоступны. Проверьте сеть или блокировщик.';}
</script></body></html>`, [channel, lots, selectedCadastre, selectedLotId, showCadastre]);

  return <iframe ref={frame} className="yandexDesktopFrame" title="Яндекс.Карта лотов" srcDoc={html} sandbox="allow-scripts" />;
}

function SourceButton({ label, url, kind }: { label: string; url: string | null; kind?: string }) {
  const safeUrl = safeExternalUrl(url);
  return <button
    className="mapSourceButton"
    data-kind={kind}
    disabled={!safeUrl}
    onClick={() => safeUrl && window.open(safeUrl, "_blank", "noopener,noreferrer")}
  >{label}{safeUrl && <ExternalLink size={13} />}</button>;
}

function LotPreview({ lot, onClose, onReview }: { lot: MapLot; onClose: () => void; onReview: (status: string) => void }) {
  const images = lot.image_urls.length ? lot.image_urls : lot.image_url ? [lot.image_url] : [];
  const [imageIndex, setImageIndex] = React.useState(0);
  React.useEffect(() => setImageIndex(0), [lot.id]);

  return <aside className="mapLotPreview" aria-label="Карточка выбранного лота">
    <button className="mapPreviewClose" title="Закрыть" onClick={onClose}><X size={21} /></button>
    <div className="mapPreviewMedia">
      {images.length ? <img src={images[imageIndex]} alt="Фотография лота" /> : <div className="mapPreviewPlaceholder">Фотография лота отсутствует</div>}
      {images.length > 1 && <>
        <button className="mapPreviewArrow mapPreviewArrow--left" aria-label="Предыдущее фото" onClick={() => setImageIndex((imageIndex - 1 + images.length) % images.length)}><ChevronLeft /></button>
        <button className="mapPreviewArrow mapPreviewArrow--right" aria-label="Следующее фото" onClick={() => setImageIndex((imageIndex + 1) % images.length)}><ChevronRight /></button>
        <span className="mapPreviewCounter">{imageIndex + 1} / {images.length}</span>
      </>}
    </div>
    <div className="mapPreviewBody">
      <span className="mapPreviewSource">{lot.source_name || lot.source}</span>
      <h2>{lot.title}</h2>
      <p className="mapPreviewDescription">{lot.description || lot.address || "Описание отсутствует"}</p>
      <strong className="mapPreviewPrice">{money(lot.current_price)}</strong>
      <dl className="mapPreviewDetails">
        {lot.address && <><dt>Адрес:</dt><dd>{lot.address}</dd></>}
        {lot.cadastral_number && <><dt>Кадастр:</dt><dd>{lot.cadastral_number}</dd></>}
        {lot.procedure_number && <><dt>Процедура:</dt><dd>{lot.procedure_number}</dd></>}
        {lot.application_deadline && <><dt>Заявки до:</dt><dd>{lot.application_deadline}</dd></>}
        {lot.auction_at && <><dt>Торги:</dt><dd>{lot.auction_at}</dd></>}
      </dl>
      <div className="mapSourceGrid">
        <SourceButton label="Источник" url={lot.source_url} />
        <SourceButton label="ГИС Торги" url={lot.gis_torgi_url} kind="gis" />
        <SourceButton label="ЭТП" url={lot.etp_url} kind="etp" />
        <SourceButton label="Торги России" url={lot.torgi_russia_url} kind="russia" />
      </div>
      <h3 className="mapReviewTitle">Оценка лота</h3>
      <div className="mapReviewButtons">
        <button className={lot.review_status === "approved" ? "active" : ""} data-status="approved" onClick={() => onReview("approved")}><Check />Интересен</button>
        <button className={lot.review_status === "maybe" ? "active" : ""} data-status="maybe" onClick={() => onReview("maybe")}><b>?</b>Сомневаюсь</button>
        <button className={lot.review_status === "rejected" ? "active" : ""} data-status="rejected" onClick={() => onReview("rejected")}><X />Плохой</button>
      </div>
    </div>
  </aside>;
}

export function MapView({ refreshToken }: { refreshToken: number }) {
  const [lots, setLots] = React.useState<MapLot[]>([]);
  const [regions, setRegions] = React.useState<RegionOption[]>([]);
  const [error, setError] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [canSync, setCanSync] = React.useState(false);
  const [cadQuery, setCadQuery] = React.useState("");
  const [cad, setCad] = React.useState<Record<string, unknown> | null>(null);
  const [showCadastre, setShowCadastre] = React.useState(true);
  const [selectedLotId, setSelectedLotId] = React.useState<number | null>(null);
  const [filters, setFilters] = React.useState({ region: "", minPrice: "", maxPrice: "", includeArchived: false });
  const selectedLot = lots.find((lot) => lot.id === selectedLotId) || null;

  const load = React.useCallback(async (region = filters.region, includeArchived = filters.includeArchived) => {
    setLoading(true); setError("");
    try {
      setLots((await fetchMapLots(region || undefined, includeArchived)).items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка загрузки карты");
    } finally {
      setLoading(false);
    }
  }, [filters.includeArchived, filters.region]);

  React.useEffect(() => { void load(); }, [load, refreshToken]);
  React.useEffect(() => {
    Promise.all([fetchCapabilities(), fetchRegions()])
      .then(([capabilities, values]) => { setCanSync(capabilities.region_sync); setRegions(values); })
      .catch(() => setCanSync(false));
  }, []);

  const review = React.useCallback(async (lotId: number, status: string) => {
    try {
      await setReviewStatus(lotId, status);
      setLots((items) => items.map((lot) => lot.id === lotId ? { ...lot, review_status: status } : lot));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить оценку");
    }
  }, []);

  const visibleLots = lots.filter((lot) =>
    (!filters.minPrice || (lot.current_price ?? 0) >= Number(filters.minPrice))
    && (!filters.maxPrice || (lot.current_price ?? Number.POSITIVE_INFINITY) <= Number(filters.maxPrice))
  );
  const cadText = cad ? [
    cad.cadastral_number && `Кадастровый номер: ${String(cad.cadastral_number)}`,
    cad.address && `Адрес: ${String(cad.address)}`,
    cad.area && `Площадь: ${String(cad.area)}`,
    cad.category && `Категория: ${String(cad.category)}`,
  ].filter(Boolean).join("\n") : "Введите кадастровый номер или адрес";

  return <section className="mapDesktopShell">
    <div className="mapDesktopSidebar">
      {selectedLot
        ? <LotPreview lot={selectedLot} onClose={() => setSelectedLotId(null)} onReview={(status) => review(selectedLot.id, status)} />
        : <div className="mapControlPanel">
          <h2>Кадастровый поиск</h2>
          <label className="srOnly" htmlFor="cadastre-map-search">Кадастровый номер или адрес</label>
          <input id="cadastre-map-search" value={cadQuery} onChange={(event) => setCadQuery(event.target.value)} placeholder="Кадастровый номер или адрес" />
          <button disabled={cadQuery.trim().length < 3} onClick={async () => { try { setError(""); setCad(await searchCadastre(cadQuery)); } catch (err) { setError(String(err)); } }}><Search size={14} />Найти объект</button>
          <label className="mapControlCheck"><input type="checkbox" checked={showCadastre} onChange={(event) => setShowCadastre(event.target.checked)} />Показать кадастровые границы</label>
          <button onClick={() => load()}><RefreshCcw size={14} />Обновить метки лотов</button>
          <button className="mapAllRussiaButton" onClick={() => { const next = { ...filters, region: "" }; setFilters(next); void load("", next.includeArchived); }}><MapPin size={15} />Поиск всех лотов РФ</button>

          <fieldset className="mapFiltersBox"><legend>Фильтры лотов</legend>
            <label><span>Цена от</span><input type="number" value={filters.minPrice} onChange={(event) => setFilters({ ...filters, minPrice: event.target.value })} placeholder="Не задано" /></label>
            <label><span>Цена до</span><input type="number" value={filters.maxPrice} onChange={(event) => setFilters({ ...filters, maxPrice: event.target.value })} placeholder="Не задано" /></label>
            <label><span>Регион</span><select value={filters.region} onChange={(event) => setFilters({ ...filters, region: event.target.value })}><option value="">Все регионы</option>{regions.map((region) => <option key={region.code} value={region.code}>{region.name}</option>)}</select></label>
            <label className="mapControlCheck"><input type="checkbox" checked={filters.includeArchived} onChange={(event) => setFilters({ ...filters, includeArchived: event.target.checked })} />Показывать архивные</label>
            <div><button onClick={() => load()}>Применить</button><button onClick={() => { const empty = { region: "", minPrice: "", maxPrice: "", includeArchived: false }; setFilters(empty); void load("", false); }}>Сбросить</button></div>
          </fieldset>
          <pre className="mapCadastreResult">{cadText}</pre>
          {canSync && <button className="mapSyncButton" disabled={!filters.region} onClick={async () => { try { const result = await syncRegion(filters.region, true); setMessage(`Синхронизация: ${result.dispatchMode || result.status}`); } catch (err) { setError(String(err)); } }}>Синхронизировать выбранный регион</button>}
          {loading && <MapState>Обновление меток…</MapState>}
          {message && <MapState>{message}</MapState>}
          {error && <MapState error>{error}</MapState>}
          <small className="mapLotCount">На карте: {visibleLots.length} лотов</small>
        </div>}
    </div>
    <YandexDesktopMap lots={visibleLots} selectedCadastre={cad} showCadastre={showCadastre} selectedLotId={selectedLotId} onSelect={setSelectedLotId} onReview={review} />
  </section>;
}
