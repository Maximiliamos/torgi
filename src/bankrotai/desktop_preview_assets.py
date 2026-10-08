"""Static map-preview CSS, HTML and JavaScript used by PySide6 desktop UI.

This extraction contains no runtime logic; constants must remain byte-for-byte
identical to the inline strings previously embedded in gui.py.
"""

MAP_PREVIEW_STYLE = """
.lot-preview {
    position: absolute; z-index: 1000; top: 0; left: 0; bottom: 29px; width: 380px;
    box-sizing: border-box; overflow-y: auto; background: #fff; color: #273142;
    box-shadow: 4px 0 18px rgba(34, 46, 66, .22); font: 14px Arial, sans-serif;
    transform: translateX(-105%); transition: transform .22s ease;
}
.lot-preview.open { transform: translateX(0); }
.lot-preview__close {
    position: absolute; z-index: 2; top: 10px; right: 10px; width: 34px; height: 34px;
    border: 0; border-radius: 50%; background: rgba(255,255,255,.94); color: #536174;
    font-size: 23px; cursor: pointer; box-shadow: 0 1px 5px rgba(0,0,0,.18);
}
.lot-preview__media { position: relative; height: 245px; background: #edf1f6; }
.lot-preview__photo, .lot-preview__placeholder {
    display: block; width: 100%; height: 245px; object-fit: cover; background: #edf1f6;
}
.lot-preview__placeholder { display: flex; align-items: center; justify-content: center; color: #8995a7; font-size: 15px; }
.lot-preview__arrow {
    position: absolute; z-index: 2; top: 50%; transform: translateY(-50%); width: 38px; height: 48px;
    border: 0; border-radius: 7px; background: rgba(20,30,45,.62); color: white; font-size: 28px;
    cursor: pointer; display: none;
}
.lot-preview__arrow:hover { background: rgba(20,30,45,.82); }
.lot-preview__arrow--prev { left: 10px; }
.lot-preview__arrow--next { right: 10px; }
.lot-preview__counter {
    position: absolute; right: 10px; bottom: 9px; padding: 4px 8px; border-radius: 12px;
    background: rgba(20,30,45,.68); color: white; font-size: 12px; display: none;
}
.lot-preview__body { padding: 18px; }
.lot-preview__source { color: #16866d; font-size: 12px; font-weight: 700; text-transform: uppercase; }
.lot-preview__title { margin: 10px 0; font-size: 17px; line-height: 1.45; }
.lot-preview__description { color: #536174; line-height: 1.45; max-height: 105px; overflow: auto; white-space: pre-wrap; }
.lot-preview__price { margin: 14px 0; font-size: 22px; font-weight: 700; }
.lot-preview__details { margin: 12px 0 16px; line-height: 1.55; color: #536174; }
.lot-preview__details b { color: #273142; }
.lot-preview__links { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.lot-preview__source-button {
    width: 100%; padding: 11px 6px; border: 0; border-radius: 7px; background: #2868e8;
    color: #fff; font-weight: 700; font-size: 15px; cursor: pointer;
}
.lot-preview__source-button[data-kind="gis"] { background: #177d65; }
.lot-preview__source-button[data-kind="etp"] { background: #7654b5; }
.lot-preview__source-button[data-kind="russia"] { background: #596579; }
.lot-preview__source-button:disabled { background: #aab3c1; cursor: default; }
.lot-preview__review-title { margin: 18px 0 9px; font-weight: 700; }
.lot-preview__reviews { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.review-button { padding: 10px 4px; border: 2px solid #e1e6ed; border-radius: 8px; background: #fff; cursor: pointer; font-size: 12px; }
.review-button span { display: block; font-size: 23px; margin-bottom: 3px; }
.review-button[data-status="approved"] { color: #188b5b; }
.review-button[data-status="maybe"] { color: #b88700; }
.review-button[data-status="rejected"] { color: #d43f3f; }
.review-button.active[data-status="approved"] { border-color: #22a76f; background: #e8f8f0; }
.review-button.active[data-status="maybe"] { border-color: #e4b72c; background: #fff8d9; }
.review-button.active[data-status="rejected"] { border-color: #df5252; background: #fff0f0; }
.map-status {
    position: absolute; z-index: 1100; left: 0; right: 0; bottom: 0; height: 29px;
    box-sizing: border-box; display: flex; align-items: center; justify-content: space-between;
    gap: 16px; padding: 0 12px; overflow: hidden; border-top: 1px solid #d8dee4;
    background: rgba(255,255,255,.98); color: #52606d; font: 11px Arial, sans-serif;
    white-space: nowrap;
}
.map-status__summary { overflow: hidden; text-overflow: ellipsis; }
.map-status__state { display: flex; align-items: center; flex: 0 0 auto; gap: 6px; color: #177d65; }
.map-status__state::before { width: 7px; height: 7px; border-radius: 50%; background: #20a36e; content: ''; }
"""

MAP_PREVIEW_HTML = """
<aside id="lot-preview" class="lot-preview" aria-hidden="true">
  <button id="lot-preview-close" class="lot-preview__close" title="&#1047;&#1072;&#1082;&#1088;&#1099;&#1090;&#1100;">&times;</button>
  <div class="lot-preview__media">
    <img id="lot-preview-photo" class="lot-preview__photo" alt="&#1060;&#1086;&#1090;&#1086; &#1083;&#1086;&#1090;&#1072;">
    <div id="lot-preview-placeholder" class="lot-preview__placeholder">&#1060;&#1086;&#1090;&#1086; &#1086;&#1090;&#1089;&#1091;&#1090;&#1089;&#1090;&#1074;&#1091;&#1077;&#1090;</div>
    <button id="lot-preview-prev" class="lot-preview__arrow lot-preview__arrow--prev" aria-label="Previous">&#8249;</button>
    <button id="lot-preview-next" class="lot-preview__arrow lot-preview__arrow--next" aria-label="Next">&#8250;</button>
    <div id="lot-preview-counter" class="lot-preview__counter"></div>
  </div>
  <div class="lot-preview__body">
    <div id="lot-preview-source" class="lot-preview__source"></div>
    <h2 id="lot-preview-title" class="lot-preview__title"></h2>
    <div id="lot-preview-description" class="lot-preview__description"></div>
    <div id="lot-preview-price" class="lot-preview__price"></div>
    <div id="lot-preview-details" class="lot-preview__details"></div>
    <div class="lot-preview__links">
      <button class="lot-preview__source-button related-link" data-url-key="source_url">&#1048;&#1089;&#1090;&#1086;&#1095;&#1085;&#1080;&#1082;</button>
      <button class="lot-preview__source-button related-link" data-kind="gis" data-url-key="gis_torgi_url">&#1043;&#1048;&#1057; &#1058;&#1086;&#1088;&#1075;&#1080;</button>
      <button class="lot-preview__source-button related-link" data-kind="etp" data-url-key="etp_url">&#1069;&#1058;&#1055;</button>
      <button class="lot-preview__source-button related-link" data-kind="russia" data-url-key="torgi_russia_url">&#1058;&#1086;&#1088;&#1075;&#1080; &#1056;&#1060;</button>
    </div>
    <div class="lot-preview__review-title">&#1054;&#1094;&#1077;&#1085;&#1082;&#1072; &#1083;&#1086;&#1090;&#1072;</div>
    <div class="lot-preview__reviews">
      <button class="review-button" data-status="approved"><span>&#10003;</span>&#1048;&#1085;&#1090;&#1077;&#1088;&#1077;&#1089;&#1077;&#1085;</button>
      <button class="review-button" data-status="maybe"><span>?</span>&#1057;&#1086;&#1084;&#1085;&#1077;&#1074;&#1072;&#1102;&#1089;&#1100;</button>
      <button class="review-button" data-status="rejected"><span>&#10005;</span>&#1055;&#1083;&#1086;&#1093;&#1086;&#1081;</button>
    </div>
  </div>
</aside>
<footer class="map-status" aria-label="Состояние карты">
  <span id="map-status-summary" class="map-status__summary">0 объектов · 0 на карте · 0 без координат · обновление...</span>
  <span class="map-status__state">Система готова</span>
</footer>
"""

MAP_PREVIEW_SCRIPT = """
let bankrotaiBridge = null;
let selectedPreviewLot = null;
let previewImages = [];
let previewImageIndex = 0;

function mapUpdateAge(value) {
    if (!value) return 'время обновления неизвестно';
    const minutes = Math.max(0, Math.floor((Date.now() - Date.parse(value)) / 60000));
    if (minutes < 1) return 'обновлено только что';
    if (minutes < 60) return 'обновлено ' + minutes + ' мин назад';
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return 'обновлено ' + hours + ' ч назад';
    return 'обновлено ' + Math.floor(hours / 24) + ' дн назад';
}

window.setMapStatus = function(value) {
    const stats = value || {};
    document.getElementById('map-status-summary').textContent =
        Number(stats.total || 0) + ' объектов · ' + Number(stats.mapped || 0) +
        ' на карте · ' + Number(stats.without_coordinates || 0) +
        ' без координат · ' + mapUpdateAge(stats.updated_at);
};

if (window.qt && window.qt.webChannelTransport) {
    new QWebChannel(qt.webChannelTransport, function(channel) {
        bankrotaiBridge = channel.objects.bankrotaiBridge;
    });
}

function previewText(id, value) {
    document.getElementById(id).textContent = value || '';
}

function setPreviewReviewStatus(status) {
    document.querySelectorAll('.review-button').forEach(function(button) {
        button.classList.toggle('active', button.dataset.status === status);
    });
}

function renderPreviewLinks() {
    document.querySelectorAll('.related-link').forEach(function(button) {
        const url = selectedPreviewLot && selectedPreviewLot[button.dataset.urlKey];
        button.disabled = !url;
    });
}

function renderPreviewImage() {
    const photo = document.getElementById('lot-preview-photo');
    const placeholder = document.getElementById('lot-preview-placeholder');
    const hasImage = previewImages.length > 0;
    photo.style.display = hasImage ? 'block' : 'none';
    placeholder.style.display = hasImage ? 'none' : 'flex';
    if (hasImage) photo.src = previewImages[previewImageIndex]; else photo.removeAttribute('src');
    const multiple = previewImages.length > 1;
    document.getElementById('lot-preview-prev').style.display = multiple ? 'block' : 'none';
    document.getElementById('lot-preview-next').style.display = multiple ? 'block' : 'none';
    const counter = document.getElementById('lot-preview-counter');
    counter.style.display = multiple ? 'block' : 'none';
    counter.textContent = hasImage ? (previewImageIndex + 1) + ' / ' + previewImages.length : '';
}

function movePreviewImage(delta) {
    if (previewImages.length < 2) return;
    previewImageIndex = (previewImageIndex + delta + previewImages.length) % previewImages.length;
    renderPreviewImage();
}

function showLotPreview(lot) {
    selectedPreviewLot = lot;
    const panel = document.getElementById('lot-preview');
    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
    if (bankrotaiBridge) bankrotaiBridge.previewOpened(mapKind, Number(lot.id));
    previewText('lot-preview-source', lot.source_name || lot.source || 'Источник не указан');
    previewText('lot-preview-title', lot.title || 'Лот без названия');
    previewText('lot-preview-description', lot.description || lot.address || 'Описание отсутствует');
    previewText('lot-preview-price', formatPrice(lot.price));

    const details = [];
    if (lot.address) details.push('<b>Адрес:</b> ' + escapeHtml(lot.address));
    if (lot.cadastral_number) details.push('<b>Кадастр:</b> ' + escapeHtml(lot.cadastral_number));
    if (lot.procedure_number) details.push('<b>Процедура:</b> ' + escapeHtml(lot.procedure_number));
    if (lot.application_deadline) details.push('<b>Приём заявок до:</b> ' + escapeHtml(lot.application_deadline));
    if (lot.auction_at) details.push('<b>Торги:</b> ' + escapeHtml(lot.auction_at));
    document.getElementById('lot-preview-details').innerHTML = details.join('<br>');

    previewImages = Array.from(new Set((lot.image_urls || []).concat(lot.image_url || []).filter(Boolean)));
    previewImageIndex = 0;
    renderPreviewImage();
    renderPreviewLinks();
    setPreviewReviewStatus(lot.review_status || 'new');
}

document.getElementById('lot-preview-photo').addEventListener('error', function() {
    this.style.display = 'none'; document.getElementById('lot-preview-placeholder').style.display = 'flex';
});
document.getElementById('lot-preview-close').addEventListener('click', function() {
    document.getElementById('lot-preview').classList.remove('open');
    document.getElementById('lot-preview').setAttribute('aria-hidden', 'true');
    if (bankrotaiBridge) bankrotaiBridge.previewClosed(mapKind);
});
document.getElementById('lot-preview-prev').addEventListener('click', function() { movePreviewImage(-1); });
document.getElementById('lot-preview-next').addEventListener('click', function() { movePreviewImage(1); });
document.querySelectorAll('.related-link').forEach(function(button) {
    button.addEventListener('click', function() {
        const url = selectedPreviewLot && selectedPreviewLot[button.dataset.urlKey];
        if (!url) return;
        if (bankrotaiBridge) bankrotaiBridge.openSource(url); else window.open(url, '_blank');
    });
});
document.querySelectorAll('.review-button').forEach(function(button) {
    button.addEventListener('click', function() {
        if (!selectedPreviewLot || !bankrotaiBridge) return;
        const status = button.dataset.status;
        bankrotaiBridge.setReviewStatus(Number(selectedPreviewLot.id), status, function(ok) {
            if (ok) {
                selectedPreviewLot.review_status = status;
                setPreviewReviewStatus(status);
                if (window.applyLotReviewStatus) window.applyLotReviewStatus(Number(selectedPreviewLot.id), status);
            }
        });
    });
});

window.showLotPreview = showLotPreview;
window.updateLotPreviewExtras = function(lotId, extras) {
    if (!selectedPreviewLot || Number(selectedPreviewLot.id) !== Number(lotId)) return;
    Object.assign(selectedPreviewLot, extras || {});
    const extraImages = (extras && (extras.torgi_russia_image_urls || extras.image_urls)) || [];
    previewImages = Array.from(new Set(previewImages.concat(extraImages).filter(Boolean)));
    renderPreviewImage();
    renderPreviewLinks();
};
window.setLotReviewStatus = function(lotId, status) {
    if (selectedPreviewLot && Number(selectedPreviewLot.id) === Number(lotId)) {
        selectedPreviewLot.review_status = status;
        setPreviewReviewStatus(status);
    }
    if (window.applyLotReviewStatus) window.applyLotReviewStatus(Number(lotId), status);
};
"""
