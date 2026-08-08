import React from "react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  MapPin,
  RefreshCcw,
  Search,
  Star,
  X,
} from "lucide-react";

import {
  fetchCapabilities,
  fetchCurrentUser,
  fetchMapLotDetail,
  fetchMapLotsSWR,
  fetchRegions,
  MapLot,
  MapMarkerLot,
  RegionOption,
  searchCadastre,
  setReviewStatus,
  splitLot,
  syncRegion,
} from "../../lib/api";

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
  const channel = React.useMemo(() => crypto.randomUUID(), []);
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
    () => `<!doctype html><html><head><meta charset="utf-8"><script src="https://api-maps.yandex.ru/2.1/?lang=ru_RU"></script><style>
html,body,#map{height:100%;margin:0}body{font:13px Arial,sans-serif;overflow:hidden}.hint{position:absolute;z-index:5;left:12px;top:12px;background:#fff;border:1px solid #cbd2dc;border-radius:4px;padding:9px 12px;color:#42526b;box-shadow:0 2px 8px #0002}
</style></head><body><div id="map"></div><div id="hint" class="hint">Загрузка Яндекс.Карт…</div><script>
const channel=${safeScriptJson(channel)};const instanceId=(crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random());let map=null;let manager=null;let lots=[];let cad=null;let selectedGeometry=null;let showCad=true;let selectedId=null;let pending=[];let overlayObjects=[];let viewportTimer=null;
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));}
function ended(l){return l.is_archived||['closed','completed','cancelled','canceled','failed','annulled','archive','archived'].includes(String(l.status||'').toLowerCase());}
function color(l){if(ended(l))return '#111111';return l.review_status==='approved'?'#24a269':l.review_status==='maybe'?'#e0aa16':l.review_status==='rejected'?'#d94b4b':'#7d8795';}
function icon(l){const c=color(l),selected=Number(l.id)===selectedId;return '<svg xmlns="http://www.w3.org/2000/svg" width="42" height="52" viewBox="0 0 38 48"><path d="M19 1C9.1 1 1 9.1 1 19c0 13.2 18 28 18 28s18-14.8 18-28C37 9.1 28.9 1 19 1z" fill="'+c+'" stroke="'+(selected?'#f1a800':'white')+'" stroke-width="'+(selected?'3':'2')+'"/><path d="M12 23V14h14v9M10 23h18M15 18h2m4 0h2" fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round"/></svg>';}
function opts(l){return{iconLayout:'default#image',iconImageHref:'data:image/svg+xml;charset=UTF-8,'+encodeURIComponent(icon(l)),iconImageSize:[42,52],iconImageOffset:[-21,-52]};}
function send(type,payload={}){parent.postMessage({type,channel,...payload},'*');}
function convert(coords){if(!Array.isArray(coords))return coords;if(coords.length===2&&typeof coords[0]==='number')return[coords[1],coords[0]];return coords.map(convert);}
function feature(l){return{type:'Feature',id:Number(l.id),geometry:{type:'Point',coordinates:[l.lat,l.lon]},properties:{hintContent:esc(l.title),lotId:Number(l.id)},options:opts(l)};}
function clusterSelection(clusterId,objects){const entries=Array.isArray(objects)?objects:[];const lotIds=entries.map(item=>Number(item.id??item.properties?.lotId)).filter(Number.isFinite);const coords=entries.map(item=>item.geometry?.coordinates).filter(value=>Array.isArray(value)&&value.length===2);const samePoint=coords.length>1&&coords.every(value=>Math.abs(Number(value[0])-Number(coords[0][0]))<1e-9&&Math.abs(Number(value[1])-Number(coords[0][1]))<1e-9);if(lotIds.length>1&&(samePoint||map.getZoom()>=18)){send('bankrotai-cluster-select',{clusterId,lotIds,zoom:map.getZoom()});return true;}return false;}
function clearOverlays(){overlayObjects.forEach(item=>map.geoObjects.remove(item));overlayObjects=[];}
function addGeometry(value,style){if(!value||!value.type||!value.coordinates)return;const sets=value.type==='MultiPolygon'?value.coordinates:[value.coordinates];sets.forEach(coords=>{const polygon=new ymaps.Polygon(convert(coords),{},style);map.geoObjects.add(polygon);overlayObjects.push(polygon);});}
function renderOverlays(focus=false){if(!map)return;clearOverlays();if(cad&&Number.isFinite(cad.lat)&&Number.isFinite(cad.lon)){const point=new ymaps.Placemark([cad.lat,cad.lon],{balloonContent:esc(cad.cadastral_number||cad.address||'Кадастровый объект')},{preset:'islands#violetDotIcon'});map.geoObjects.add(point);overlayObjects.push(point);if(showCad&&cad.geometry)addGeometry(cad.geometry,{strokeColor:'#7c3aed',strokeWidth:3,fillColor:'#7c3aed22'});if(focus)map.setCenter([cad.lat,cad.lon],Math.max(map.getZoom(),16));}if(showCad&&selectedGeometry)addGeometry(selectedGeometry,{strokeColor:'#2468d8',strokeWidth:3,fillColor:'#2468d822'});}
function updateSelection(nextId){const previous=selectedId;selectedId=nextId==null?null:Number(nextId);[previous,selectedId].forEach(id=>{const lot=lots.find(item=>Number(item.id)===Number(id));if(manager&&lot)manager.objects.setObjectOptions(Number(id),opts(lot));});}
function renderLots(){if(!map||!manager)return;const started=performance.now();manager.removeAll();manager.add({type:'FeatureCollection',features:lots.filter(l=>Number.isFinite(l.lat)&&Number.isFinite(l.lon)).map(feature)});if(selectedId!=null)updateSelection(selectedId);requestAnimationFrame(()=>send('bankrotai-rendered',{durationMs:performance.now()-started,count:lots.length}));}
function emitViewport(){if(!map)return;const bounds=map.getBounds();send('bankrotai-viewport',{bounds:[bounds[0][1],bounds[0][0],bounds[1][1],bounds[1][0]],zoom:map.getZoom()});}
function scheduleViewport(){clearTimeout(viewportTimer);viewportTimer=setTimeout(emitViewport,250);}
function command(data){if(!map){pending.push(data);return;}if(data.type==='replace-lots'){lots=Array.isArray(data.lots)?data.lots:[];renderLots();}else if(data.type==='select-lot'){updateSelection(data.lotId);}else if(data.type==='toggle-cadastre'){showCad=Boolean(data.enabled);renderOverlays(false);}else if(data.type==='show-cadastre-result'){cad=data.value||null;renderOverlays(Boolean(cad));}else if(data.type==='show-selected-geometry'){selectedGeometry=data.value||null;renderOverlays(false);}else if(data.type==='resume'){map.container.fitToViewport();scheduleViewport();}}
window.addEventListener('message',event=>{if(event.source!==parent||event.data?.channel!==channel)return;command(event.data);});
function init(){map=new ymaps.Map('map',{center:[57.6261,39.8845],zoom:7,controls:['zoomControl','typeSelector','fullscreenControl','geolocationControl']});manager=new ymaps.ObjectManager({clusterize:true,gridSize:64,clusterDisableClickZoom:false});manager.clusters.options.set({preset:'islands#darkBlueClusterIcons'});manager.objects.events.add('click',event=>send('bankrotai-select',{lotId:Number(event.get('objectId'))}));manager.clusters.events.add('click',event=>{const clusterId=event.get('objectId');const cluster=manager.clusters.getById(clusterId);if(clusterSelection(clusterId,cluster?.properties?.geoObjects))event.preventDefault?.();});map.geoObjects.add(manager);map.events.add('boundschange',scheduleViewport);window.bankrotaiDebug={instanceId,getViewport:()=>({center:map.getCenter(),zoom:map.getZoom(),instanceId}),setViewport:(center,zoom)=>map.setCenter(center,zoom),getLotReview:id=>lots.find(l=>Number(l.id)===Number(id))?.review_status||null,clickObject:id=>manager.objects.events.fire('click',{objectId:Number(id)}),clickCoincident:ids=>clusterSelection('debug',lots.filter(l=>ids.map(Number).includes(Number(l.id))).map(feature)),getObjectCount:()=>manager.objects.getLength()};pending.splice(0).forEach(command);document.getElementById('hint').style.display='none';send('bankrotai-ready');scheduleViewport();}
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
              <dd>{lot.application_deadline}</dd>
            </>
          )}
          {lot.auction_at && (
            <>
              <dt>Торги:</dt>
              <dd>{lot.auction_at}</dd>
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
}: {
  refreshToken: number;
  favoritesOnly?: boolean;
  active?: boolean;
  onFavoriteCount?: (count: number) => void;
}) {
  const [lots, setLots] = React.useState<MapMarkerLot[]>([]);
  const [selectedLot, setSelectedLot] = React.useState<MapLot | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [detailError, setDetailError] = React.useState("");
  const [viewport, setViewport] = React.useState<[number, number, number, number] | null>(null);
  const requestRevision = React.useRef(0);
  const requestController = React.useRef<AbortController | null>(null);
  const reviewOverrides = React.useRef(new Map<number, string>());
  const [statistics, setStatistics] = React.useState({
    total: 0,
    mapped: 0,
    withoutCoordinates: 0,
    returned: 0,
    limit: 0,
    truncated: false,
    updatedAt: null as string | null,
  });
  const [timings, setTimings] = React.useState({ api: 0, server: 0, render: 0, cached: false });
  const [clock, setClock] = React.useState(Date.now());
  const [regions, setRegions] = React.useState<RegionOption[]>([]);
  const [error, setError] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [canSync, setCanSync] = React.useState(false);
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
    includeArchived: false,
  });

  const applyResponse = React.useCallback((response: Awaited<ReturnType<typeof fetchMapLotsSWR>>["data"], cached: boolean, apiMs = 0) => {
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
      region = filters.region,
      includeArchived = filters.includeArchived,
    ) => {
      if (!favoritesOnly && !viewport) return;
      const revision = ++requestRevision.current;
      requestController.current?.abort();
      const controller = new AbortController();
      requestController.current = controller;
      setLoading(true);
      setError("");
      try {
        const query = favoritesOnly
          ? { city_slug: region || undefined, include_archived: includeArchived, review_status: "approved" as const }
          : {
              city_slug: region || undefined,
              include_archived: includeArchived,
              west: viewport?.[0],
              south: viewport?.[1],
              east: viewport?.[2],
              north: viewport?.[3],
            };
        const result = await fetchMapLotsSWR(query, (cached) => {
          if (revision === requestRevision.current) applyResponse(cached, true);
        }, controller.signal);
        if (revision === requestRevision.current) {
          applyResponse(result.data, result.fromCache, result.networkMs);
        }
      } catch (err) {
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          setError(err instanceof Error ? err.message : "Ошибка загрузки карты");
        }
      } finally {
        if (revision === requestRevision.current) setLoading(false);
      }
    },
    [applyResponse, favoritesOnly, filters.includeArchived, filters.region, viewport],
  );

  React.useEffect(() => {
    if (active) void load();
  }, [active, load, refreshToken]);
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
    Promise.all([fetchCapabilities(), fetchRegions(), fetchCurrentUser()])
      .then(([capabilities, values, user]) => {
        setCanSync(capabilities.region_sync);
        setRegions(values);
        setIsAdmin(user.role === "admin");
      })
      .catch(() => setCanSync(false));
  }, []);

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
      setSelectedLot((lot) => lot?.id === lotId ? { ...lot, review_status: status } : lot);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Не удалось сохранить оценку",
      );
    }
  }, []);

  const visibleLots = lots.filter(
    (lot) =>
      (!filters.minPrice ||
        (lot.current_price ?? 0) >= Number(filters.minPrice)) &&
      (!filters.maxPrice ||
        (lot.current_price ?? Number.POSITIVE_INFINITY) <=
          Number(filters.maxPrice)),
  );
  const favoriteLots = visibleLots.filter(
    (lot) => lot.review_status === "approved",
  );
  React.useEffect(() => {
    onFavoriteCount?.(favoritesOnly ? statistics.total : favoriteLots.length);
  }, [favoriteLots.length, favoritesOnly, onFavoriteCount, statistics.total]);
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
      const precision = zoom >= 16 ? 4 : zoom >= 10 ? 3 : 2;
      const next: [number, number, number, number] = [
        Math.max(-180, Number((west - lonPadding).toFixed(precision))),
        Math.max(-90, Number((south - latPadding).toFixed(precision))),
        Math.min(180, Number((east + lonPadding).toFixed(precision))),
        Math.min(90, Number((north + latPadding).toFixed(precision))),
      ];
      setViewport((current) =>
        current?.every((value, index) => value === next[index]) ? current : next,
      );
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
            <h2>Кадастровый поиск</h2>
            <label className="srOnly" htmlFor="cadastre-map-search">
              Кадастровый номер или адрес
            </label>
            <input
              id="cadastre-map-search"
              value={cadQuery}
              onChange={(event) => setCadQuery(event.target.value)}
              placeholder="Кадастровый номер или адрес"
            />
            <button
              disabled={cadQuery.trim().length < 3}
              onClick={async () => {
                try {
                  setError("");
                  setCad(await searchCadastre(cadQuery));
                } catch (err) {
                  setError(String(err));
                }
              }}
            >
              <Search size={14} />
              Найти объект
            </button>
            <label className="mapControlCheck">
              <input
                type="checkbox"
                checked={showCadastre}
                onChange={(event) => setShowCadastre(event.target.checked)}
              />
              Показать кадастровые границы
            </label>
            <button onClick={() => load()}>
              <RefreshCcw size={14} />
              Обновить метки лотов
            </button>
            <button
              className="mapAllRussiaButton"
              onClick={() => {
                const next = { ...filters, region: "" };
                setFilters(next);
                void load("", next.includeArchived);
              }}
            >
              <MapPin size={15} />
              Поиск всех лотов РФ
            </button>

            <fieldset className="mapFiltersBox">
              <legend>Фильтры лотов</legend>
              <label>
                <span>Цена от</span>
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
                <span>Цена до</span>
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
                <span>Регион карты</span>
                <select
                  value={filters.region}
                  onChange={(event) =>
                    setFilters({ ...filters, region: event.target.value })
                  }
                >
                  <option value="">Все регионы</option>
                  {regions.map((region) => (
                    <option key={region.code} value={region.code}>
                      {region.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="mapControlCheck">
                <input
                  type="checkbox"
                  checked={filters.includeArchived}
                  onChange={(event) =>
                    setFilters({
                      ...filters,
                      includeArchived: event.target.checked,
                    })
                  }
                />
                Показывать архивные
              </label>
              <div>
                <button onClick={() => load()}>Применить</button>
                <button
                  onClick={() => {
                    const empty = {
                      region: "",
                      minPrice: "",
                      maxPrice: "",
                      includeArchived: false,
                    };
                    setFilters(empty);
                    void load("", false);
                  }}
                >
                  Сбросить
                </button>
              </div>
            </fieldset>
            <pre className="mapCadastreResult">{cadText}</pre>
            {canSync && (
              <button
                className="mapSyncButton"
                disabled={!filters.region}
                onClick={async () => {
                  try {
                    const result = await syncRegion(filters.region, true);
                    setMessage(
                      `Синхронизация: ${result.dispatchMode || result.status}`,
                    );
                  } catch (err) {
                    setError(String(err));
                  }
                }}
              >
                Синхронизировать выбранный регион
              </button>
            )}
            {loading && <MapState>Обновление меток…</MapState>}
            {message && <MapState>{message}</MapState>}
            {error && <MapState error>{error}</MapState>}
            <small className="mapLotCount">
              На карте: {visibleLots.length} лотов
            </small>
          </div>
        )}
      </div>
      <div className="mapDesktopCanvas">
        <YandexDesktopMap
          lots={visibleLots}
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
            {statistics.total} объектов · {visibleLots.length} на карте ·{" "}
            {statistics.withoutCoordinates} без координат ·{" "}
            {relativeUpdate(statistics.updatedAt, clock)}
            {timings.api > 0 && (
              <> · API {Math.round(timings.api)} мс · карта {Math.round(timings.render)} мс{timings.cached ? " · кеш" : ""}</>
            )}
          </span>
          <span className={error ? "mapAppState mapAppState--error" : "mapAppState"}>
            <i />
            {loading ? "Обновление данных" : error ? "Требуется внимание" : "Система готова"}
          </span>
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
