import React from "react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  RefreshCcw,
  Search,
  Star,
  X,
} from "lucide-react";

import {
  ApiError,
  clearMapCache,
  fetchCurrentMapDataset,
  fetchCurrentUser,
  fetchMapLotDetail,
  fetchMapLotsSWR,
  fetchMapTile,
  fetchRegions,
  fetchNationwideLotSync,
  fetchOperationsProgress,
  MapLot,
  MapDataset,
  MapMarkerLot,
  MapTileFeature,
  MapTilePayload,
  OperationsProgress,
  RegionOption,
  searchCadastre,
  setReviewStatus,
  splitLot,
  startNationwideLotSync,
} from "../../lib/api";

export const MAP_REDUCED_LIMIT = 500;
// The measured populated tile is small (163 B in the deterministic benchmark),
// while 512 entries preserve useful pan-back history without unbounded growth.
export const MAX_MAP_TILE_CACHE_ENTRIES = 512;

export function mapLimitForZoom(zoom: number) {
  if (zoom <= 7) return 250;
  if (zoom <= 10) return 750;
  return 1500;
}

export function mapBoundsPrecision(zoom: number) {
  if (zoom <= 7) return 1;
  if (zoom < 10) return 2;
  if (zoom < 16) return 3;
  return 4;
}

export function visibleTileCoordinates(bounds: [number, number, number, number], zoom: number) {
  const z = Math.min(14, Math.max(0, Math.floor(zoom)));
  const scale = 2 ** z;
  const tileX = (lon: number) => Math.max(0, Math.min(scale - 1, Math.floor((lon + 180) / 360 * scale)));
  const tileY = (lat: number) => {
    const bounded = Math.max(-85.05112878, Math.min(85.05112878, lat));
    const rad = bounded * Math.PI / 180;
    return Math.max(0, Math.min(scale - 1, Math.floor((1 - Math.asinh(Math.tan(rad)) / Math.PI) / 2 * scale)));
  };
  const [west, south, east, north] = bounds;
  const y0 = tileY(north); const y1 = tileY(south);
  const xRanges = west <= east ? [[tileX(west), tileX(east)]] : [[tileX(west), scale - 1], [0, tileX(east)]];
  const result: Array<{ z: number; x: number; y: number; key: string }> = [];
  for (const [x0, x1] of xRanges) for (let x = x0; x <= x1; x += 1) for (let y = y0; y <= y1; y += 1) {
    result.push({ z, x, y, key: `${z}/${x}/${y}` });
  }
  return result;
}

type TileCoordinate = { z: number; x: number; y: number };

export function mapTileCacheKey(version: string, tile: TileCoordinate) {
  return `${version}:${tile.z}:${tile.x}:${tile.y}`;
}

export function applyMapTileReviewOverrides(
  features: MapTileFeature[],
  overrides: ReadonlyMap<number, string>,
) {
  return features.map((feature) => {
    const status = feature.kind === "lot" ? overrides.get(Number(feature.id)) : undefined;
    return status === undefined ? feature : { ...feature, review_status: status };
  });
}

export function fetchCachedMapTile(
  completed: Map<string, MapTilePayload>,
  inflight: Map<string, Promise<MapTilePayload>>,
  version: string,
  tile: TileCoordinate,
  maxEntries = MAX_MAP_TILE_CACHE_ENTRIES,
) {
  const key = mapTileCacheKey(version, tile);
  const cached = completed.get(key);
  if (cached) {
    completed.delete(key);
    completed.set(key, cached);
    return Promise.resolve(cached);
  }
  const pending = inflight.get(key);
  if (pending) return pending;

  const request = fetchMapTile(version, tile.z, tile.x, tile.y).then((payload) => {
    if (!payload || !Array.isArray(payload.features)) throw new Error(`Некорректный tile ${key}`);
    completed.set(key, payload);
    while (completed.size > maxEntries) {
      const oldest = completed.keys().next().value;
      if (oldest === undefined) break;
      completed.delete(oldest);
    }
    return payload;
  }).finally(() => {
    if (inflight.get(key) === request) inflight.delete(key);
  });
  inflight.set(key, request);
  return request;
}

export function mapObjectCountLabel(total: number, returned: number, exact: boolean) {
  return exact ? `${total} объектов` : `не менее ${returned} объектов в области`;
}

function isTemporaryMapFailure(error: unknown) {
  return error instanceof ApiError && (error.status == null || [502, 503, 504].includes(error.status));
}

export const MAP_SELECTION_SCRIPT = `
function updateSelection(nextId,focus=false){const previous=selectedId;selectedId=nextId==null?null:Number(nextId);[previous,selectedId].forEach(id=>{const lot=lots.find(item=>Number(item.id)===Number(id));if(manager&&lot)manager.objects.setObjectOptions(Number(id),opts(lot));});const selected=lots.find(item=>Number(item.id)===selectedId);if(focus&&selected&&Number.isFinite(selected.lat)&&Number.isFinite(selected.lon))map.setCenter([selected.lat,selected.lon],Math.max(map.getZoom(),16));}
`;

export const formatMoscowDate = (value: string) => {
  const explicitUtc = /[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`;
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(explicitUtc));
};

const money = (value?: number | null) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("ru-RU", {
        style: "currency",
        currency: "RUB",
        maximumFractionDigits: 0,
      }).format(value);

function safeScriptJson(value: unknown) {
  return JSON.stringify(value).split("<").join("\\u003c");
}

function safeExternalUrl(value: string | null) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href
      : null;
  } catch {
    return null;
  }
}

function markerPreview(lot: MapMarkerLot): MapLot {
  return {
    ...lot,
    external_id: String(lot.id),
    description: lot.address || "Полная карточка загружается…",
    cadastral_number: null,
    category: "",
    region: null,
    geometry: null,
    confidence: "",
    source: "",
    source_name: "Лот на карте",
    source_url: null,
    gis_torgi_url: null,
    etp_url: null,
    torgi_russia_url: null,
    image_url: null,
    image_urls: [],
    procedure_number: null,
    application_deadline: null,
    auction_at: null,
    sources: [],
  };
}

function MapState({
  children,
  error = false,
}: {
  children: React.ReactNode;
  error?: boolean;
}) {
  return (
    <div
      className={
        error
          ? "mapDesktopMessage mapDesktopMessage--error"
          : "mapDesktopMessage"
      }
    >
      {children}
    </div>
  );
}

function OperationProgressCard({ value }: { value: OperationsProgress }) {
  const activeSync = value.sync && ["queued", "running"].includes(value.sync.status);
  const activeSources = value.sync?.sources.filter((source) => ["queued", "running"].includes(source.status)) ?? [];
  const completedSources = value.sync?.sources.filter((source) => source.status === "success").length ?? 0;
  const sourceTotal = value.sync?.sources.length ?? 0;
  const batch = value.geocoding.task?.status === "running" ? value.geocoding.task.progress : null;
  if (!activeSync && !batch && value.geocoding.remaining === 0) return null;
  return (
    <section className="mapOperationProgress" aria-label="Ход обработки данных">
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
      <div>
        <strong>Геокодирование — {value.geocoding.percent.toFixed(1)}%</strong>
        <progress max={100} value={value.geocoding.percent} />
        <span>{value.geocoding.geocoded} из {value.geocoding.total} с координатами</span>
        <small>В очереди: {value.geocoding.remaining} · окончательных ошибок: {value.geocoding.terminal_failures}</small>
        {batch && <small>Запросы геокодера: {batch.resolved_queries ?? 0} из {batch.unique_queries ?? batch.queued ?? 0} · из кеша {batch.cache_hits ?? 0}</small>}
        {batch && <small>Текущий пакет: {batch.processed ?? 0} из {batch.queued ?? 0} · успешно {batch.geocoded ?? 0} · ошибок {batch.failed ?? 0}</small>}
      </div>
    </section>
  );
}

function CoincidentLotsPanel({
  lots,
  selectedLotId,
  onSelect,
  onClose,
}: {
  lots: MapMarkerLot[];
  selectedLotId: number | null;
  onSelect: (id: number) => void;
  onClose: () => void;
}) {
  return (
    <section className="coincidentLotsPanel" aria-label="Лоты в выбранной точке">
      <header>
        <div>
          <strong>В этой точке найдено {lots.length} лота</strong>
          <span>Выберите объект для открытия карточки</span>
        </div>
        <button type="button" title="Закрыть список" onClick={onClose}>
          <X size={17} />
        </button>
      </header>
      <div>
        {lots.map((lot, index) => (
          <button
            type="button"
            key={lot.id}
            data-lot-id={lot.id}
            className={lot.id === selectedLotId ? "active" : ""}
            onClick={() => onSelect(lot.id)}
          >
            <span>{index + 1}</span>
            <div>
              <strong>{lot.title}</strong>
              <small>{lot.address || "Адрес не указан"}</small>
            </div>
            <b>{money(lot.current_price)}</b>
          </button>
        ))}
      </div>
    </section>
  );
}

function YandexDesktopMap({
  lots,
  tileEntries,
  reviewMarkerUpdate,
  selectedCadastre,
  showCadastre,
  selectedLotId,
  selectedLotGeometry,
  active,
  onSelect,
  onClusterSelect,
  onViewport,
  onRendered,
}: {
  lots: MapMarkerLot[];
  tileEntries: Array<{ key: string; features: MapTileFeature[] }>;
  reviewMarkerUpdate: { lotId: number; status: string; revision: number } | null;
  selectedCadastre: Record<string, unknown> | null;
  showCadastre: boolean;
  selectedLotId: number | null;
  selectedLotGeometry: GeoJSON.GeoJsonObject | null;
  active: boolean;
  onSelect: (id: number) => void;
  onClusterSelect: (ids: number[]) => void;
  onViewport: (bounds: [number, number, number, number], zoom: number) => void;
  onRendered: (durationMs: number, count: number) => void;
}) {
  const frame = React.useRef<HTMLIFrameElement>(null);
  const channel = "bankrotai-map-v1";
  const [readyRevision, setReadyRevision] = React.useState(0);

  const postCommand = React.useCallback(
    (type: string, payload: Record<string, unknown> = {}) => {
      frame.current?.contentWindow?.postMessage(
        { type, channel, ...payload },
        "*",
      );
    },
    [channel],
  );

  React.useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (
        event.source !== frame.current?.contentWindow ||
        event.data?.channel !== channel
      )
        return;
      if (event.data?.type === "bankrotai-select")
        onSelect(Number(event.data.lotId));
      if (event.data?.type === "bankrotai-cluster-select")
        onClusterSelect(
          Array.isArray(event.data.lotIds)
            ? event.data.lotIds.map(Number).filter(Number.isFinite)
            : [],
        );
      if (event.data?.type === "bankrotai-ready")
        setReadyRevision((value) => value + 1);
      if (event.data?.type === "bankrotai-viewport")
        onViewport(event.data.bounds, Number(event.data.zoom));
      if (event.data?.type === "bankrotai-rendered")
        onRendered(Number(event.data.durationMs), Number(event.data.count));
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [channel, onClusterSelect, onRendered, onSelect, onViewport]);

  React.useEffect(() => {
    if (readyRevision) postCommand("replace-lots", { lots });
  }, [lots, postCommand, readyRevision]);
  React.useEffect(() => {
    if (readyRevision) postCommand("sync-tiles", { entries: tileEntries });
  }, [postCommand, readyRevision, tileEntries]);
  React.useEffect(() => {
    if (readyRevision && reviewMarkerUpdate) postCommand("update-lot-review", reviewMarkerUpdate);
  }, [postCommand, readyRevision, reviewMarkerUpdate]);
  React.useEffect(() => {
    if (readyRevision) postCommand("select-lot", { lotId: selectedLotId });
  }, [postCommand, readyRevision, selectedLotId]);
  React.useEffect(() => {
    if (readyRevision)
      postCommand("toggle-cadastre", { enabled: showCadastre });
  }, [postCommand, readyRevision, showCadastre]);
  React.useEffect(() => {
    if (readyRevision)
      postCommand("show-cadastre-result", { value: selectedCadastre });
  }, [postCommand, readyRevision, selectedCadastre]);
  React.useEffect(() => {
    if (readyRevision)
      postCommand("show-selected-geometry", { value: selectedLotGeometry });
  }, [postCommand, readyRevision, selectedLotGeometry]);
  React.useEffect(() => {
    if (readyRevision && active) postCommand("resume");
  }, [active, postCommand, readyRevision]);

  const html = React.useMemo(
    () => `<!doctype html><html><head><meta charset="utf-8"><script src="https://api-maps.yandex.ru/2.1.77/?lang=ru_RU&amp;csp=true"></script><style>
html,body,#map{height:100%;margin:0}body{font:13px Arial,sans-serif;overflow:hidden}.hint{position:absolute;z-index:5;left:12px;top:12px;background:#fff;border:1px solid #cbd2dc;border-radius:4px;padding:9px 12px;color:#42526b;box-shadow:0 2px 8px #0002}
</style></head><body><div id="map"></div><div id="hint" class="hint">Загрузка Яндекс.Карт…</div><script>
const channel=${safeScriptJson(channel)};const instanceId=(crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random());let map=null;let manager=null;let legacyManager=null;let tileManager=null;let lots=[];let mode='legacy';const tileObjects=new Map();const tileLots=new Map();let cad=null;let selectedGeometry=null;let showCad=true;let selectedId=null;let pending=[];let overlayObjects=[];let viewportTimer=null;
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));}
function ended(l){return l.is_archived||['closed','completed','cancelled','canceled','failed','annulled','archive','archived'].includes(String(l.status||'').toLowerCase());}
function color(l){if(ended(l))return '#111111';return l.review_status==='approved'?'#24a269':l.review_status==='maybe'?'#e0aa16':l.review_status==='rejected'?'#d94b4b':'#7d8795';}
function icon(l){const c=color(l),selected=Number(l.id)===selectedId;return '<svg xmlns="http://www.w3.org/2000/svg" width="42" height="52" viewBox="0 0 38 48"><path d="M19 1C9.1 1 1 9.1 1 19c0 13.2 18 28 18 28s18-14.8 18-28C37 9.1 28.9 1 19 1z" fill="'+c+'" stroke="'+(selected?'#f1a800':'white')+'" stroke-width="'+(selected?'3':'2')+'"/><path d="M12 23V14h14v9M10 23h18M15 18h2m4 0h2" fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round"/></svg>';}
function opts(l){return{iconLayout:'default#image',iconImageHref:'data:image/svg+xml;charset=UTF-8,'+encodeURIComponent(icon(l)),iconImageSize:[42,52],iconImageOffset:[-21,-52]};}
function send(type,payload={}){parent.postMessage({type,channel,...payload},'*');}
function convert(coords){if(!Array.isArray(coords))return coords;if(coords.length===2&&typeof coords[0]==='number')return[coords[1],coords[0]];return coords.map(convert);}
function feature(l){const cluster=l.kind==='cluster';return{type:'Feature',id:cluster?String(l.id):Number(l.id),geometry:{type:'Point',coordinates:[l.lat,l.lon]},properties:{hintContent:cluster?esc(l.count+' лотов'):esc(l.title),iconContent:cluster?String(l.count):undefined,lotId:cluster?undefined:Number(l.id),kind:l.kind||'lot',bounds:l.bounds},options:cluster?{preset:'islands#blueCircleIcon'}:opts(l)};}
function clusterSelection(clusterId,objects){const entries=Array.isArray(objects)?objects:[];const lotIds=entries.map(item=>Number(item.id??item.properties?.lotId)).filter(Number.isFinite);const coords=entries.map(item=>item.geometry?.coordinates).filter(value=>Array.isArray(value)&&value.length===2);const samePoint=coords.length>1&&coords.every(value=>Math.abs(Number(value[0])-Number(coords[0][0]))<1e-9&&Math.abs(Number(value[1])-Number(coords[0][1]))<1e-9);if(lotIds.length>1&&(samePoint||map.getZoom()>=18)){send('bankrotai-cluster-select',{clusterId,lotIds,zoom:map.getZoom()});return true;}return false;}
function clearOverlays(){overlayObjects.forEach(item=>map.geoObjects.remove(item));overlayObjects=[];}
function activateManager(next){if(manager===next)return;if(manager)map.geoObjects.remove(manager);manager=next;map.geoObjects.add(manager);}
function addGeometry(value,style){if(!value||!value.type||!value.coordinates)return;const sets=value.type==='MultiPolygon'?value.coordinates:[value.coordinates];sets.forEach(coords=>{const polygon=new ymaps.Polygon(convert(coords),{},style);map.geoObjects.add(polygon);overlayObjects.push(polygon);});}
function renderOverlays(focus=false){if(!map)return;clearOverlays();if(cad&&Number.isFinite(cad.lat)&&Number.isFinite(cad.lon)){const point=new ymaps.Placemark([cad.lat,cad.lon],{balloonContent:esc(cad.cadastral_number||cad.address||'Кадастровый объект')},{preset:'islands#violetDotIcon'});map.geoObjects.add(point);overlayObjects.push(point);if(showCad&&cad.geometry)addGeometry(cad.geometry,{strokeColor:'#7c3aed',strokeWidth:3,fillColor:'#7c3aed22'});if(focus)map.setCenter([cad.lat,cad.lon],Math.max(map.getZoom(),16));}if(showCad&&selectedGeometry)addGeometry(selectedGeometry,{strokeColor:'#2468d8',strokeWidth:3,fillColor:'#2468d822'});}
${MAP_SELECTION_SCRIPT}
function renderLots(){if(!map||!legacyManager)return;const started=performance.now();activateManager(legacyManager);manager.removeAll();manager.add({type:'FeatureCollection',features:lots.filter(l=>Number.isFinite(l.lat)&&Number.isFinite(l.lon)).map(feature)});if(selectedId!=null)updateSelection(selectedId);requestAnimationFrame(()=>send('bankrotai-rendered',{durationMs:performance.now()-started,count:lots.length}));}
function syncTiles(entries){if(!map||!tileManager)return;const started=performance.now();if(!entries.length){if(mode==='tiles'){tileManager.removeAll();tileObjects.clear();tileLots.clear();mode='legacy';renderLots();}return;}if(mode!=='tiles'){activateManager(tileManager);tileManager.removeAll();tileObjects.clear();tileLots.clear();mode='tiles';}const wanted=new Set(entries.map(entry=>entry.key));for(const[key,ids]of tileObjects){if(!wanted.has(key)){tileManager.remove(ids);ids.forEach(id=>tileLots.delete(Number(id)));tileObjects.delete(key);}}for(const entry of entries){if(tileObjects.has(entry.key))continue;const raw=entry.features||[];const features=raw.map(feature);tileManager.add({type:'FeatureCollection',features});raw.forEach(item=>{if(item.kind==='lot')tileLots.set(Number(item.id),item);});tileObjects.set(entry.key,features.map(item=>item.id));}requestAnimationFrame(()=>send('bankrotai-rendered',{durationMs:performance.now()-started,count:[...tileObjects.values()].reduce((n,ids)=>n+ids.length,0)}));}
function updateTileReview(lotId,status){if(!tileManager)return false;const id=Number(lotId),lot=tileLots.get(id);if(!lot)return false;const updated={...lot,review_status:status};tileLots.set(id,updated);tileManager.objects.setObjectOptions(id,opts(updated));return true;}
function emitViewport(){if(!map)return;const bounds=map.getBounds();send('bankrotai-viewport',{bounds:[bounds[0][1],bounds[0][0],bounds[1][1],bounds[1][0]],zoom:map.getZoom()});}
function scheduleViewport(){clearTimeout(viewportTimer);viewportTimer=setTimeout(emitViewport,250);}
function command(data){if(!map){pending.push(data);return;}if(data.type==='replace-lots'){lots=Array.isArray(data.lots)?data.lots:[];if(mode!=='tiles')renderLots();}else if(data.type==='sync-tiles'){syncTiles(Array.isArray(data.entries)?data.entries:[]);}else if(data.type==='update-lot-review'){updateTileReview(data.lotId,data.status);}else if(data.type==='select-lot'){updateSelection(data.lotId,true);}else if(data.type==='toggle-cadastre'){showCad=Boolean(data.enabled);renderOverlays(false);}else if(data.type==='show-cadastre-result'){cad=data.value||null;renderOverlays(Boolean(cad));}else if(data.type==='show-selected-geometry'){selectedGeometry=data.value||null;renderOverlays(false);}else if(data.type==='resume'){map.container.fitToViewport();scheduleViewport();}}
window.addEventListener('message',event=>{if(event.source!==parent||event.data?.channel!==channel)return;command(event.data);});
function init(){map=new ymaps.Map('map',{center:[57.6261,39.8845],zoom:7,controls:['zoomControl','typeSelector','fullscreenControl','geolocationControl']});legacyManager=new ymaps.ObjectManager({clusterize:true,gridSize:64,clusterDisableClickZoom:false});legacyManager.clusters.options.set({preset:'islands#darkBlueClusterIcons'});legacyManager.objects.events.add('click',event=>send('bankrotai-select',{lotId:Number(event.get('objectId'))}));legacyManager.clusters.events.add('click',event=>{const clusterId=event.get('objectId');const cluster=legacyManager.clusters.getById(clusterId);if(clusterSelection(clusterId,cluster?.properties?.geoObjects))event.preventDefault?.();});tileManager=new ymaps.ObjectManager({clusterize:false});tileManager.objects.events.add('click',event=>{const id=event.get('objectId'),object=tileManager.objects.getById(id);if(object?.properties?.kind==='cluster'&&Array.isArray(object.properties.bounds)){const b=object.properties.bounds;map.setBounds([[b[1],b[0]],[b[3],b[2]]],{checkZoomRange:true,zoomMargin:24});return;}send('bankrotai-select',{lotId:Number(id)});});manager=legacyManager;map.geoObjects.add(manager);map.events.add('boundschange',scheduleViewport);window.bankrotaiDebug={instanceId,getViewport:()=>({center:map.getCenter(),zoom:map.getZoom(),instanceId}),setViewport:(center,zoom)=>map.setCenter(center,zoom),getLotReview:id=>tileLots.get(Number(id))?.review_status??lots.find(l=>Number(l.id)===Number(id))?.review_status??null,clickObject:id=>manager.objects.events.fire('click',{objectId:id}),clickCoincident:ids=>clusterSelection('debug',lots.filter(l=>ids.map(Number).includes(Number(l.id))).map(feature)),getObjectCount:()=>manager.objects.getLength()};pending.splice(0).forEach(command);document.getElementById('hint').style.display='none';send('bankrotai-ready');scheduleViewport();}
if(window.ymaps){ymaps.ready(init);}else{document.getElementById('hint').textContent='Яндекс.Карты недоступны. Проверьте сеть или блокировщик.';}
</script></body></html>`,
    [channel],
  );

  return (
    <iframe
      ref={frame}
      className="yandexDesktopFrame"
      title="Яндекс.Карта лотов"
      srcDoc={html}
      sandbox="allow-scripts"
    />
  );
}

function SourceButton({
  label,
  url,
  kind,
}: {
  label: string;
  url: string | null;
  kind?: string;
}) {
  const safeUrl = safeExternalUrl(url);
  return (
    <button
      className="mapSourceButton"
      data-kind={kind}
      disabled={!safeUrl}
      onClick={() =>
        safeUrl && window.open(safeUrl, "_blank", "noopener,noreferrer")
      }
    >
      {label}
      {safeUrl && <ExternalLink size={13} />}
    </button>
  );
}

function LotPreview({
  lot,
  isAdmin,
  onClose,
  onReview,
  onSplit,
  detailLoading,
  detailError,
}: {
  lot: MapLot;
  isAdmin: boolean;
  onClose: () => void;
  onReview: (status: string) => void;
  onSplit: (processedLotId: number) => void;
  detailLoading: boolean;
  detailError: string;
}) {
  const images = lot.image_urls.length
    ? lot.image_urls
    : lot.image_url
      ? [lot.image_url]
      : [];
  const [imageIndex, setImageIndex] = React.useState(0);
  React.useEffect(() => setImageIndex(0), [lot.id]);

  return (
    <aside className="mapLotPreview" aria-label="Карточка выбранного лота">
      <button className="mapPreviewClose" title="Закрыть" onClick={onClose}>
        <X size={21} />
      </button>
      <div className="mapPreviewMedia">
        {images.length ? (
          <img src={images[imageIndex]} alt="Фотография лота" />
        ) : (
          <div className="mapPreviewPlaceholder">
            Фотография лота отсутствует
          </div>
        )}
        {images.length > 1 && (
          <>
            <button
              className="mapPreviewArrow mapPreviewArrow--left"
              aria-label="Предыдущее фото"
              onClick={() =>
                setImageIndex((imageIndex - 1 + images.length) % images.length)
              }
            >
              <ChevronLeft />
            </button>
            <button
              className="mapPreviewArrow mapPreviewArrow--right"
              aria-label="Следующее фото"
              onClick={() => setImageIndex((imageIndex + 1) % images.length)}
            >
              <ChevronRight />
            </button>
            <span className="mapPreviewCounter">
              {imageIndex + 1} / {images.length}
            </span>
          </>
        )}
      </div>
      <div className="mapPreviewBody">
        <span className="mapPreviewSource">
          {lot.source_name || lot.source}
        </span>
        <h2>{lot.title}</h2>
        <p className="mapPreviewDescription">
          {lot.description || lot.address || "Описание отсутствует"}
        </p>
        <strong className="mapPreviewPrice">{money(lot.current_price)}</strong>
        <dl className="mapPreviewDetails">
          {lot.address && (
            <>
              <dt>Адрес:</dt>
              <dd>{lot.address}</dd>
            </>
          )}
          {lot.cadastral_number && (
            <>
              <dt>Кадастр:</dt>
              <dd>{lot.cadastral_number}</dd>
            </>
          )}
          {lot.procedure_number && (
            <>
              <dt>Процедура:</dt>
              <dd>{lot.procedure_number}</dd>
            </>
          )}
          {lot.application_deadline && (
            <>
              <dt>Заявки до:</dt>
              <dd>{formatMoscowDate(lot.application_deadline)} МСК</dd>
            </>
          )}
          {lot.auction_at && (
            <>
              <dt>Торги:</dt>
              <dd>{formatMoscowDate(lot.auction_at)} МСК</dd>
            </>
          )}
        </dl>
        <div className="mapSourceGrid">
          <SourceButton label="Источник" url={lot.source_url} />
          <SourceButton label="ГИС Торги" url={lot.gis_torgi_url} kind="gis" />
          <SourceButton label="ЭТП" url={lot.etp_url} kind="etp" />
          <SourceButton
            label="Торги РФ"
            url={lot.torgi_russia_url}
            kind="russia"
          />
        </div>
        {detailLoading && <MapState>Загрузка полной карточки…</MapState>}
        {detailError && <MapState error>{detailError}</MapState>}
        <details className="mapPublications" open={lot.sources.length > 1}>
          <summary>Объединено публикаций: {lot.sources.length}</summary>
          <div>
            {lot.sources.map((source) => (
              <article key={`${source.processed_lot_id}-${source.external_id}`}>
                <div>
                  <strong>{source.source_system}</strong>
                  <span>Лот №{source.external_id}</span>
                  <small>
                    {source.title} · {money(source.price)}
                  </small>
                </div>
                <div className="mapPublicationActions">
                  <SourceButton label="Открыть" url={source.url} />
                  {isAdmin && !source.is_primary && (
                    <button
                      className="mapSplitButton"
                      onClick={() => onSplit(source.processed_lot_id)}
                    >
                      Ошибочно объединены — разделить
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        </details>
        <h3 className="mapReviewTitle">Оценка лота</h3>
        <div className="mapReviewButtons">
          <button
            className={lot.review_status === "approved" ? "active" : ""}
            data-status="approved"
            onClick={() => onReview("approved")}
          >
            <Check />
            Интересен
          </button>
          <button
            className={lot.review_status === "maybe" ? "active" : ""}
            data-status="maybe"
            onClick={() => onReview("maybe")}
          >
            <b>?</b>Сомневаюсь
          </button>
          <button
            className={lot.review_status === "rejected" ? "active" : ""}
            data-status="rejected"
            onClick={() => onReview("rejected")}
          >
            <X />
            Плохой
          </button>
        </div>
      </div>
    </aside>
  );
}

function relativeUpdate(value: string | null, now: number) {
  if (!value) return "время обновления неизвестно";
  const elapsed = Math.max(0, now - new Date(value).getTime());
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 1) return "обновлено только что";
  if (minutes < 60) return `обновлено ${minutes} мин назад`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `обновлено ${hours} ч назад`;
  return `обновлено ${Math.floor(hours / 24)} дн назад`;
}

export function MapView({
  refreshToken,
  favoritesOnly = false,
  active = true,
  onFavoriteCount,
  statusContent,
}: {
  refreshToken: number;
  favoritesOnly?: boolean;
  active?: boolean;
  onFavoriteCount?: (count: number) => void;
  statusContent?: React.ReactNode;
}) {
  const [lots, setLots] = React.useState<MapMarkerLot[]>([]);
  const [selectedLot, setSelectedLot] = React.useState<MapLot | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [detailError, setDetailError] = React.useState("");
  const [viewport, setViewport] = React.useState<[number, number, number, number] | null>(null);
  const [viewportZoom, setViewportZoom] = React.useState(7);
  const [mapDataset, setMapDataset] = React.useState<MapDataset | null>(null);
  const [mapDatasetStatus, setMapDatasetStatus] = React.useState<"loading" | "ready" | "unavailable">("loading");
  const [tileEntries, setTileEntries] = React.useState<Array<{ key: string; features: MapTileFeature[] }>>([]);
  const [reviewMarkerUpdate, setReviewMarkerUpdate] = React.useState<{ lotId: number; status: string; revision: number } | null>(null);
  const [viewportLimit, setViewportLimit] = React.useState(250);
  const requestRevision = React.useRef(0);
  const datasetRequestRevision = React.useRef(0);
  const tileRequestRevision = React.useRef(0);
  const completedTileCache = React.useRef(new Map<string, MapTilePayload>());
  const inflightTileRequests = React.useRef(new Map<string, Promise<MapTilePayload>>());
  const visibleTileSetSignature = React.useRef<string | null>(null);
  const requestController = React.useRef<AbortController | null>(null);
  const reviewOverrides = React.useRef(new Map<number, string>());
  const hasRenderedLots = React.useRef(false);
  const [statistics, setStatistics] = React.useState({
    total: 0,
    mapped: 0,
    withoutCoordinates: 0,
    returned: 0,
    limit: 0,
    truncated: false,
    updatedAt: null as string | null,
    exact: true,
  });
  const [timings, setTimings] = React.useState({ api: 0, server: 0, render: 0, cached: false });
  const [clock, setClock] = React.useState(Date.now());
  const [regions, setRegions] = React.useState<RegionOption[]>([]);
  const [error, setError] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [mapNotice, setMapNotice] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [isAdmin, setIsAdmin] = React.useState(false);
  const [cadQuery, setCadQuery] = React.useState("");
  const [cad, setCad] = React.useState<Record<string, unknown> | null>(null);
  const [showCadastre, setShowCadastre] = React.useState(true);
  const [selectedLotId, setSelectedLotId] = React.useState<number | null>(null);
  const [coincidentLotIds, setCoincidentLotIds] = React.useState<number[]>([]);
  const [filters, setFilters] = React.useState({
    region: "",
    minPrice: "",
    maxPrice: "",
  });
  const [appliedFilters, setAppliedFilters] = React.useState(filters);
  const [syncing, setSyncing] = React.useState(false);
  const [operationProgress, setOperationProgress] = React.useState<OperationsProgress | null>(null);
  const tileMode = !favoritesOnly && !appliedFilters.region && !appliedFilters.minPrice && !appliedFilters.maxPrice;
  const visibleMapObjects = tileMode && mapDataset
    ? tileEntries.reduce((count, entry) => count + entry.features.length, 0)
    : lots.length;

  const applyResponse = React.useCallback((response: Awaited<ReturnType<typeof fetchMapLotsSWR>>["data"], cached: boolean, apiMs = 0) => {
    hasRenderedLots.current = true;
    setLots(
      response.items.map((lot) => ({
        ...lot,
        review_status: reviewOverrides.current.get(lot.id) ?? lot.review_status,
      })),
    );
    setStatistics({
      total: response.total,
      mapped: response.mapped_total ?? response.items.length,
      withoutCoordinates:
        response.without_coordinates ??
        Math.max(response.total - response.items.length, 0),
      returned: response.returned ?? response.items.length,
      limit: response.limit ?? response.items.length,
      truncated: response.truncated ?? false,
      updatedAt: response.updated_at,
      exact: response.statistics_exact !== false,
    });
    setTimings((value) => ({
      ...value,
      api: apiMs,
      server: response.timings?.server_ms ?? 0,
      cached,
    }));
  }, []);

  const load = React.useCallback(
    async (
      applied = appliedFilters,
    ) => {
      if (tileMode) return;
      if (!favoritesOnly && !viewport) return;
      const revision = ++requestRevision.current;
      requestController.current?.abort();
      const controller = new AbortController();
      requestController.current = controller;
      setLoading(true);
      setError("");
      setMapNotice("");
      try {
        const query = favoritesOnly
          ? { region_code: applied.region || undefined, review_status: "approved" as const }
          : {
              region_code: applied.region || undefined,
              min_start_price: applied.minPrice ? Number(applied.minPrice) : undefined,
              max_start_price: applied.maxPrice ? Number(applied.maxPrice) : undefined,
              west: viewport?.[0],
              south: viewport?.[1],
              east: viewport?.[2],
              north: viewport?.[3],
              limit: viewportLimit,
            };
        let usedReducedLimit = false;
        let result: Awaited<ReturnType<typeof fetchMapLotsSWR>>;
        try {
          result = await fetchMapLotsSWR(query, (cached) => {
            if (revision === requestRevision.current) applyResponse(cached, true);
          }, controller.signal, 1);
        } catch (firstError) {
          if (favoritesOnly || !isTemporaryMapFailure(firstError)) throw firstError;
          usedReducedLimit = true;
          result = await fetchMapLotsSWR({ ...query, limit: MAP_REDUCED_LIMIT }, (cached) => {
            if (revision === requestRevision.current) applyResponse(cached, true);
          }, controller.signal, 1);
        }
        if (revision === requestRevision.current) {
          applyResponse(result.data, result.fromCache, result.networkMs);
          setMapNotice(usedReducedLimit
            ? "Карта загружена в облегчённом режиме. Приблизьте область для показа дополнительных лотов."
            : "");
        }
      } catch (err) {
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          if (hasRenderedLots.current) {
            setMapNotice("Не удалось обновить область. Показаны ранее загруженные лоты.");
          } else {
            setError(err instanceof Error ? err.message : "Ошибка загрузки карты");
          }
        }
      } finally {
        if (revision === requestRevision.current) setLoading(false);
      }
    },
    [appliedFilters, applyResponse, favoritesOnly, tileMode, viewport, viewportLimit],
  );

  const loadCurrentMapDataset = React.useCallback(async () => {
    const revision = ++datasetRequestRevision.current;
    setMapDatasetStatus("loading");
    try {
      const dataset = await fetchCurrentMapDataset();
      if (revision !== datasetRequestRevision.current) return;
      setMapDataset(dataset);
      setMapDatasetStatus("ready");
      setError("");
    } catch {
      if (revision !== datasetRequestRevision.current) return;
      setMapDataset(null);
      setMapDatasetStatus("unavailable");
      setError("Актуальный набор данных карты пока недоступен");
    }
  }, []);

  React.useEffect(() => {
    if (active) void load();
  }, [active, load, refreshToken]);
  React.useEffect(() => {
    if (!tileMode) return;
    requestRevision.current += 1;
    requestController.current?.abort();
    setLoading(false);
  }, [tileMode]);
  React.useEffect(() => () => requestController.current?.abort(), []);
  React.useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  React.useEffect(() => {
    if (favoritesOnly) setSelectedLotId(null);
  }, [favoritesOnly]);
  React.useEffect(() => {
    if (selectedLotId == null) {
      setSelectedLot(null);
      setDetailLoading(false);
      setDetailError("");
      return;
    }
    setDetailLoading(true);
    setDetailError("");
    let cancelled = false;
    fetchMapLotDetail(selectedLotId)
      .then((value) => { if (!cancelled) setSelectedLot(value); })
      .catch((err) => {
        if (!cancelled)
          setDetailError(
            err instanceof Error
              ? `Не удалось загрузить полную карточку: ${err.message}`
              : "Не удалось загрузить полную карточку",
          );
      })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [selectedLotId]);
  React.useEffect(() => {
    Promise.all([fetchRegions(), fetchCurrentUser()])
      .then(([values, user]) => {
        setRegions(values);
        setIsAdmin(user.role === "admin");
      })
      .catch(() => setIsAdmin(false));
  }, []);
  React.useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const refresh = () => fetchOperationsProgress()
      .then((value) => { if (!cancelled) setOperationProgress(value); })
      .catch(() => undefined);
    void refresh();
    const timer = window.setInterval(refresh, 10_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [active]);
  React.useEffect(() => {
    void loadCurrentMapDataset();
    return () => { datasetRequestRevision.current += 1; };
  }, [loadCurrentMapDataset, refreshToken]);
  React.useEffect(() => {
    if (!active || !tileMode || !mapDataset || !viewport) {
      tileRequestRevision.current += 1;
      visibleTileSetSignature.current = null;
      setTileEntries([]);
      return;
    }
    const coordinates = visibleTileCoordinates(viewport, viewportZoom);
    const signature = `${mapDataset.version}|${coordinates.map((tile) => tile.key).sort().join("|")}`;
    if (visibleTileSetSignature.current === signature) return;
    visibleTileSetSignature.current = signature;
    const revision = ++tileRequestRevision.current;
    setLoading(true);
    Promise.all(coordinates.map(async (tile) => ({
      key: `${mapDataset.version}/${tile.key}`,
      features: applyMapTileReviewOverrides(
        (await fetchCachedMapTile(
          completedTileCache.current,
          inflightTileRequests.current,
          mapDataset.version,
          tile,
        )).features,
        reviewOverrides.current,
      ),
    }))).then((entries) => {
      if (revision !== tileRequestRevision.current) return;
      setTileEntries(entries);
      setStatistics((value) => ({
        ...value, total: mapDataset.point_count, mapped: mapDataset.point_count,
        returned: entries.reduce((count, entry) => count + entry.features.length, 0),
        truncated: false, updatedAt: mapDataset.published_at, exact: true,
      }));
      setError("");
    }).catch((err) => {
      if (revision !== tileRequestRevision.current) return;
      visibleTileSetSignature.current = null;
      if (!(err instanceof DOMException && err.name === "AbortError")) setError("Не удалось загрузить тайлы карты");
    }).finally(() => {
      if (revision === tileRequestRevision.current) setLoading(false);
    });
  }, [active, mapDataset, tileMode, viewport, viewportZoom]);
  React.useEffect(() => () => { tileRequestRevision.current += 1; }, []);

  const review = React.useCallback(async (lotId: number, status: string) => {
    try {
      await setReviewStatus(lotId, status);
      reviewOverrides.current.set(lotId, status);
      if (reviewOverrides.current.size > 500) {
        const oldest = reviewOverrides.current.keys().next().value;
        if (oldest !== undefined) reviewOverrides.current.delete(oldest);
      }
      setLots((items) =>
        items.map((lot) =>
          lot.id === lotId ? { ...lot, review_status: status } : lot,
        ),
      );
      setReviewMarkerUpdate((value) => ({ lotId, status, revision: (value?.revision ?? 0) + 1 }));
      setSelectedLot((lot) => lot?.id === lotId ? { ...lot, review_status: status } : lot);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Не удалось сохранить оценку",
      );
    }
  }, []);

  const visibleLots = lots;
  const favoriteLots = visibleLots.filter(
    (lot) => lot.review_status === "approved",
  );
  React.useEffect(() => {
    onFavoriteCount?.(
      favoritesOnly && statistics.exact ? statistics.total : favoriteLots.length,
    );
  }, [favoriteLots.length, favoritesOnly, onFavoriteCount, statistics.exact, statistics.total]);
  const cadText = cad
    ? [
        cad.cadastral_number &&
          `Кадастровый номер: ${String(cad.cadastral_number)}`,
        cad.address && `Адрес: ${String(cad.address)}`,
        cad.area && `Площадь: ${String(cad.area)}`,
        cad.category && `Категория: ${String(cad.category)}`,
      ]
        .filter(Boolean)
        .join("\n")
    : "Введите кадастровый номер или адрес";

  const handleViewport = React.useCallback(
    (bounds: [number, number, number, number], zoom: number) => {
      const [west, south, east, north] = bounds;
      const lonPadding = Math.max((east - west) * 0.15, 0.02);
      const latPadding = Math.max((north - south) * 0.15, 0.02);
      const precision = mapBoundsPrecision(zoom);
      const next: [number, number, number, number] = [
        Math.max(-180, Number((west - lonPadding).toFixed(precision))),
        Math.max(-90, Number((south - latPadding).toFixed(precision))),
        Math.min(180, Number((east + lonPadding).toFixed(precision))),
        Math.min(90, Number((north + latPadding).toFixed(precision))),
      ];
      setViewport((current) =>
        current?.every((value, index) => value === next[index]) ? current : next,
      );
      setViewportLimit(mapLimitForZoom(zoom));
      setViewportZoom(zoom);
    },
    [],
  );

  const selectLot = React.useCallback(
    (lotId: number) => {
      const marker = lots.find((lot) => lot.id === lotId);
      if (marker) setSelectedLot(markerPreview(marker));
      setSelectedLotId(lotId);
    },
    [lots],
  );
  const coincidentLots = coincidentLotIds
    .map((id) => lots.find((lot) => lot.id === id))
    .filter((lot): lot is MapMarkerLot => Boolean(lot));

  const refreshCatalogue = React.useCallback(async () => {
    setSyncing(true);
    setError("");
    try {
      const started = await startNationwideLotSync();
      setMessage("Обновление каталога запущено. Текущие метки остаются доступны.");
      for (;;) {
        await new Promise((resolve) => window.setTimeout(resolve, 3000));
        const status = await fetchNationwideLotSync(started.task_id);
        void fetchOperationsProgress().then(setOperationProgress).catch(() => undefined);
        const sourceProgress = status.sources?.map((source) => `${source.source_system}: ${source.items_seen}`).join(" · ");
        setMessage(`Обновление лотов${sourceProgress ? ` · ${sourceProgress}` : "…"}`);
        if (["success", "failed", "partial"].includes(status.status)) {
          if (status.status === "failed") throw new Error("Обновление источников не выполнено");
          await clearMapCache();
          setMessage(status.status === "success" ? "Каталог обновлён" : "Каталог обновлён частично; прежние данные недоступного источника сохранены");
          await load();
          break;
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось обновить каталог");
    } finally {
      setSyncing(false);
    }
  }, [load]);

  return (
    <section className="mapDesktopShell">
      <div className="mapDesktopSidebar">
        {selectedLot ? (
          <>
          {coincidentLots.length > 1 && (
            <CoincidentLotsPanel
              lots={coincidentLots}
              selectedLotId={selectedLotId}
              onSelect={selectLot}
              onClose={() => setCoincidentLotIds([])}
            />
          )}
          <LotPreview
            lot={selectedLot}
            isAdmin={isAdmin}
            onClose={() => setSelectedLotId(null)}
            onReview={(status) => review(selectedLot.id, status)}
            detailLoading={detailLoading}
            detailError={detailError}
            onSplit={async (processedLotId) => {
              try {
                await splitLot(
                  processedLotId,
                  "Ошибочное объединение исправлено из карты",
                );
                setMessage(`Публикация ${processedLotId} отделена`);
                await load();
              } catch (err) {
                setError(String(err));
              }
            }}
          />
          </>
        ) : coincidentLots.length > 1 ? (
          <CoincidentLotsPanel
            lots={coincidentLots}
            selectedLotId={selectedLotId}
            onSelect={selectLot}
            onClose={() => setCoincidentLotIds([])}
          />
        ) : favoritesOnly ? (
          <section className="mapFavoritesPanel" aria-label="Интересные лоты">
            <header>
              <Star size={18} fill="currentColor" />
              <div>
                <h2>Интересные лоты</h2>
                <span>{favoriteLots.length} зелёных</span>
              </div>
            </header>
            {favoriteLots.length ? (
              <div className="mapFavoritesList">
                {favoriteLots.map((lot) => (
                  <button key={lot.id} onClick={() => selectLot(lot.id)}>
                    <strong>{lot.title}</strong>
                    <span>{lot.address || "Адрес не указан"}</span>
                    <b>{money(lot.current_price)}</b>
                  </button>
                ))}
              </div>
            ) : (
              <MapState>
                Зелёных лотов пока нет. Отметьте интересный лот на карте.
              </MapState>
            )}
          </section>
        ) : (
          <div className="mapControlPanel">
            <h2>Лоты недвижимости</h2>
            <fieldset className="mapFiltersBox">
              <legend>Фильтры</legend>
              <label>
                <span>Стартовая цена от</span>
                <input
                  type="number"
                  value={filters.minPrice}
                  onChange={(event) =>
                    setFilters({ ...filters, minPrice: event.target.value })
                  }
                  placeholder="Не задано"
                />
              </label>
              <label>
                <span>Стартовая цена до</span>
                <input
                  type="number"
                  value={filters.maxPrice}
                  onChange={(event) =>
                    setFilters({ ...filters, maxPrice: event.target.value })
                  }
                  placeholder="Не задано"
                />
              </label>
              <label>
                <span>Субъект РФ</span>
                <select
                  value={filters.region}
                  onChange={(event) =>
                    setFilters({ ...filters, region: event.target.value })
                  }
                >
                  <option value="">Все регионы</option>
                  {regions.map((region) => (
                    <option key={region.code} value={region.code}>
                      {region.code} — {region.name}
                    </option>
                  ))}
                </select>
              </label>
              <div>
                <button onClick={() => setAppliedFilters(filters)}>Применить</button>
                <button
                  onClick={() => {
                    const empty = { region: "", minPrice: "", maxPrice: "" };
                    setFilters(empty);
                    setAppliedFilters(empty);
                  }}
                >
                  Сбросить
                </button>
              </div>
            </fieldset>
            {operationProgress && <OperationProgressCard value={operationProgress} />}
            {loading && <MapState>Обновление меток…</MapState>}
            {tileMode && mapDatasetStatus === "loading" && <MapState>Загрузка карты…</MapState>}
            {mapNotice && <MapState>{mapNotice}</MapState>}
            {message && <MapState>{message}</MapState>}
            {error && <MapState error>{error}</MapState>}
            <small className="mapLotCount">
              На карте: {visibleMapObjects} объектов слоя
            </small>
          </div>
        )}
      </div>
      <div className="mapDesktopCanvas">
        <div className="mapTopToolbar">
          <input value={cadQuery} onChange={(event) => setCadQuery(event.target.value)} placeholder="Кадастровый номер или адрес" aria-label="Кадастровый номер или адрес" />
          <button disabled={cadQuery.trim().length < 3} onClick={async () => {
            try { setError(""); setCad(await searchCadastre(cadQuery)); setShowCadastre(true); }
            catch (err) { setError(String(err)); }
          }}><Search size={14} />Найти</button>
          {cad && <span title={cadText}>Кадастровый объект найден</span>}
          <button onClick={() => tileMode ? void loadCurrentMapDataset() : void load()}><RefreshCcw size={14} />Обновить метки</button>
          {isAdmin && <button disabled={syncing} onClick={() => void refreshCatalogue()}><RefreshCcw size={14} />{syncing ? "Обновление лотов…" : "Обновить лоты"}</button>}
        </div>
        <YandexDesktopMap
          lots={visibleLots}
          tileEntries={tileEntries}
          reviewMarkerUpdate={reviewMarkerUpdate}
          selectedCadastre={cad}
          showCadastre={showCadastre}
          selectedLotId={selectedLotId}
          selectedLotGeometry={selectedLot?.geometry || null}
          active={active}
          onSelect={selectLot}
          onClusterSelect={(ids) => {
            setCoincidentLotIds(Array.from(new Set(ids)));
            setSelectedLotId(null);
          }}
          onViewport={handleViewport}
          onRendered={(durationMs) =>
            setTimings((value) => ({ ...value, render: durationMs }))
          }
        />
        <footer className="mapBottomStatus" aria-label="Состояние карты">
          <span>
            {mapObjectCountLabel(statistics.total, statistics.returned, statistics.exact)} · {visibleMapObjects} на карте
            {statistics.exact && <> · {statistics.withoutCoordinates} без координат · {relativeUpdate(statistics.updatedAt, clock)}</>}
            {timings.api > 0 && (
              <> · API {Math.round(timings.api)} мс · карта {Math.round(timings.render)} мс{timings.cached ? " · кеш" : ""}</>
            )}
          </span>
          <div className="mapBottomStatusEnd">
            {statusContent}
            <span className={error ? "mapAppState mapAppState--error" : "mapAppState"}>
              <i />
              {loading || (tileMode && mapDatasetStatus === "loading") ? "Обновление данных" : error ? "Требуется внимание" : "Система готова"}
            </span>
          </div>
        </footer>
        {statistics.truncated && (
          <div className="mapViewportWarning" role="status">
            В этой области слишком много объектов. Показаны первые {statistics.limit}. Приблизьте карту.
          </div>
        )}
      </div>
    </section>
  );
}
