from __future__ import annotations

import base64
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Disable hardware acceleration for Qt to avoid Trae Sandbox errors
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QT_XCB_GL_INTEGRATION"] = "none"
os.environ["QT_OPENGL"] = "software"
os.environ["QTWEBENGINE_DISABLE_GPU"] = "1"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QUrl, QThread, Signal, QStandardPaths, QStringListModel, QBuffer, QIODevice
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtGui import QColor, QBrush, QDesktopServices, QImage
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QHeaderView, QLabel,
    QMainWindow, QMessageBox, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QTabWidget, QFileDialog,
    QProgressBar, QStatusBar, QLineEdit, QComboBox, QCheckBox, QGroupBox, QFormLayout,
    QCompleter, QScrollArea, QSizePolicy, QToolButton, QFrame,
)
from sqlalchemy import desc, select, func

from bankrotai.core import get_logger, get_settings
from bankrotai.db import ProcessedLot, LotGeoSnapshot, session_scope, init_db, RegionSyncState
from bankrotai.scrapers import (
    TBankrotClient,
    TBankrotSearchFilters,
    TorgiGovClient,
    TorgiGovSearchFilters,
    import_manual_html,
    sync_public_real_estate,
    ingest_recent_tbankrot,
)
from bankrotai.logic import delete_lots_batch, cleanup_closed_lots, persist_lot
from bankrotai.ai import OpenAIAppraiser, apply_evaluation_to_lot
from openpyxl import Workbook
from bankrotai.domain import NormalizedLot

logger = get_logger("gui")


class WheelSafeComboBox(QComboBox):
    """
    QComboBox that still opens on click, but ignores wheel changes while closed.
    This prevents accidental filter changes when scrolling the left panel.
    """

    def wheelEvent(self, event):
        if self.view() and self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


SORT_ROLE = Qt.UserRole + 100
EXTERNAL_ID_ROLE = Qt.UserRole + 101
URL_ROLE = Qt.UserRole + 102
MAP_ICON_FILENAMES = {
    "land": "участки.png",
    "rent": "аренда.png",
    "realEstate": "недвижимость + участки со строением.png",
    "auto": "авто.png",
    "other": "Прочее.png",
}
_MAP_ICON_DATA_URL_CACHE: dict[str, str] | None = None


class SortableTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE)

        if left is None:
            left = self.text()
        if right is None:
            right = other.text()

        try:
            return left < right
        except Exception:
            return str(left).lower() < str(right).lower()


def make_text_item(text: str | None) -> QTableWidgetItem:
    value = text or ""
    item = SortableTableWidgetItem(value)
    item.setData(SORT_ROLE, value.lower())
    return item


def make_number_item(value) -> QTableWidgetItem:
    if value is None:
        item = SortableTableWidgetItem("")
        item.setData(SORT_ROLE, -1)
        return item

    try:
        numeric = float(value)
    except Exception:
        numeric = -1

    display = f"{numeric:,.0f}".replace(",", " ")
    item = SortableTableWidgetItem(display)
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    item.setData(SORT_ROLE, numeric)
    return item


def parse_date_sort_value(value):
    if not value:
        return datetime.min

    if isinstance(value, datetime):
        return value

    raw = str(value).strip()
    raw = raw.replace("Z", "")
    raw = re.sub(r"([+-]\d{2}:\d{2})$", "", raw)
    raw = raw.split(".000+")[0]
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            pass

    return datetime.min


def make_date_item(value) -> QTableWidgetItem:
    sort_value = parse_date_sort_value(value)

    if not value:
        display = ""
    elif isinstance(value, datetime):
        display = value.strftime("%d.%m.%Y")
    else:
        display = str(value)

    item = SortableTableWidgetItem(display)
    item.setData(SORT_ROLE, sort_value)
    return item


class CollapsibleSection(QWidget):
    def __init__(self, title: str, parent=None, expanded: bool = False):
        super().__init__(parent)

        self.toggle_button = QToolButton()
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle_button.setStyleSheet("""
            QToolButton {
                border: none;
                background: #eef3fb;
                color: #143370;
                font-weight: 700;
                padding: 7px 6px;
                text-align: left;
                border-radius: 4px;
            }
            QToolButton:hover {
                background: #e2ebfb;
            }
        """)

        self.content = QWidget()
        self.content.setVisible(expanded)
        self.content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(8, 8, 8, 8)
        self.content_layout.setSpacing(8)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        root.addWidget(self.toggle_button)
        root.addWidget(self.content)

        self.toggle_button.clicked.connect(self._on_toggled)

    def _on_toggled(self, checked: bool):
        self.content.setVisible(checked)
        self.toggle_button.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

    def addWidget(self, widget: QWidget):
        self.content_layout.addWidget(widget)

    def addLayout(self, layout):
        self.content_layout.addLayout(layout)


AI_PROVIDER_OPTIONS = [
    ("gemini", "Google Gemini"),
    ("groq", "GroqCloud"),
    ("grok", "Grok / xAI"),
    ("github", "GitHub Models"),
    ("omniroute", "Kiro через OmniRoute"),
    ("nvidia", "NVIDIA"),
]

AI_MODEL_OPTIONS = {
    "omniroute": [
        ("kr/claude-sonnet-4", "Kiro: Claude Sonnet 4"),
    ],
    "nvidia": [
        ("nvidia/llama-3.3-nemotron-super-49b-v1", "NVIDIA: Llama 3.3 Nemotron Super 49B"),
        ("meta/llama-3.3-70b-instruct", "NVIDIA: Llama 3.3 70B Instruct"),
    ],
    "gemini": [
        ("gemini-2.5-flash", "Gemini 2.5 Flash"),
        ("gemini-2.5-pro", "Gemini 2.5 Pro"),
        ("gemini-2.0-flash", "Gemini 2.0 Flash"),
    ],
    "grok": [
        ("grok-4", "Grok 4"),
        ("grok-4-fast-reasoning", "Grok 4 Fast Reasoning"),
        ("grok-3-mini", "Grok 3 Mini"),
    ],
    "groq": [
        ("llama-3.3-70b-versatile", "Groq: Llama 3.3 70B Versatile"),
        ("openai/gpt-oss-120b", "Groq: GPT-OSS 120B"),
        ("llama-3.1-8b-instant", "Groq: Llama 3.1 8B Instant"),
    ],
    "github": [
        ("openai/gpt-4.1-mini", "GitHub: GPT-4.1 Mini"),
        ("openai/gpt-4.1", "GitHub: GPT-4.1"),
        ("xai/grok-3-mini", "GitHub: Grok 3 Mini"),
        ("meta/Llama-4-Scout-17B-16E-Instruct", "GitHub: Llama 4 Scout"),
    ],
}

def translate_status(status: str) -> str: 
    mapping = { 
        "active": "Активен", 
        "closed": "Завершен", 
        "pending": "Ожидается", 
        "scheduled": "Запланирован", 
        "unknown": "Неизвестно", 
    } 
    return mapping.get(status, status) 

def translate_category(category: str) -> str: 
    mapping = { 
        "apartment": "Квартира", 
        "house": "Жилой дом", 
        "commercial": "Коммерческая недв.", 
        "commercial_room": "Нежилое помещение", 
        "commercial_building": "Нежилое здание", 
        "commercial_building_with_land": "Нежилое здание + ЗУ", 
        "complex": "Имущ. комплекс", 
        "office": "Офис", 
        "retail": "Торговое", 
        "land": "Земельный участок", 
        "car": "Автомобиль", 
        "transport": "Транспорт", 
        "vehicle": "Спецтехника", 
        "equipment": "Оборудование", 
        "parking": "Парковка", 
        "unfinished": "Недострой", 
        "receivable": "Права требования", 
        "real_estate": "Недвижимость", 
        "other": "Прочее", 
    } 
    return mapping.get(category, category) 

# --- Background Workers ---

class SyncWorker(QThread):
    finished = Signal(int)
    progress = Signal(str)
    progress_percent = Signal(int)
    error = Signal(str)

    def __init__(self, region: str):
        super().__init__()
        self.region = region

    def run(self):
        try:
            self.progress.emit(f"Запуск синхронизации для региона: {self.region}...")
            with session_scope() as session:
                self.progress.emit("Загрузка данных с GorodTorgi (может занять 1-2 минуты)...")
                lots_gt = sync_public_real_estate(session, self.region)
                
                self.progress.emit("Загрузка данных с TBankrot...")
                lots_tb = ingest_recent_tbankrot(session, self.region)
                
                total = len(lots_gt) + len(lots_tb)
                self.finished.emit(total)
        except Exception as e:
            logger.error(f"SyncWorker error: {e}")
            self.error.emit(str(e))


class TorgiGovSearchWorker(QThread):
    progress = Signal(str)
    progress_percent = Signal(int)
    page_loaded = Signal(list, dict)
    finished = Signal(list, dict)
    error = Signal(str)

    def __init__(
        self,
        filters: TorgiGovSearchFilters,
        *,
        load_all: bool = False,
        max_items: int | None = 5000,
        use_excel: bool = False,
    ):
        super().__init__()
        self.filters = filters
        self.load_all = load_all
        self.max_items = max_items
        self.use_excel = use_excel
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def _persist_lots(self, lots: list[NormalizedLot]) -> None:
        if not lots:
            return
        with session_scope() as session:
            for lot in lots:
                persist_lot(session, lot)

    def run(self):
        try:
            self.progress.emit("Подключение к torgi.gov.ru...")
            self.progress_percent.emit(3)

            client = TorgiGovClient(diagnostics=True)

            if self.use_excel:
                self.progress.emit("Скачивание Excel-выгрузки torgi.gov.ru...")
                self.progress_percent.emit(25)
                lots, meta = client.search_lots_excel(self.filters)
                self.progress.emit("Импорт строк из Excel-выгрузки...")
                self.progress_percent.emit(75)
                self._persist_lots(lots)
                self.page_loaded.emit(lots, meta)
            elif self.load_all:
                self.progress.emit("Загрузка всех страниц torgi.gov.ru...")

                def report(page: int, total: int | None, loaded: int):
                    if total:
                        percent = min(99, int((loaded / max(total, 1)) * 100))
                        self.progress_percent.emit(percent)
                        self.progress.emit(f"Загружено {loaded}/{total} лотов, страница {page}...")
                    else:
                        self.progress_percent.emit(min(99, 5 + page))
                        self.progress.emit(f"Загружено {loaded} лотов, страница {page}...")

                def page_loaded(lots_on_page: list[NormalizedLot], page_meta: dict):
                    self.page_loaded.emit(lots_on_page, page_meta)

                lots, meta = client.search_all_lots(
                    self.filters,
                    max_items=self.max_items,
                    progress_cb=report,
                    page_cb=page_loaded,
                    stop_cb=lambda: self._stop_requested,
                )
            else:
                self.progress.emit("Загрузка страницы онлайн-лотов...")
                self.progress_percent.emit(35)
                lots, meta = client.search_lots(self.filters)
                self._persist_lots(lots)
                self.page_loaded.emit(lots, meta)

            self.progress_percent.emit(100)
            self.finished.emit(lots, meta)
        except Exception as e:
            logger.exception("TorgiGovSearchWorker error")
            self.error.emit(str(e))


class TBankrotSearchWorker(QThread):
    progress = Signal(str)
    progress_percent = Signal(int)
    page_loaded = Signal(list, dict)
    finished = Signal(list, dict)
    error = Signal(str)

    def __init__(
        self,
        filters: TBankrotSearchFilters,
        *,
        load_all: bool = False,
        max_items: int | None = 5000,
    ):
        super().__init__()
        self.filters = filters
        self.load_all = load_all
        self.max_items = max_items
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def _persist_lots(self, lots: list[NormalizedLot]) -> None:
        if not lots:
            return
        with session_scope() as session:
            for lot in lots:
                persist_lot(session, lot)

    def run(self):
        try:
            self.progress.emit("Подключение к tbankrot.ru...")
            self.progress_percent.emit(3)
            client = TBankrotClient(diagnostics=True)

            if self.load_all:
                self.progress.emit("Загрузка всех страниц TBankrot...")

                def report(page: int, _total: int | None, loaded: int):
                    self.progress_percent.emit(min(99, 5 + page))
                    self.progress.emit(f"Загружено {loaded} лотов, страница {page}...")

                def page_loaded(lots_on_page: list[NormalizedLot], page_meta: dict):
                    self._persist_lots(lots_on_page)
                    self.page_loaded.emit(lots_on_page, page_meta)

                lots, meta = client.search_all_lots(
                    self.filters,
                    max_items=self.max_items,
                    progress_cb=report,
                    page_cb=page_loaded,
                    stop_cb=lambda: self._stop_requested,
                )
            else:
                self.progress.emit("Загрузка страницы TBankrot...")
                self.progress_percent.emit(35)
                lots, meta = client.search_filtered_lots(self.filters)
                self.page_loaded.emit(lots, meta)

            self.progress_percent.emit(100)
            self.finished.emit(lots, meta)
        except Exception as e:
            logger.exception("TBankrotSearchWorker error")
            self.error.emit(str(e))


class ImportWorker(QThread):
    finished = Signal(int, int, int) # new, updated, skipped
    progress = Signal(str)
    progress_percent = Signal(int)
    error = Signal(str)

    def __init__(self, file_path: str, region: str):
        super().__init__()
        self.file_path = file_path
        self.region = region

    def run(self):
        try:
            with session_scope() as session:
                # skip_geo=True — сильно ускоряет импорт
                new, upd, skip = import_manual_html(
                    session, 
                    self.file_path, 
                    self.region, 
                    skip_geo=True,          # ← главное ускорение
                    progress_cb=self._report_progress
                )
                self.finished.emit(new, upd, skip)
        except Exception as e:
            logger.error(f"ImportWorker error: {e}")
            self.error.emit(str(e))

    def _report_progress(self, current, total):
        self.progress.emit(f"Обработано: {current}/{total}")

    def _report_progress_percent(self, current, total):
        if total:
            self.progress_percent.emit(int((current / total) * 100))

class AIWorker(QThread):
    finished = Signal(int)
    lot_finished = Signal(int, int)
    lot_failed = Signal(int, str, int)
    progress = Signal(str)
    progress_percent = Signal(int)
    error = Signal(str)

    def __init__(self, appraiser: OpenAIAppraiser, lot_ids: list[int] | None = None, limit: int = 10):
        super().__init__()
        self.appraiser = appraiser
        self.lot_ids = lot_ids
        self.limit = limit

    def run(self):
        try:
            from bankrotai.db import DB_WRITE_LOCK, find_unappraised_lots
            import time

            processed_count = 0
            failed_count = 0

            with session_scope() as session:
                if self.lot_ids:
                    lot_ids = list(self.lot_ids)
                else:
                    lots = find_unappraised_lots(session, limit=self.limit)
                    lot_ids = [lot.id for lot in lots]

            if not lot_ids:
                self.progress_percent.emit(100)
                self.finished.emit(0)
                return

            total = len(lot_ids)
            for i, lot_id in enumerate(lot_ids):
                self.progress_percent.emit(int((i / total) * 100))

                try:
                    with session_scope() as session:
                        lot = session.get(ProcessedLot, lot_id)
                        if not lot:
                            continue
                        self.progress.emit(f"AI Оценка [{i+1}/{total}]: {lot.title[:40]}...")
                        nl = NormalizedLot.from_processed_lot(lot)

                    evaluation = self.appraiser.evaluate(nl)

                    with DB_WRITE_LOCK:
                        with session_scope() as session:
                            lot = session.get(ProcessedLot, lot_id)
                            if not lot:
                                continue
                            apply_evaluation_to_lot(lot, evaluation)
                            try:
                                cache_key = self.appraiser._get_cache_key(nl)
                                self.appraiser._save_evaluation_to_db(session, nl, evaluation, cache_key)
                            except Exception:
                                logger.exception("Failed to save valuation run for lot %s", lot_id)
                            processed_count += 1

                    self.lot_finished.emit(lot_id, processed_count)
                except Exception as lot_error:
                    failed_count += 1
                    message = str(lot_error)
                    logger.exception("AI evaluation failed for lot %s; continuing batch", lot_id)
                    self.progress.emit(
                        f"Ошибка AI оценки лота ID {lot_id}; продолжаю. Ошибок: {failed_count}"
                    )
                    self.lot_failed.emit(lot_id, message, failed_count)

                self.progress_percent.emit(int(((i + 1) / total) * 100))
                if i < total - 1:
                    time.sleep(2)

            self.finished.emit(processed_count)
        except Exception as e:
            logger.error(f"AIWorker error: {e}")
            provider = getattr(getattr(self.appraiser, "provider", None), "provider", "")
            message = str(e)
            if any(marker in message.lower() for marker in ("connection error", "apiconnectionerror", "timed out", "timeout")):
                message = (
                    f"AI connection error ({provider or 'unknown provider'}): {message}\n\n"
                    "Провайдер недоступен из текущей сети. Для NVIDIA часто требуется зарубежный VPN; "
                    "также можно выбрать OmniRoute/Kiro в настройках AI и проверить API-ключ."
                )
            self.error.emit(message)


class GeoWorker(QThread):
    finished = Signal(int)
    progress = Signal(str)
    progress_percent = Signal(int)
    error = Signal(str)

    def __init__(
        self,
        limit: int | None = 500,
        lot_ids: list[int] | None = None,
        refresh_existing: bool = False,
    ):
        super().__init__()
        self.limit = limit
        self.lot_ids = lot_ids
        self.refresh_existing = refresh_existing

    def run(self):
        try:
            from bankrotai.db import DB_WRITE_LOCK
            from bankrotai.geo import apply_lot_geo_result, resolve_lot_geo
            from sqlalchemy import exists

            processed_count = 0

            with session_scope() as session:
                if self.lot_ids:
                    lot_ids = list(self.lot_ids)
                else:
                    stmt = select(ProcessedLot.id).where(
                        ~exists().where(LotGeoSnapshot.lot_id == ProcessedLot.id)
                    )
                    if self.limit:
                        stmt = stmt.limit(self.limit)
                    lot_ids = list(session.scalars(stmt).all())

            if not lot_ids:
                self.progress_percent.emit(100)
                self.finished.emit(0)
                return

            total = len(lot_ids)
            for i, lot_id in enumerate(lot_ids):
                self.progress_percent.emit(int((i / total) * 100))

                with session_scope() as session:
                    lot = session.get(ProcessedLot, lot_id)
                    if not lot:
                        continue
                    cadastral_number = lot.cadastral_number
                    address = lot.address
                    title = lot.title
                    label = (address or cadastral_number or title or "")[:40]

                self.progress.emit(f"Геокодирование [{i+1}/{total}]: {label}...")
                geo_result = resolve_lot_geo(cadastral_number, address)

                with DB_WRITE_LOCK:
                    with session_scope() as session:
                        lot = session.get(ProcessedLot, lot_id)
                        if not lot:
                            continue
                        if self.refresh_existing:
                            session.query(LotGeoSnapshot).filter_by(lot_id=lot.id).delete()
                            session.flush()
                        apply_lot_geo_result(session, lot, geo_result)
                        processed_count += 1

                self.progress_percent.emit(int(((i + 1) / total) * 100))

            self.finished.emit(processed_count)
        except Exception as e:
            logger.error(f"GeoWorker error: {e}")
            self.error.emit(str(e))


class CadastreSearchWorker(QThread):
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self):
        try:
            from bankrotai.geo import CADASTRAL_GEOCODER
            self.finished.emit(CADASTRAL_GEOCODER.search(self.query))
        except Exception as e:
            logger.exception("Cadastre search worker failed")
            self.error.emit(str(e))


NSPD_REFERER = "https://nspd.gov.ru/map?theme_id=1&is_copy_url=true&active_layers=&baseLayerId="
NSPD_WMS_LAYERS = {
    "land": "36048",
    "buildings": "36328",
}


class CadastralWmsProxyHandler(BaseHTTPRequestHandler):
    timeout = 12

    def log_message(self, format, *args):
        logger.debug("WMS proxy: " + format, *args)

    def do_GET(self):
        parsed = urlparse(self.path)
        layer_key = parsed.path.rsplit("/", 1)[-1]
        layer_id = NSPD_WMS_LAYERS.get(layer_key)

        if not layer_id:
            self.send_error(404, "Unknown cadastral layer")
            return

        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params.setdefault("SERVICE", "WMS")
        params.setdefault("VERSION", "1.3.0")
        params.setdefault("REQUEST", "GetMap")
        params.setdefault("FORMAT", "image/png")
        params.setdefault("TRANSPARENT", "true")
        params["LAYERS"] = layer_id

        upstream = f"https://nspd.gov.ru/api/aeggis/v4/{layer_id}/wms?{urlencode(params)}"
        headers = {
            "Referer": NSPD_REFERER,
            "User-Agent": "Mozilla/5.0 BankrotAI/1.0",
            "Accept": "image/png,image/*,*/*;q=0.8",
        }

        try:
            import requests
            requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
            response = requests.get(upstream, headers=headers, timeout=(3.05, 8), verify=False)
            response.raise_for_status()
        except Exception as e:
            logger.warning("NSPD WMS request failed for %s: %s", layer_key, e)
            self.send_response(204)
            self.end_headers()
            return

        content_type = response.headers.get("Content-Type", "image/png")
        self.send_response(response.status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(response.content)


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BankrotAI - Аналитика торгов")
        self.resize(1300, 900)
        self.current_selected_lot_id = None
        self._appraiser = None
        self.cadastre_search_worker = None
        self.cadastral_wms_proxy = None
        self.cadastral_wms_proxy_port = None
        self.torgi_results: list[NormalizedLot] = []
        self.torgi_meta: dict = {}
        self.torgi_current_page = 1
        self.torgi_unsupported_warnings: list[str] = []
        self.tbankrot_results: list[NormalizedLot] = []
        self.tbankrot_meta: dict = {}
        self.tbankrot_current_page = 1
        self._last_tbankrot_sort_column = None
        self._last_tbankrot_sort_order = Qt.AscendingOrder
        init_db()
        self.start_cadastral_wms_proxy()
        self.init_statusbar()
        self.init_ui()
        self.update_dashboard()

    def start_cadastral_wms_proxy(self):
        if self.cadastral_wms_proxy_port:
            return

        port = _free_local_port()
        server = ThreadingHTTPServer(("127.0.0.1", port), CadastralWmsProxyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        self.cadastral_wms_proxy = server
        self.cadastral_wms_proxy_port = port
        logger.info("Local cadastral WMS proxy started on 127.0.0.1:%s", port)

    def init_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(15)
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.status_bar.showMessage("Система готова")

    def start_task_progress(self, key: str, label_text: str):
        if not hasattr(self, "task_progress_widgets"):
            self.task_progress_widgets = {}
        self.finish_task_progress(key)
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(label_text)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setMaximumHeight(15)
        bar.setMaximumWidth(150)
        layout.addWidget(label)
        layout.addWidget(bar)
        self.status_bar.addPermanentWidget(widget)
        self.task_progress_widgets[key] = (widget, bar)

    def update_task_progress(self, key: str, value: int):
        item = getattr(self, "task_progress_widgets", {}).get(key)
        if item:
            item[1].setValue(max(0, min(100, int(value))))

    def finish_task_progress(self, key: str):
        item = getattr(self, "task_progress_widgets", {}).pop(key, None)
        if not item:
            return
        widget, _bar = item
        self.status_bar.removeWidget(widget)
        widget.deleteLater()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #dcdcdc; top: -1px; background: white; }
            QTabBar::tab { background: #f0f0f0; border: 1px solid #dcdcdc; padding: 10px 20px; min-width: 120px; }
            QTabBar::tab:selected { background: white; border-bottom-color: white; font-weight: bold; }
        """)
        main_layout.addWidget(self.tabs)

        # 1. Torgi.gov.ru Search Tab
        self.dash_tab = QWidget()
        self.init_dash_tab()
        self.tabs.addTab(self.dash_tab, "Поиск ГИС Торги")

        # 2. TBankrot Search Tab
        self.tbankrot_tab = QWidget()
        self.init_tbankrot_tab()
        self.tabs.addTab(self.tbankrot_tab, "Поиск Т Банкрот")

        # 3. Registry Tab
        self.registry_tab = QWidget()
        self.init_registry_tab()
        self.tabs.addTab(self.registry_tab, "📋 Реестр лотов")

        # 4. Map Tab
        self.map_tab = QWidget()
        self.init_map_tab()
        self.tabs.addTab(self.map_tab, "🗺️ Карта и Кадастр")

        # 5. Yandex Map Tab
        self.yandex_map_tab = QWidget()
        self.init_yandex_map_tab()
        self.tabs.addTab(self.yandex_map_tab, "Карта и кадастр Яндекс")

        # 6. Tools Tab
        self.tools_tab = QWidget()
        self.init_tools_tab()
        self.tabs.addTab(self.tools_tab, "🛠️ Инструменты")

    def init_dash_tab(self):
        layout = QVBoxLayout(self.dash_tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._last_sort_column = None
        self._last_sort_order = Qt.AscendingOrder

        top_layout = QHBoxLayout()
        title = QLabel("Поиск ГИС Торги (torgi.gov.ru)")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #143370;")
        top_layout.addWidget(title)
        top_layout.addStretch()
        refresh_btn = QPushButton("🔄")
        refresh_btn.setToolTip("Обновить статистику")
        refresh_btn.setFixedSize(42, 34)
        refresh_btn.clicked.connect(self.update_dashboard)
        top_layout.addWidget(refresh_btn)
        layout.addLayout(top_layout)

        self.stats_label = QLabel("Загрузка статистики...")
        self.stats_label.setWordWrap(True)
        self.stats_label.setMaximumHeight(118)
        self.stats_label.setStyleSheet("""
            QLabel {
                font-size: 13px;
                padding: 12px 14px;
                background: #f7f9fc;
                border: 1px solid #dfe7f3;
                border-radius: 8px;
                color: #27364f;
            }
        """)
        layout.addWidget(self.stats_label)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)

        left_panel = QWidget()
        left_panel.setMinimumWidth(380)
        left_panel.setMaximumWidth(430)
        left_panel.setStyleSheet("QWidget { background: white; }")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(8)
        splitter.addWidget(left_panel)

        filter_scroll = QScrollArea()
        filter_scroll.setWidgetResizable(True)
        filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        filter_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        filter_scroll.setStyleSheet("QScrollArea { border: 1px solid #dfe7f3; border-radius: 8px; background: white; }")
        filter_widget = QWidget()
        filter_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        filter_layout = QVBoxLayout(filter_widget)
        filter_layout.setContentsMargins(10, 10, 10, 10)
        filter_layout.setSpacing(8)
        filter_scroll.setWidget(filter_widget)
        left_layout.addWidget(filter_scroll, 1)

        def line(placeholder: str = "") -> QLineEdit:
            widget = QLineEdit()
            widget.setPlaceholderText(placeholder)
            widget.setMinimumHeight(32)
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            return widget

        def combo(options: list[tuple[str, str | None]], editable: bool = False) -> QComboBox:
            widget = WheelSafeComboBox()
            widget.setEditable(editable)
            widget.setMinimumHeight(32)
            widget.setMaxVisibleItems(14)
            widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            widget.setMinimumContentsLength(14)
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for label, value in options:
                widget.addItem(label, value)
                if value == "__group__":
                    item = widget.model().item(widget.count() - 1)
                    if item:
                        item.setEnabled(False)
            widget.setStyleSheet("""
                QComboBox {
                    background: white;
                    border: 1px solid #cfd8e6;
                    border-radius: 5px;
                    padding: 4px 28px 4px 8px;
                    color: #1f2d3d;
                }
                QComboBox:hover { border-color: #115dee; }
                QComboBox::drop-down {
                    subcontrol-origin: padding;
                    subcontrol-position: top right;
                    width: 24px;
                    border-left: 1px solid #dfe7f3;
                }
                QComboBox QAbstractItemView {
                    background: white;
                    border: 1px solid #cfd8e6;
                    selection-background-color: #eaf1ff;
                    selection-color: #143370;
                    outline: none;
                }
            """)
            return widget

        def section(title_text: str, expanded: bool = False) -> CollapsibleSection:
            item = CollapsibleSection(title_text, expanded=expanded)
            filter_layout.addWidget(item)
            return item

        def add_labeled_field(target_layout: QVBoxLayout, label_text: str, widget: QWidget):
            label = QLabel(label_text)
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 12px; color: #143370; font-weight: 600;")
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            target_layout.addWidget(label)
            target_layout.addWidget(widget)

        def two_field_row(left: QWidget, right: QWidget) -> QHBoxLayout:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            left.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            row.addWidget(left)
            row.addWidget(right)
            return row

        main_section = section("Основной поиск", expanded=True)
        self.torgi_search_input = line("Введите параметр поиска")
        add_labeled_field(main_section.content_layout, "Введите параметр поиска", self.torgi_search_input)
        self.torgi_load_all_checkbox = QCheckBox("Загрузить все страницы результата")
        self.torgi_load_all_checkbox.setChecked(True)
        self.torgi_load_all_checkbox.setToolTip(
            "Если включено, программа пройдет по всем страницам API и покажет все найденные лоты."
        )
        main_section.addWidget(self.torgi_load_all_checkbox)
        self.torgi_max_items_input = line("например 5000")
        self.torgi_max_items_input.setText("5000")
        add_labeled_field(main_section.content_layout, "Лимит загрузки", self.torgi_max_items_input)

        deal_section = section("Вид сделки")
        self.torgi_type_transaction_combo = combo([
            ("Не выбрано", None),
            ("Продажа", "SALE"),
            ("Аренда", "RENT"),
        ])
        add_labeled_field(deal_section.content_layout, "Вид сделки", self.torgi_type_transaction_combo)

        price_section = section("Начальная цена")
        self.torgi_price_min_input = line("от")
        self.torgi_price_max_input = line("до")
        label = QLabel("Начальная цена")
        label.setWordWrap(True)
        label.setStyleSheet("font-size: 12px; color: #143370; font-weight: 600;")
        price_section.addWidget(label)
        price_section.addLayout(two_field_row(self.torgi_price_min_input, self.torgi_price_max_input))

        location_section = section("Местонахождение")
        region_options = [("Не выбрано", None)]
        for district, region_names in TorgiGovClient.SUBJECT_RF_DISTRICTS.items():
            region_options.append((district, "__group__"))
            region_options.extend(
                (f"  {name}", TorgiGovClient.SUBJECT_RF_CODES[name])
                for name in region_names
                if name in TorgiGovClient.SUBJECT_RF_CODES
            )
        self.torgi_subject_combo = combo(region_options, editable=True)
        add_labeled_field(location_section.content_layout, "Субъект местонахождения имущества", self.torgi_subject_combo)
        self.torgi_fias_input = line("Город, район, адрес")
        add_labeled_field(location_section.content_layout, "Местонахождение имущества", self.torgi_fias_input)
        self.torgi_ownership_combo = combo([
            ("Не выбрано", None),
            ("Федеральная", "FEDERAL"),
            ("Региональная", "REGIONAL"),
            ("Муниципальная", "MUNICIPAL"),
        ])
        add_labeled_field(location_section.content_layout, "Форма собственности", self.torgi_ownership_combo)

        category_section = section("Категория имущества")
        self.torgi_category_combo = combo(
            [("Не выбрано", None)] + [(label, code) for code, label in TorgiGovClient.CATEGORY_CODE_LABELS.items()]
        )
        add_labeled_field(category_section.content_layout, "Категория имущества", self.torgi_category_combo)

        lot_section = section("Параметры лота")
        self.torgi_lot_status_combo = combo([
            ("Не выбрано", None),
            ("Активные", TorgiGovClient.DEFAULT_LOT_STATUS),
            ("Прием заявок", "APPLICATIONS_SUBMISSION"),
            ("Определение участников", "DETERMINING_PARTICIPANTS"),
            ("Проведение торгов", "BIDDING"),
            ("Подведение итогов", "SUMMING_UP"),
            ("Завершен", "COMPLETED"),
            ("Несостоявшийся", "FAILED"),
        ])
        self._set_combo_data(self.torgi_lot_status_combo, TorgiGovClient.DEFAULT_LOT_STATUS)
        self.torgi_status_combo = self.torgi_lot_status_combo
        add_labeled_field(lot_section.content_layout, "Статус лота", self.torgi_lot_status_combo)
        self.torgi_currency_combo = combo([("Не выбрано", None), ("RUB", "RUB"), ("USD", "USD"), ("EUR", "EUR")])
        add_labeled_field(lot_section.content_layout, "Валюта", self.torgi_currency_combo)
        self.torgi_price_fin_from_input = line("от")
        self.torgi_price_fin_to_input = line("до")
        label = QLabel("Итоговая цена")
        label.setWordWrap(True)
        label.setStyleSheet("font-size: 12px; color: #143370; font-weight: 600;")
        lot_section.addWidget(label)
        lot_section.addLayout(two_field_row(self.torgi_price_fin_from_input, self.torgi_price_fin_to_input))
        self.torgi_is_msp_checkbox = QCheckBox("Торги среди МСП")
        lot_section.addWidget(self.torgi_is_msp_checkbox)

        notice_section = section("Извещение")
        self.torgi_notice_status_combo = combo([
            ("Не выбрано", None),
            ("Опубликованные", "PUBLISHED"),
            ("Завершенные", "COMPLETED"),
            ("Отмененные", "CANCELED"),
        ])
        add_labeled_field(notice_section.content_layout, "Статус извещения", self.torgi_notice_status_combo)
        self.torgi_notice_number_input = line("Номер извещения")
        add_labeled_field(notice_section.content_layout, "Номер извещения", self.torgi_notice_number_input)
        self.torgi_etp_input = line("Код ЭТП")
        add_labeled_field(notice_section.content_layout, "Электронная площадка", self.torgi_etp_input)
        self.torgi_publish_from_input = line("от YYYY-MM-DD")
        self.torgi_publish_to_input = line("до YYYY-MM-DD")
        label = QLabel("Дата публикации")
        label.setWordWrap(True)
        label.setStyleSheet("font-size: 12px; color: #143370; font-weight: 600;")
        notice_section.addWidget(label)
        notice_section.addLayout(two_field_row(self.torgi_publish_from_input, self.torgi_publish_to_input))
        self.torgi_bidd_end_from_input = line("от YYYY-MM-DD")
        self.torgi_bidd_end_to_input = line("до YYYY-MM-DD")
        label = QLabel("Дата окончания подачи заявок")
        label.setWordWrap(True)
        label.setStyleSheet("font-size: 12px; color: #143370; font-weight: 600;")
        notice_section.addWidget(label)
        notice_section.addLayout(two_field_row(self.torgi_bidd_end_from_input, self.torgi_bidd_end_to_input))
        self.torgi_auction_from_input = line("от YYYY-MM-DD")
        self.torgi_auction_to_input = line("до YYYY-MM-DD")
        label = QLabel("Дата проведения торгов")
        label.setWordWrap(True)
        label.setStyleSheet("font-size: 12px; color: #143370; font-weight: 600;")
        notice_section.addWidget(label)
        notice_section.addLayout(two_field_row(self.torgi_auction_from_input, self.torgi_auction_to_input))
        self.torgi_npa_input = line("Нормативный правовой акт")
        add_labeled_field(notice_section.content_layout, "Нормативный правовой акт", self.torgi_npa_input)
        self.torgi_bidd_type_combo = combo([("Не выбрано", None), ("Электронный аукцион", "ELECTRONIC_AUCTION")])
        add_labeled_field(notice_section.content_layout, "Вид торгов", self.torgi_bidd_type_combo)
        self.torgi_bidd_form_combo = combo([("Не выбрано", None), ("Открытая", "OPEN"), ("Закрытая", "CLOSED")])
        add_labeled_field(notice_section.content_layout, "Форма проведения торгов", self.torgi_bidd_form_combo)
        self.torgi_is_stopped_checkbox = QCheckBox("Наличие приостановленных торгов")
        notice_section.addWidget(self.torgi_is_stopped_checkbox)

        organizer_section = section("Организатор торгов")
        self.torgi_organizer_name_input = line("Наименование")
        self.torgi_organizer_inn_input = line("ИНН")
        self.torgi_organizer_kpp_input = line("КПП")
        self.torgi_organizer_ogrn_input = line("ОГРН/ОГРНИП")
        add_labeled_field(organizer_section.content_layout, "Наименование", self.torgi_organizer_name_input)
        add_labeled_field(organizer_section.content_layout, "ИНН", self.torgi_organizer_inn_input)
        add_labeled_field(organizer_section.content_layout, "КПП", self.torgi_organizer_kpp_input)
        add_labeled_field(organizer_section.content_layout, "ОГРН/ОГРНИП", self.torgi_organizer_ogrn_input)

        right_section = section("Правообладатель")
        self.torgi_right_holder_name_input = line("Наименование")
        self.torgi_right_holder_inn_input = line("ИНН")
        self.torgi_right_holder_kpp_input = line("КПП")
        self.torgi_right_holder_ogrn_input = line("ОГРН")
        add_labeled_field(right_section.content_layout, "Наименование", self.torgi_right_holder_name_input)
        add_labeled_field(right_section.content_layout, "ИНН", self.torgi_right_holder_inn_input)
        add_labeled_field(right_section.content_layout, "КПП", self.torgi_right_holder_kpp_input)
        add_labeled_field(right_section.content_layout, "ОГРН", self.torgi_right_holder_ogrn_input)
        self.torgi_rh_gov_prt_checkbox = QCheckBox("Правообладатель включен в перечень компаний с гос. участием")
        right_section.addWidget(self.torgi_rh_gov_prt_checkbox)

        appeals_section = section("Жалобы")
        self.torgi_has_appeals_checkbox = QCheckBox("Наличие жалоб")
        self.torgi_has_solutions_checkbox = QCheckBox("Наличие решений")
        self.torgi_has_prescriptions_checkbox = QCheckBox("Наличие предписаний")
        self.torgi_amo_org_input = line("Орган контроля")
        appeals_section.addWidget(self.torgi_has_appeals_checkbox)
        appeals_section.addWidget(self.torgi_has_solutions_checkbox)
        appeals_section.addWidget(self.torgi_has_prescriptions_checkbox)
        add_labeled_field(appeals_section.content_layout, "Орган контроля", self.torgi_amo_org_input)

        attachment_section = section("Поиск в прикрепленных файлах")
        self.torgi_attachment_input = line("Фраза для поиска")
        add_labeled_field(attachment_section.content_layout, "Фраза для поиска", self.torgi_attachment_input)
        self.torgi_match_phrase_checkbox = QCheckBox("Точное совпадение")
        attachment_section.addWidget(self.torgi_match_phrase_checkbox)
        filter_layout.addStretch()

        bottom_bar = QFrame()
        bottom_bar.setFrameShape(QFrame.NoFrame)
        bottom_layout = QVBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(0, 8, 0, 0)
        bottom_layout.setSpacing(6)
        self.torgi_search_btn = QPushButton("🔎 Найти онлайн")
        self.torgi_search_btn.setMinimumHeight(38)
        self.torgi_search_btn.setStyleSheet("""
            QPushButton {
                background: #115dee;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 700;
                padding: 8px;
            }
            QPushButton:hover { background: #0d4fc9; }
            QPushButton:disabled { background: #9bb6ea; }
        """)
        self.torgi_search_btn.clicked.connect(self.run_torgi_search)
        self.torgi_excel_search_btn = QPushButton("Поиск через эксель")
        self.torgi_excel_search_btn.setMinimumHeight(38)
        self.torgi_excel_search_btn.setStyleSheet("""
            QPushButton {
                background: #1f9d55;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 700;
                padding: 8px;
            }
            QPushButton:hover { background: #188447; }
            QPushButton:disabled { background: #9bd3b4; }
        """)
        self.torgi_excel_search_btn.clicked.connect(self.run_torgi_excel_search)
        self.torgi_stop_btn = QPushButton("Остановить поиск")
        self.torgi_stop_btn.setEnabled(False)
        self.torgi_stop_btn.clicked.connect(self.stop_torgi_search)
        self.torgi_clear_btn = QPushButton("Очистить поиск")
        self.torgi_clear_btn.clicked.connect(self.clear_torgi_filters)
        self.torgi_map_btn = QPushButton("Показать на карте")
        self.torgi_map_btn.setEnabled(False)
        bottom_layout.addWidget(self.torgi_search_btn)
        bottom_layout.addWidget(self.torgi_excel_search_btn)
        bottom_layout.addWidget(self.torgi_stop_btn)
        bottom_layout.addWidget(self.torgi_clear_btn)
        bottom_layout.addWidget(self.torgi_map_btn)
        left_layout.addWidget(bottom_bar, 0)

        self.torgi_unsupported_inputs = {
            "Итоговая цена": [self.torgi_price_fin_from_input, self.torgi_price_fin_to_input],
            "Нормативный правовой акт": [self.torgi_npa_input],
            "Приостановленные торги": [self.torgi_is_stopped_checkbox],
            "КПП/ОГРН организатора": [self.torgi_organizer_kpp_input, self.torgi_organizer_ogrn_input],
            "КПП/ОГРН правообладателя": [self.torgi_right_holder_kpp_input, self.torgi_right_holder_ogrn_input],
            "Гос. участие правообладателя": [self.torgi_rh_gov_prt_checkbox],
            "Жалобы/решения/предписания": [
                self.torgi_has_appeals_checkbox,
                self.torgi_has_solutions_checkbox,
                self.torgi_has_prescriptions_checkbox,
                self.torgi_amo_org_input,
            ],
        }

        results_panel = QWidget()
        results_layout = QVBoxLayout(results_panel)
        results_layout.setContentsMargins(14, 0, 0, 0)
        results_layout.setSpacing(10)
        splitter.addWidget(results_panel)

        header_layout = QHBoxLayout()
        self.torgi_status_label = QLabel("Найдено 0, страница 1, источник torgi.gov.ru")
        self.torgi_status_label.setStyleSheet("font-size: 13px; color: #60769f;")
        header_layout.addWidget(self.torgi_status_label)
        header_layout.addStretch()
        self.torgi_debug_btn = QPushButton("Параметры запроса")
        self.torgi_debug_btn.clicked.connect(self.show_torgi_request_diagnostics)
        header_layout.addWidget(self.torgi_debug_btn)
        self.torgi_open_site_btn = QPushButton("Открыть на torgi.gov.ru")
        self.torgi_open_site_btn.clicked.connect(self.open_torgi_site)
        header_layout.addWidget(self.torgi_open_site_btn)
        results_layout.addLayout(header_layout)

        self.active_filters_widget = QWidget()
        self.active_filters_layout = QHBoxLayout(self.active_filters_widget)
        self.active_filters_layout.setContentsMargins(0, 0, 0, 0)
        self.active_filters_layout.setSpacing(6)
        self.active_filters_layout.setAlignment(Qt.AlignLeft)
        results_layout.addWidget(self.active_filters_widget)

        self.torgi_results_table = QTableWidget()
        self.torgi_table = self.torgi_results_table
        self.torgi_results_table.setColumnCount(10)
        self.torgi_results_table.setHorizontalHeaderLabels([
            "В базе",
            "ID / Извещение",
            "Название",
            "Категория",
            "Регион / адрес",
            "Начальная цена",
            "Статус",
            "Дата публикации",
            "Окончание заявок",
            "Ссылка",
        ])
        header = self.torgi_results_table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self.on_torgi_header_clicked)
        self.torgi_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.torgi_results_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.torgi_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.torgi_results_table.setAlternatingRowColors(True)
        self.torgi_results_table.setSortingEnabled(True)
        self.torgi_results_table.cellClicked.connect(self.open_torgi_link_cell)
        self.torgi_results_table.cellDoubleClicked.connect(self.open_torgi_result_url)
        results_layout.addWidget(self.torgi_results_table, 1)

        actions = QHBoxLayout()
        self.torgi_import_selected_btn = QPushButton("Импортировать выбранные в базу")
        self.torgi_import_selected_btn.clicked.connect(self.import_selected_torgi_lots)
        actions.addWidget(self.torgi_import_selected_btn)
        self.torgi_import_all_btn = QPushButton("Импортировать все найденные")
        self.torgi_import_all_btn.clicked.connect(self.import_all_torgi_lots)
        actions.addWidget(self.torgi_import_all_btn)
        actions.addStretch()
        self.torgi_prev_btn = QPushButton("Предыдущая страница")
        self.torgi_prev_btn.clicked.connect(self.search_torgi_prev_page)
        self.torgi_prev_btn.setEnabled(False)
        actions.addWidget(self.torgi_prev_btn)
        self.torgi_next_btn = QPushButton("Следующая страница")
        self.torgi_next_btn.clicked.connect(self.search_torgi_next_page)
        self.torgi_next_btn.setEnabled(False)
        actions.addWidget(self.torgi_next_btn)
        results_layout.addLayout(actions)

        for signal_source in (
            self.torgi_search_input.textChanged,
            self.torgi_price_min_input.textChanged,
            self.torgi_price_max_input.textChanged,
            self.torgi_subject_combo.currentIndexChanged,
            self.torgi_category_combo.currentIndexChanged,
            self.torgi_notice_status_combo.currentIndexChanged,
            self.torgi_lot_status_combo.currentIndexChanged,
        ):
            signal_source.connect(lambda *_: self.update_active_filter_chips())

        splitter.setSizes([400, 900])
        self.restore_torgi_filter_state()
        self.update_active_filter_chips()

    def init_tbankrot_tab(self):
        layout = QVBoxLayout(self.tbankrot_tab)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        top_layout = QHBoxLayout()
        title = QLabel("Поиск Т Банкрот (tbankrot.ru)")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #143370;")
        top_layout.addWidget(title)
        top_layout.addStretch()
        layout.addLayout(top_layout)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)

        left_panel = QWidget()
        left_panel.setMinimumWidth(380)
        left_panel.setMaximumWidth(430)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(8)
        splitter.addWidget(left_panel)

        filter_scroll = QScrollArea()
        filter_scroll.setWidgetResizable(True)
        filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        filter_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        filter_scroll.setStyleSheet("QScrollArea { border: 1px solid #dfe7f3; border-radius: 8px; background: white; }")
        filter_widget = QWidget()
        filter_layout = QVBoxLayout(filter_widget)
        filter_layout.setContentsMargins(10, 10, 10, 10)
        filter_layout.setSpacing(8)
        filter_scroll.setWidget(filter_widget)
        left_layout.addWidget(filter_scroll, 1)

        def line(placeholder: str = "") -> QLineEdit:
            widget = QLineEdit()
            widget.setPlaceholderText(placeholder)
            widget.setMinimumHeight(32)
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            return widget

        def combo(options: list[tuple[str, str | None]]) -> QComboBox:
            widget = WheelSafeComboBox()
            widget.setMinimumHeight(32)
            widget.setMaxVisibleItems(16)
            widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            widget.setMinimumContentsLength(16)
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for label, value in options:
                widget.addItem(label, value)
            return widget

        def section(title_text: str, expanded: bool = False) -> CollapsibleSection:
            item = CollapsibleSection(title_text, expanded=expanded)
            filter_layout.addWidget(item)
            return item

        def add_labeled_field(target_layout: QVBoxLayout, label_text: str, widget: QWidget):
            label = QLabel(label_text)
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 12px; color: #143370; font-weight: 600;")
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            target_layout.addWidget(label)
            target_layout.addWidget(widget)

        def two_field_row(left: QWidget, right: QWidget) -> QHBoxLayout:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(left)
            row.addWidget(right)
            return row

        main_section = section("Основной поиск", expanded=True)
        self.tbankrot_search_input = line("Поиск")
        add_labeled_field(main_section.content_layout, "Поиск", self.tbankrot_search_input)
        self.tbankrot_load_all_checkbox = QCheckBox("Загрузить все страницы результата")
        self.tbankrot_load_all_checkbox.setChecked(True)
        main_section.content_layout.addWidget(self.tbankrot_load_all_checkbox)
        self.tbankrot_max_items_input = line("5000")
        self.tbankrot_max_items_input.setText("5000")
        add_labeled_field(main_section.content_layout, "Лимит лотов при загрузке всех страниц", self.tbankrot_max_items_input)

        price_section = section("Цена и регион", expanded=True)
        self.tbankrot_price_min_input = line("Цена от")
        self.tbankrot_price_max_input = line("Цена до")
        price_section.content_layout.addLayout(two_field_row(self.tbankrot_price_min_input, self.tbankrot_price_max_input))
        region_options = [("Не выбрано", None)]
        region_options.extend((label, code) for code, label in sorted(TBankrotClient.REGION_LABELS.items(), key=lambda item: item[1]))
        self.tbankrot_region_combo = combo(region_options)
        add_labeled_field(price_section.content_layout, "Регион", self.tbankrot_region_combo)

        trade_section = section("Торги", expanded=True)
        self.tbankrot_trade_type_combo = combo([
            ("Не выбрано", None),
            ("Аукцион", "auction"),
            ("Публичное предложение", "public"),
        ])
        add_labeled_field(trade_section.content_layout, "Тип торгов", self.tbankrot_trade_type_combo)
        self.tbankrot_lot_number_input = line("Номер лота")
        add_labeled_field(trade_section.content_layout, "Номер лота", self.tbankrot_lot_number_input)
        self.tbankrot_photo_only_checkbox = QCheckBox("Только с фото")
        trade_section.content_layout.addWidget(self.tbankrot_photo_only_checkbox)
        self.tbankrot_show_closed_checkbox = QCheckBox("Показывать завершенные")
        trade_section.content_layout.addWidget(self.tbankrot_show_closed_checkbox)
        self.tbankrot_show_paused_checkbox = QCheckBox("Показывать приостановленные")
        trade_section.content_layout.addWidget(self.tbankrot_show_paused_checkbox)

        parties_section = section("Участники и слова")
        self.tbankrot_debtor_input = line("Должник")
        add_labeled_field(parties_section.content_layout, "Должник", self.tbankrot_debtor_input)
        self.tbankrot_auction_manager_input = line("Арбитражный управляющий")
        add_labeled_field(parties_section.content_layout, "Арбитражный управляющий", self.tbankrot_auction_manager_input)
        self.tbankrot_organizer_input = line("Организатор")
        add_labeled_field(parties_section.content_layout, "Организатор", self.tbankrot_organizer_input)
        self.tbankrot_stop_words_input = line("Стоп-слова")
        add_labeled_field(parties_section.content_layout, "Стоп-слова", self.tbankrot_stop_words_input)
        filter_layout.addStretch()

        bottom_bar = QFrame()
        bottom_layout = QVBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(0, 8, 0, 0)
        bottom_layout.setSpacing(6)
        self.tbankrot_search_btn = QPushButton("Найти на TBankrot")
        self.tbankrot_search_btn.setMinimumHeight(38)
        self.tbankrot_search_btn.setStyleSheet("""
            QPushButton {
                background: #115dee;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 700;
                padding: 8px;
            }
            QPushButton:hover { background: #0d4fc9; }
            QPushButton:disabled { background: #9bb6ea; }
        """)
        self.tbankrot_search_btn.clicked.connect(self.run_tbankrot_search)
        self.tbankrot_stop_btn = QPushButton("Остановить поиск")
        self.tbankrot_stop_btn.setEnabled(False)
        self.tbankrot_stop_btn.clicked.connect(self.stop_tbankrot_search)
        self.tbankrot_clear_btn = QPushButton("Очистить поиск")
        self.tbankrot_clear_btn.clicked.connect(self.clear_tbankrot_filters)
        bottom_layout.addWidget(self.tbankrot_search_btn)
        bottom_layout.addWidget(self.tbankrot_stop_btn)
        bottom_layout.addWidget(self.tbankrot_clear_btn)
        left_layout.addWidget(bottom_bar, 0)

        results_panel = QWidget()
        results_layout = QVBoxLayout(results_panel)
        results_layout.setContentsMargins(14, 0, 0, 0)
        results_layout.setSpacing(10)
        splitter.addWidget(results_panel)

        header_layout = QHBoxLayout()
        self.tbankrot_status_label = QLabel("Найдено 0, страница 1, источник tbankrot.ru")
        self.tbankrot_status_label.setStyleSheet("font-size: 13px; color: #60769f;")
        header_layout.addWidget(self.tbankrot_status_label)
        header_layout.addStretch()
        self.tbankrot_debug_btn = QPushButton("Параметры запроса")
        self.tbankrot_debug_btn.clicked.connect(self.show_tbankrot_request_diagnostics)
        header_layout.addWidget(self.tbankrot_debug_btn)
        self.tbankrot_open_site_btn = QPushButton("Открыть на tbankrot.ru")
        self.tbankrot_open_site_btn.clicked.connect(self.open_tbankrot_site)
        header_layout.addWidget(self.tbankrot_open_site_btn)
        results_layout.addLayout(header_layout)

        self.tbankrot_active_filters_widget = QWidget()
        self.tbankrot_active_filters_layout = QHBoxLayout(self.tbankrot_active_filters_widget)
        self.tbankrot_active_filters_layout.setContentsMargins(0, 0, 0, 0)
        self.tbankrot_active_filters_layout.setSpacing(6)
        self.tbankrot_active_filters_layout.setAlignment(Qt.AlignLeft)
        results_layout.addWidget(self.tbankrot_active_filters_widget)

        self.tbankrot_results_table = QTableWidget()
        self.tbankrot_results_table.setColumnCount(10)
        self.tbankrot_results_table.setHorizontalHeaderLabels([
            "В базе",
            "ID",
            "Название",
            "Категория",
            "Регион / адрес",
            "Цена",
            "Статус",
            "Дата публикации",
            "Окончание заявок",
            "Ссылка",
        ])
        header = self.tbankrot_results_table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self.on_tbankrot_header_clicked)
        self.tbankrot_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbankrot_results_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tbankrot_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbankrot_results_table.setAlternatingRowColors(True)
        self.tbankrot_results_table.setSortingEnabled(True)
        self.tbankrot_results_table.cellClicked.connect(self.open_tbankrot_link_cell)
        self.tbankrot_results_table.cellDoubleClicked.connect(self.open_tbankrot_result_url)
        results_layout.addWidget(self.tbankrot_results_table, 1)

        actions = QHBoxLayout()
        self.tbankrot_import_selected_btn = QPushButton("Импортировать выбранные в базу")
        self.tbankrot_import_selected_btn.clicked.connect(self.import_selected_tbankrot_lots)
        actions.addWidget(self.tbankrot_import_selected_btn)
        self.tbankrot_import_all_btn = QPushButton("Импортировать все найденные")
        self.tbankrot_import_all_btn.clicked.connect(self.import_all_tbankrot_lots)
        actions.addWidget(self.tbankrot_import_all_btn)
        actions.addStretch()
        self.tbankrot_prev_btn = QPushButton("Предыдущая страница")
        self.tbankrot_prev_btn.clicked.connect(self.search_tbankrot_prev_page)
        self.tbankrot_prev_btn.setEnabled(False)
        actions.addWidget(self.tbankrot_prev_btn)
        self.tbankrot_next_btn = QPushButton("Следующая страница")
        self.tbankrot_next_btn.clicked.connect(self.search_tbankrot_next_page)
        self.tbankrot_next_btn.setEnabled(False)
        actions.addWidget(self.tbankrot_next_btn)
        results_layout.addLayout(actions)

        for signal_source in (
            self.tbankrot_search_input.textChanged,
            self.tbankrot_price_min_input.textChanged,
            self.tbankrot_price_max_input.textChanged,
            self.tbankrot_region_combo.currentIndexChanged,
            self.tbankrot_trade_type_combo.currentIndexChanged,
            self.tbankrot_lot_number_input.textChanged,
        ):
            signal_source.connect(lambda *_: self.update_tbankrot_filter_chips())

        splitter.setSizes([400, 900])
        self.restore_tbankrot_filter_state()
        self.update_tbankrot_filter_chips()
        self.render_tbankrot_results()

    def _combo_value(self, widget: QComboBox) -> str | None:
        data = widget.currentData()
        if data not in (None, ""):
            return str(data)
        text = widget.currentText().strip()
        if not text or text == "Не выбрано":
            return None
        return text

    def _line_text(self, widget: QLineEdit) -> str | None:
        value = widget.text().strip()
        return value or None

    def _line_float(self, widget: QLineEdit) -> float | None:
        value = widget.text().strip().replace(" ", "").replace(",", ".")
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            raise ValueError(f"Некорректное числовое значение: {widget.text()}")

    def _line_int_or_none(self, widget: QLineEdit) -> int | None:
        value = widget.text().strip().replace(" ", "")
        if not value:
            return None
        try:
            parsed = int(value)
            return parsed if parsed > 0 else None
        except ValueError:
            raise ValueError(f"Некорректное целое значение: {widget.text()}")

    def _set_combo_data(self, widget: QComboBox, value: str | None) -> None:
        if value in (None, ""):
            index = widget.findData(None)
        else:
            index = widget.findData(value)
        if index >= 0:
            widget.setCurrentIndex(index)
        elif widget.isEditable() and value:
            widget.setEditText(str(value))

    def _collect_unsupported_torgi_warnings(self) -> list[str]:
        warnings = []
        for label, widgets in getattr(self, "torgi_unsupported_inputs", {}).items():
            used = False
            for widget in widgets:
                if isinstance(widget, QLineEdit) and widget.text().strip():
                    used = True
                elif isinstance(widget, QCheckBox) and widget.isChecked():
                    used = True
            if used:
                warnings.append(f"Фильтр '{label}' пока отображается в GUI, но не передается в онлайн API.")
        return warnings

    def collect_torgi_filters(self, page: int | None = None) -> TorgiGovSearchFilters:
        self.torgi_unsupported_warnings = self._collect_unsupported_torgi_warnings()
        return TorgiGovSearchFilters(
            search_text=self.torgi_search_input.text().strip(),
            type_transaction=self._combo_value(self.torgi_type_transaction_combo),
            price_min=self._line_float(self.torgi_price_min_input),
            price_max=self._line_float(self.torgi_price_max_input),
            subject_rf=self._combo_value(self.torgi_subject_combo),
            fias=self._line_text(self.torgi_fias_input),
            ownership_form=self._combo_value(self.torgi_ownership_combo),
            category_code=self._combo_value(self.torgi_category_combo),
            lot_status=self._combo_value(self.torgi_lot_status_combo),
            currency_code=self._combo_value(self.torgi_currency_combo),
            publish_date_from=self._line_text(self.torgi_publish_from_input),
            publish_date_to=self._line_text(self.torgi_publish_to_input),
            bidd_end_time_from=self._line_text(self.torgi_bidd_end_from_input),
            bidd_end_time_to=self._line_text(self.torgi_bidd_end_to_input),
            auction_start_date_from=self._line_text(self.torgi_auction_from_input),
            auction_start_date_to=self._line_text(self.torgi_auction_to_input),
            notice_number=self._line_text(self.torgi_notice_number_input),
            etp_code=self._line_text(self.torgi_etp_input),
            bidd_type=self._combo_value(self.torgi_bidd_type_combo),
            bidd_form=self._combo_value(self.torgi_bidd_form_combo),
            notice_status=self._combo_value(self.torgi_notice_status_combo),
            organizer_name=self._line_text(self.torgi_organizer_name_input),
            organizer_inn=self._line_text(self.torgi_organizer_inn_input),
            right_holder_name=self._line_text(self.torgi_right_holder_name_input),
            right_holder_inn=self._line_text(self.torgi_right_holder_inn_input),
            attachment_text=self._line_text(self.torgi_attachment_input),
            match_phrase=self.torgi_match_phrase_checkbox.isChecked(),
            is_msp=self.torgi_is_msp_checkbox.isChecked(),
            page=page or self.torgi_current_page,
            page_size=20,
        )

    def save_torgi_filter_state(self) -> None:
        from bankrotai.core import set_app_setting
        state = {
            "search_text": self.torgi_search_input.text().strip(),
            "subject_rf": self._combo_value(self.torgi_subject_combo),
            "price_min": self.torgi_price_min_input.text().strip(),
            "price_max": self.torgi_price_max_input.text().strip(),
            "category_code": self._combo_value(self.torgi_category_combo),
            "lot_status": self._combo_value(self.torgi_lot_status_combo),
        }
        set_app_setting("torgi_gov_last_filters", json.dumps(state, ensure_ascii=False))

    def restore_torgi_filter_state(self) -> None:
        from bankrotai.core import get_app_setting
        raw = get_app_setting("torgi_gov_last_filters", "")
        if not raw:
            return
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            return
        self.torgi_search_input.setText(state.get("search_text", ""))
        self._set_combo_data(self.torgi_subject_combo, state.get("subject_rf"))
        self.torgi_price_min_input.setText(state.get("price_min", ""))
        self.torgi_price_max_input.setText(state.get("price_max", ""))
        self._set_combo_data(self.torgi_category_combo, state.get("category_code"))
        self._set_combo_data(self.torgi_lot_status_combo, state.get("lot_status") or TorgiGovClient.DEFAULT_LOT_STATUS)

    def update_active_filter_chips(self):
        if not hasattr(self, "active_filters_layout"):
            return
        while self.active_filters_layout.count():
            item = self.active_filters_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        chips = self.get_active_filter_chips()
        if not chips:
            label = QLabel("Фильтры не выбраны")
            label.setStyleSheet("color: #7a8699; font-size: 12px;")
            self.active_filters_layout.addWidget(label)
            return

        for label_text, clear_callback in chips:
            chip = QPushButton(f"{label_text}  ×")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet("""
                QPushButton {
                    background: #eaf1ff;
                    color: #143370;
                    border: 1px solid #c7d8ff;
                    border-radius: 12px;
                    padding: 4px 10px;
                    font-size: 12px;
                }
                QPushButton:hover { background: #dce9ff; }
            """)
            chip.clicked.connect(clear_callback)
            self.active_filters_layout.addWidget(chip)

        self.active_filters_layout.addStretch(1)

    def get_active_filter_chips(self) -> list[tuple[str, Callable[[], None]]]:
        chips: list[tuple[str, Callable[[], None]]] = []

        text = self.torgi_search_input.text().strip()
        if text:
            chips.append((f"Поиск: {text}", lambda: self.clear_filter_widget(self.torgi_search_input)))

        region_label = self.torgi_subject_combo.currentText()
        region_value = self.torgi_subject_combo.currentData()
        if region_value:
            chips.append((region_label, lambda: self.clear_filter_combo(self.torgi_subject_combo)))

        category_label = self.torgi_category_combo.currentText()
        category_value = self.torgi_category_combo.currentData()
        if category_value:
            chips.append((category_label, lambda: self.clear_filter_combo(self.torgi_category_combo)))

        notice_label = self.torgi_notice_status_combo.currentText()
        notice_value = self.torgi_notice_status_combo.currentData()
        if notice_value:
            chips.append((notice_label, lambda: self.clear_filter_combo(self.torgi_notice_status_combo)))

        lot_label = self.torgi_lot_status_combo.currentText()
        lot_value = self.torgi_lot_status_combo.currentData()
        if lot_value:
            chips.append((lot_label, lambda: self.clear_filter_combo(self.torgi_lot_status_combo)))

        price_min = self.torgi_price_min_input.text().strip()
        if price_min:
            chips.append((f"Цена от {price_min}", lambda: self.clear_filter_widget(self.torgi_price_min_input)))

        price_max = self.torgi_price_max_input.text().strip()
        if price_max:
            chips.append((f"Цена до {price_max}", lambda: self.clear_filter_widget(self.torgi_price_max_input)))

        return chips

    def clear_filter_widget(self, widget):
        widget.clear()
        self.update_active_filter_chips()
        self.save_torgi_filter_state()

    def clear_filter_combo(self, combo):
        combo.setCurrentIndex(0)
        self.update_active_filter_chips()
        self.save_torgi_filter_state()

    def clear_torgi_filters(self):
        line_widgets = [
            self.torgi_search_input, self.torgi_price_min_input, self.torgi_price_max_input,
            self.torgi_fias_input, self.torgi_price_fin_from_input, self.torgi_price_fin_to_input,
            self.torgi_notice_number_input, self.torgi_etp_input, self.torgi_publish_from_input,
            self.torgi_publish_to_input, self.torgi_bidd_end_from_input, self.torgi_bidd_end_to_input,
            self.torgi_auction_from_input, self.torgi_auction_to_input, self.torgi_npa_input,
            self.torgi_organizer_name_input, self.torgi_organizer_inn_input,
            self.torgi_organizer_kpp_input, self.torgi_organizer_ogrn_input,
            self.torgi_right_holder_name_input, self.torgi_right_holder_inn_input,
            self.torgi_right_holder_kpp_input, self.torgi_right_holder_ogrn_input,
            self.torgi_amo_org_input, self.torgi_attachment_input,
        ]
        for widget in line_widgets:
            widget.clear()
        for widget in [
            self.torgi_type_transaction_combo, self.torgi_subject_combo, self.torgi_ownership_combo,
            self.torgi_category_combo, self.torgi_currency_combo,
            self.torgi_bidd_type_combo, self.torgi_bidd_form_combo, self.torgi_notice_status_combo,
        ]:
            self._set_combo_data(widget, None)
        self._set_combo_data(self.torgi_status_combo, TorgiGovClient.DEFAULT_LOT_STATUS)
        for widget in [
            self.torgi_is_msp_checkbox, self.torgi_is_stopped_checkbox, self.torgi_rh_gov_prt_checkbox,
            self.torgi_has_appeals_checkbox, self.torgi_has_solutions_checkbox,
            self.torgi_has_prescriptions_checkbox, self.torgi_match_phrase_checkbox,
        ]:
            widget.setChecked(False)
        self.torgi_load_all_checkbox.setChecked(True)
        self.torgi_max_items_input.setText("5000")
        self.torgi_results = []
        self.torgi_current_page = 1
        self.render_torgi_results()
        self.update_active_filter_chips()
        self.save_torgi_filter_state()

    def run_torgi_search(self, page: int = 1):
        try:
            self.torgi_current_page = max(1, page)
            filters = self.collect_torgi_filters(self.torgi_current_page)
            load_all = self.torgi_load_all_checkbox.isChecked()
            max_items = self._line_int_or_none(self.torgi_max_items_input)
            if load_all:
                filters.page = 1
                filters.page_size = 100
                self.torgi_current_page = 1
            self.save_torgi_filter_state()
        except ValueError as exc:
            QMessageBox.warning(self, "Проверьте фильтры", str(exc))
            return

        self.torgi_search_btn.setEnabled(False)
        self.torgi_search_btn.setText("Идёт поиск...")
        self.torgi_excel_search_btn.setEnabled(False)
        self.torgi_stop_btn.setEnabled(True)
        self.torgi_results = []
        self.torgi_meta = {"mode": "all_pages" if load_all else "page", "loaded": 0}
        self.render_torgi_results()
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.status_bar.showMessage("Онлайн-поиск torgi.gov.ru...")

        self.torgi_worker = TorgiGovSearchWorker(
            filters,
            load_all=load_all,
            max_items=max_items,
        )
        self.torgi_worker.progress.connect(self.status_bar.showMessage)
        self.torgi_worker.progress_percent.connect(self.progress_bar.setValue)
        self.torgi_worker.page_loaded.connect(self.on_torgi_search_page_loaded)
        self.torgi_worker.finished.connect(self.on_torgi_search_finished)
        self.torgi_worker.error.connect(self.on_torgi_search_error)
        self.torgi_worker.start()

    def run_torgi_excel_search(self):
        try:
            self.torgi_current_page = 1
            filters = self.collect_torgi_filters(1)
            filters.page = 1
            filters.page_size = 100
            self.save_torgi_filter_state()
        except ValueError as exc:
            QMessageBox.warning(self, "Проверьте фильтры", str(exc))
            return

        self.torgi_search_btn.setEnabled(False)
        self.torgi_excel_search_btn.setEnabled(False)
        self.torgi_excel_search_btn.setText("Загрузка Excel...")
        self.torgi_stop_btn.setEnabled(False)
        self.torgi_results = []
        self.torgi_meta = {"mode": "excel", "loaded": 0}
        self.render_torgi_results()
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.status_bar.showMessage("Поиск через Excel-выгрузку torgi.gov.ru...")

        self.torgi_worker = TorgiGovSearchWorker(
            filters,
            load_all=False,
            max_items=None,
            use_excel=True,
        )
        self.torgi_worker.progress.connect(self.status_bar.showMessage)
        self.torgi_worker.progress_percent.connect(self.progress_bar.setValue)
        self.torgi_worker.page_loaded.connect(self.on_torgi_search_page_loaded)
        self.torgi_worker.finished.connect(self.on_torgi_search_finished)
        self.torgi_worker.error.connect(self.on_torgi_search_error)
        self.torgi_worker.start()

    def stop_torgi_search(self):
        worker = getattr(self, "torgi_worker", None)
        if worker and worker.isRunning():
            worker.request_stop()
            self.torgi_stop_btn.setEnabled(False)
            self.status_bar.showMessage("Останавливаю поиск после текущего запроса...", 5000)

    def on_torgi_search_page_loaded(self, lots: list, page_meta: dict):
        seen = {lot.external_id for lot in self.torgi_results}
        for lot in lots or []:
            if lot.external_id not in seen:
                self.torgi_results.append(lot)
                seen.add(lot.external_id)
        meta = dict(self.torgi_meta or {})
        meta.update(page_meta or {})
        meta["loaded"] = len(self.torgi_results)
        meta.setdefault("mode", "all_pages" if self.torgi_load_all_checkbox.isChecked() else "page")
        self.torgi_meta = meta
        self.render_torgi_results()

    def on_torgi_search_finished(self, lots: list, meta: dict):
        self.torgi_search_btn.setEnabled(True)
        self.torgi_search_btn.setText("🔎 Найти онлайн")
        self.torgi_excel_search_btn.setEnabled(True)
        self.torgi_excel_search_btn.setText("Поиск через эксель")
        self.torgi_stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        meta = dict(meta or {})
        warnings = list(meta.get("warnings") or [])
        warnings.extend(getattr(self, "torgi_unsupported_warnings", []))
        meta["warnings"] = warnings
        self.torgi_results = list(lots or [])
        self.torgi_meta = meta
        self.render_torgi_results()
        if warnings:
            self.status_bar.showMessage(warnings[0], 10000)
        elif meta.get("stop_reason") == "user_stopped":
            self.status_bar.showMessage(f"Поиск остановлен: сохранено {len(self.torgi_results)} лотов", 5000)
        elif meta.get("mode") == "excel":
            self.status_bar.showMessage(f"Excel-поиск завершен: {len(self.torgi_results)} лотов", 5000)
        else:
            self.status_bar.showMessage(f"Онлайн-поиск завершен: {len(self.torgi_results)} лотов", 5000)

    def on_torgi_search_error(self, error_msg: str):
        self.torgi_search_btn.setEnabled(True)
        self.torgi_search_btn.setText("🔎 Найти онлайн")
        self.torgi_excel_search_btn.setEnabled(True)
        self.torgi_excel_search_btn.setText("Поиск через эксель")
        self.torgi_stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.status_bar.showMessage("Ошибка онлайн-поиска torgi.gov.ru", 10000)
        QMessageBox.warning(self, "Ошибка torgi.gov.ru", error_msg)

    def render_torgi_results(self):
        meta = self.torgi_meta or {}
        loaded = meta.get("loaded", len(self.torgi_results))
        total = meta.get("total")
        page = meta.get("page", self.torgi_current_page)
        source = meta.get("source", "torgi.gov.ru")
        mode = meta.get("mode")
        diagnostics = []
        if meta.get("pages_loaded") is not None:
            diagnostics.append(f"страниц: {meta.get('pages_loaded')}")
        if meta.get("duplicates"):
            diagnostics.append(f"дублей: {meta.get('duplicates')}")
        if meta.get("skipped_without_id"):
            diagnostics.append(f"без ID: {meta.get('skipped_without_id')}")
        if meta.get("stop_reason"):
            diagnostics.append(f"стоп: {meta.get('stop_reason')}")
        if total is not None:
            total_text = f"{loaded} из {total}"
        else:
            total_text = str(loaded)
        if mode == "all_pages":
            mode_text = "все страницы"
        elif mode == "excel":
            mode_text = "Excel-выгрузка"
        else:
            mode_text = f"страница {page}"
        warnings = meta.get("warnings") or []
        suffix_parts = []
        if diagnostics:
            suffix_parts.append("; ".join(diagnostics))
        if warnings:
            suffix_parts.append(f"предупреждений: {len(warnings)}")
        suffix = f" · {' · '.join(suffix_parts)}" if suffix_parts else ""
        self.torgi_status_label.setText(
            f"Найдено {total_text}, режим: {mode_text}, источник {source}{suffix}"
        )
        self.torgi_prev_btn.setEnabled(self.torgi_current_page > 1 and mode != "all_pages")
        self.torgi_next_btn.setEnabled(bool(meta.get("has_more")) and mode != "all_pages")

        existing: set[str] = set()
        external_ids = [lot.external_id for lot in self.torgi_results]
        if external_ids:
            with session_scope() as session:
                existing = set(session.scalars(
                    select(ProcessedLot.external_id).where(ProcessedLot.external_id.in_(external_ids))
                ).all())

        self.torgi_results_table.setSortingEnabled(False)
        self.torgi_results_table.setRowCount(len(self.torgi_results))
        for row, lot in enumerate(self.torgi_results):
            in_db = lot.external_id in existing
            raw = lot.raw_data or {}
            bidd_end = raw.get("bidd_end_time") or raw.get("biddEndTime") or ""
            published = raw.get("published_at") or raw.get("publicationDate") or raw.get("firstVersionPublicationDate") or lot.published_at
            link_url = lot.source_url or lot.lot_url
            items = [
                make_text_item("Да" if in_db else ""),
                make_text_item(lot.external_id.replace("torgi_gov:", "")),
                make_text_item(lot.title),
                make_text_item(translate_category(lot.category)),
                make_text_item(lot.region_name or lot.address or lot.region_slug or ""),
                make_number_item(lot.start_price or lot.current_price),
                make_text_item(translate_status(lot.auction_status)),
                make_date_item(published),
                make_date_item(bidd_end),
                make_text_item("Открыть" if link_url else ""),
            ]
            row_color = QColor("#eaf7ef") if in_db else QColor("white")
            for col, item in enumerate(items):
                item.setData(Qt.UserRole, lot.external_id)
                item.setData(EXTERNAL_ID_ROLE, lot.external_id)
                item.setData(URL_ROLE, link_url)
                item.setBackground(QBrush(row_color))
                self.torgi_results_table.setItem(row, col, item)
        self.torgi_results_table.setSortingEnabled(True)

    def _format_money(self, value: float | None) -> str:
        if value is None:
            return ""
        try:
            return f"{int(float(value)):,} ₽".replace(",", " ")
        except (TypeError, ValueError):
            return str(value)

    def _torgi_lot_by_external_id(self, external_id: str | None) -> NormalizedLot | None:
        if not external_id:
            return None
        for lot in self.torgi_results:
            if lot.external_id == external_id:
                return lot
        return None

    def selected_torgi_lots(self) -> list[NormalizedLot]:
        rows = sorted({item.row() for item in self.torgi_results_table.selectedItems()})
        lots = []
        for row in rows:
            item = self.torgi_results_table.item(row, 1)
            external_id = item.data(EXTERNAL_ID_ROLE) if item else None
            lot = self._torgi_lot_by_external_id(external_id or (item.data(Qt.UserRole) if item else None))
            if lot:
                lots.append(lot)
        return lots

    def import_selected_torgi_lots(self):
        self.import_torgi_lots(self.selected_torgi_lots())

    def import_all_torgi_lots(self):
        total = (self.torgi_meta or {}).get("total")
        loaded = len(self.torgi_results)
        mode = (self.torgi_meta or {}).get("mode")
        if total and loaded < int(total) and mode != "all_pages":
            reply = QMessageBox.question(
                self,
                "Загружена не вся выдача",
                f"Сейчас загружено только {loaded} из {total} лотов.\n\n"
                "Чтобы импортировать все найденные лоты, включите галочку "
                "«Загрузить все страницы результата» и выполните поиск заново.\n\n"
                "Импортировать только загруженные сейчас?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.import_torgi_lots(list(self.torgi_results))

    def import_torgi_lots(self, lots: list[NormalizedLot]):
        if not lots:
            QMessageBox.information(self, "Импорт", "Нет выбранных онлайн-лотов для импорта.")
            return
        external_ids = [lot.external_id for lot in lots]
        with session_scope() as session:
            existed = set(session.scalars(
                select(ProcessedLot.external_id).where(ProcessedLot.external_id.in_(external_ids))
            ).all())
            for lot in lots:
                persist_lot(session, lot)
        added = len([lot for lot in lots if lot.external_id not in existed])
        updated = len(lots) - added
        self.status_bar.showMessage(f"Импорт torgi.gov.ru: +{added}, обновлено {updated}", 5000)
        QMessageBox.information(self, "Импорт завершен", f"Добавлено: {added}\nОбновлено: {updated}")
        self.load_lots()
        self.update_dashboard()
        self.render_torgi_results()

    def search_torgi_next_page(self):
        self.run_torgi_search(self.torgi_current_page + 1)

    def search_torgi_prev_page(self):
        self.run_torgi_search(max(1, self.torgi_current_page - 1))

    def on_torgi_header_clicked(self, column: int):
        numeric_desc_first_cols = {5}

        if self._last_sort_column == column:
            order = Qt.DescendingOrder if self._last_sort_order == Qt.AscendingOrder else Qt.AscendingOrder
        else:
            order = Qt.DescendingOrder if column in numeric_desc_first_cols else Qt.AscendingOrder

        self._last_sort_column = column
        self._last_sort_order = order
        self.torgi_results_table.sortItems(column, order)

    def open_torgi_result_url(self, row: int, column: int) -> None:
        item = self.torgi_results_table.item(row, column) or self.torgi_results_table.item(row, 1)
        external_id = item.data(EXTERNAL_ID_ROLE) if item else None
        lot = self._torgi_lot_by_external_id(external_id or (item.data(Qt.UserRole) if item else None))
        url = (item.data(URL_ROLE) if item else None) or (lot.source_url if lot else None) or (lot.lot_url if lot else None)
        if url:
            QDesktopServices.openUrl(QUrl.fromUserInput(str(url)))
        else:
            QMessageBox.information(self, "Инфо", "Ссылка на лот отсутствует.")

    def open_torgi_link_cell(self, row: int, column: int) -> None:
        if column == 9:
            self.open_torgi_result_url(row, column)

    def show_torgi_request_diagnostics(self):
        meta = self.torgi_meta or {}
        if not meta:
            try:
                filters = self.collect_torgi_filters(self.torgi_current_page)
                params, warnings = TorgiGovClient()._build_query_params(filters)
                endpoint = TorgiGovClient()._prepare_url(TorgiGovClient.SEARCH_ENDPOINT, params)
                meta = {"raw_endpoint": endpoint, "raw_params": params, "warnings": warnings}
            except Exception as exc:
                QMessageBox.warning(self, "Диагностика запроса", str(exc))
                return

        lines = [
            f"Endpoint: {meta.get('raw_endpoint') or TorgiGovClient.SEARCH_ENDPOINT}",
            f"Params: {json.dumps(meta.get('raw_params') or {}, ensure_ascii=False, indent=2)}",
            f"total: {meta.get('total')}",
            f"total_pages: {meta.get('total_pages')}",
            f"loaded: {meta.get('loaded', len(self.torgi_results))}",
            f"raw_items_loaded: {meta.get('raw_items_loaded')}",
            f"pages_loaded: {meta.get('pages_loaded')}",
            f"duplicates: {meta.get('duplicates')}",
            f"skipped_without_id: {meta.get('skipped_without_id')}",
            f"stop_reason: {meta.get('stop_reason')}",
        ]
        page_diagnostics = meta.get("page_diagnostics") or []
        if page_diagnostics:
            lines.append("Pages:")
            for item in page_diagnostics[:20]:
                lines.append(
                    f"page {item.get('page')}: items={item.get('items_on_page')}, "
                    f"new={item.get('new_unique')}, duplicates={item.get('duplicates')}, "
                    f"without_id={item.get('skipped_without_id')}"
                )
            if len(page_diagnostics) > 20:
                lines.append(f"... ещё страниц: {len(page_diagnostics) - 20}")
        warnings = meta.get("warnings") or []
        if warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in warnings)

        QMessageBox.information(self, "Параметры запроса torgi.gov.ru", "\n".join(lines))

    def open_torgi_site(self):
        try:
            filters = self.collect_torgi_filters(self.torgi_current_page)
            params, _warnings = TorgiGovClient()._build_query_params(filters)
            for key in ("withFacets", "size"):
                params.pop(key, None)
            url = f"{TorgiGovClient.FALLBACK_LIST_URL}?{urlencode(params)}"
        except Exception:
            url = TorgiGovClient.FALLBACK_LIST_URL
        import webbrowser
        webbrowser.open(url)

    def collect_tbankrot_filters(self, page: int | None = None) -> TBankrotSearchFilters:
        return TBankrotSearchFilters(
            search_text=self.tbankrot_search_input.text().strip(),
            region=self._combo_value(self.tbankrot_region_combo),
            price_min=self._line_float(self.tbankrot_price_min_input),
            price_max=self._line_float(self.tbankrot_price_max_input),
            lot_number=self._line_text(self.tbankrot_lot_number_input),
            trade_type=self._combo_value(self.tbankrot_trade_type_combo),
            photo_only=self.tbankrot_photo_only_checkbox.isChecked(),
            debtor=self._line_text(self.tbankrot_debtor_input),
            auction_manager=self._line_text(self.tbankrot_auction_manager_input),
            organizer=self._line_text(self.tbankrot_organizer_input),
            stop_words=self._line_text(self.tbankrot_stop_words_input),
            show_closed=self.tbankrot_show_closed_checkbox.isChecked(),
            show_paused=self.tbankrot_show_paused_checkbox.isChecked(),
            page=page or self.tbankrot_current_page,
            page_size=100,
        )

    def save_tbankrot_filter_state(self) -> None:
        from bankrotai.core import set_app_setting
        state = {
            "search_text": self.tbankrot_search_input.text().strip(),
            "region": self._combo_value(self.tbankrot_region_combo),
            "price_min": self.tbankrot_price_min_input.text().strip(),
            "price_max": self.tbankrot_price_max_input.text().strip(),
            "trade_type": self._combo_value(self.tbankrot_trade_type_combo),
        }
        set_app_setting("tbankrot_last_filters", json.dumps(state, ensure_ascii=False))

    def restore_tbankrot_filter_state(self) -> None:
        from bankrotai.core import get_app_setting
        raw = get_app_setting("tbankrot_last_filters", "")
        if not raw:
            return
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            return
        self.tbankrot_search_input.setText(state.get("search_text", ""))
        self._set_combo_data(self.tbankrot_region_combo, state.get("region"))
        self.tbankrot_price_min_input.setText(state.get("price_min", ""))
        self.tbankrot_price_max_input.setText(state.get("price_max", ""))
        self._set_combo_data(self.tbankrot_trade_type_combo, state.get("trade_type"))

    def update_tbankrot_filter_chips(self):
        if not hasattr(self, "tbankrot_active_filters_layout"):
            return
        while self.tbankrot_active_filters_layout.count():
            item = self.tbankrot_active_filters_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        chips = self.get_tbankrot_filter_chips()
        if not chips:
            label = QLabel("Фильтры не выбраны")
            label.setStyleSheet("color: #7a8699; font-size: 12px;")
            self.tbankrot_active_filters_layout.addWidget(label)
            return

        for label_text, clear_callback in chips:
            chip = QPushButton(f"{label_text}  ×")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet("""
                QPushButton {
                    background: #eaf1ff;
                    color: #143370;
                    border: 1px solid #c7d8ff;
                    border-radius: 12px;
                    padding: 4px 10px;
                    font-size: 12px;
                }
                QPushButton:hover { background: #dce9ff; }
            """)
            chip.clicked.connect(clear_callback)
            self.tbankrot_active_filters_layout.addWidget(chip)
        self.tbankrot_active_filters_layout.addStretch(1)

    def get_tbankrot_filter_chips(self) -> list[tuple[str, Callable[[], None]]]:
        chips: list[tuple[str, Callable[[], None]]] = []

        text = self.tbankrot_search_input.text().strip()
        if text:
            chips.append((f"Поиск: {text}", lambda: self.clear_tbankrot_filter_widget(self.tbankrot_search_input)))

        region_value = self.tbankrot_region_combo.currentData()
        if region_value:
            chips.append((self.tbankrot_region_combo.currentText(), lambda: self.clear_tbankrot_filter_combo(self.tbankrot_region_combo)))

        trade_value = self.tbankrot_trade_type_combo.currentData()
        if trade_value:
            chips.append((self.tbankrot_trade_type_combo.currentText(), lambda: self.clear_tbankrot_filter_combo(self.tbankrot_trade_type_combo)))

        price_min = self.tbankrot_price_min_input.text().strip()
        if price_min:
            chips.append((f"Цена от {price_min}", lambda: self.clear_tbankrot_filter_widget(self.tbankrot_price_min_input)))

        price_max = self.tbankrot_price_max_input.text().strip()
        if price_max:
            chips.append((f"Цена до {price_max}", lambda: self.clear_tbankrot_filter_widget(self.tbankrot_price_max_input)))

        lot_number = self.tbankrot_lot_number_input.text().strip()
        if lot_number:
            chips.append((f"Лот: {lot_number}", lambda: self.clear_tbankrot_filter_widget(self.tbankrot_lot_number_input)))

        return chips

    def clear_tbankrot_filter_widget(self, widget):
        widget.clear()
        self.update_tbankrot_filter_chips()
        self.save_tbankrot_filter_state()

    def clear_tbankrot_filter_combo(self, combo):
        combo.setCurrentIndex(0)
        self.update_tbankrot_filter_chips()
        self.save_tbankrot_filter_state()

    def clear_tbankrot_filters(self):
        for widget in [
            self.tbankrot_search_input, self.tbankrot_price_min_input, self.tbankrot_price_max_input,
            self.tbankrot_lot_number_input, self.tbankrot_debtor_input,
            self.tbankrot_auction_manager_input, self.tbankrot_organizer_input,
            self.tbankrot_stop_words_input,
        ]:
            widget.clear()
        self._set_combo_data(self.tbankrot_region_combo, None)
        self._set_combo_data(self.tbankrot_trade_type_combo, None)
        for widget in [
            self.tbankrot_photo_only_checkbox, self.tbankrot_show_closed_checkbox,
            self.tbankrot_show_paused_checkbox,
        ]:
            widget.setChecked(False)
        self.tbankrot_load_all_checkbox.setChecked(True)
        self.tbankrot_max_items_input.setText("5000")
        self.tbankrot_results = []
        self.tbankrot_current_page = 1
        self.render_tbankrot_results()
        self.update_tbankrot_filter_chips()
        self.save_tbankrot_filter_state()

    def run_tbankrot_search(self, page: int = 1):
        try:
            self.tbankrot_current_page = max(1, page)
            filters = self.collect_tbankrot_filters(self.tbankrot_current_page)
            load_all = self.tbankrot_load_all_checkbox.isChecked()
            max_items = self._line_int_or_none(self.tbankrot_max_items_input)
            if load_all:
                filters.page = 1
                self.tbankrot_current_page = 1
            self.save_tbankrot_filter_state()
        except ValueError as exc:
            QMessageBox.warning(self, "Проверьте фильтры", str(exc))
            return

        self.tbankrot_search_btn.setEnabled(False)
        self.tbankrot_search_btn.setText("Идет поиск...")
        self.tbankrot_stop_btn.setEnabled(True)
        self.tbankrot_results = []
        self.tbankrot_meta = {"mode": "all_pages" if load_all else "page", "loaded": 0}
        self.render_tbankrot_results()
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.status_bar.showMessage("Поиск TBankrot...")

        self.tbankrot_worker = TBankrotSearchWorker(
            filters,
            load_all=load_all,
            max_items=max_items,
        )
        self.tbankrot_worker.progress.connect(self.status_bar.showMessage)
        self.tbankrot_worker.progress_percent.connect(self.progress_bar.setValue)
        self.tbankrot_worker.page_loaded.connect(self.on_tbankrot_search_page_loaded)
        self.tbankrot_worker.finished.connect(self.on_tbankrot_search_finished)
        self.tbankrot_worker.error.connect(self.on_tbankrot_search_error)
        self.tbankrot_worker.start()

    def stop_tbankrot_search(self):
        worker = getattr(self, "tbankrot_worker", None)
        if worker and worker.isRunning():
            worker.request_stop()
            self.tbankrot_stop_btn.setEnabled(False)
            self.status_bar.showMessage("Останавливаю поиск TBankrot после текущего запроса...", 5000)

    def on_tbankrot_search_page_loaded(self, lots: list, page_meta: dict):
        seen = {lot.external_id for lot in self.tbankrot_results}
        for lot in lots or []:
            if lot.external_id not in seen:
                self.tbankrot_results.append(lot)
                seen.add(lot.external_id)
        meta = dict(self.tbankrot_meta or {})
        meta.update(page_meta or {})
        meta["loaded"] = len(self.tbankrot_results)
        meta.setdefault("mode", "all_pages" if self.tbankrot_load_all_checkbox.isChecked() else "page")
        self.tbankrot_meta = meta
        self.render_tbankrot_results()

    def on_tbankrot_search_finished(self, lots: list, meta: dict):
        self.tbankrot_search_btn.setEnabled(True)
        self.tbankrot_search_btn.setText("Найти на TBankrot")
        self.tbankrot_stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.tbankrot_results = list(lots or [])
        self.tbankrot_meta = dict(meta or {})
        self.render_tbankrot_results()
        if self.tbankrot_meta.get("stop_reason") == "user_stopped":
            self.status_bar.showMessage(f"Поиск TBankrot остановлен: сохранено {len(self.tbankrot_results)} лотов", 5000)
        else:
            self.status_bar.showMessage(f"Поиск TBankrot завершен: {len(self.tbankrot_results)} лотов", 5000)

    def on_tbankrot_search_error(self, error_msg: str):
        self.tbankrot_search_btn.setEnabled(True)
        self.tbankrot_search_btn.setText("Найти на TBankrot")
        self.tbankrot_stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.status_bar.showMessage("Ошибка поиска TBankrot", 10000)
        QMessageBox.warning(self, "Ошибка TBankrot", error_msg)

    def render_tbankrot_results(self):
        meta = self.tbankrot_meta or {}
        loaded = meta.get("loaded", len(self.tbankrot_results))
        page = meta.get("page", self.tbankrot_current_page)
        source = meta.get("source", "tbankrot.ru")
        mode = meta.get("mode")
        diagnostics = []
        if meta.get("pages_loaded") is not None:
            diagnostics.append(f"страниц: {meta.get('pages_loaded')}")
        if meta.get("duplicates"):
            diagnostics.append(f"дублей: {meta.get('duplicates')}")
        if meta.get("stop_reason"):
            diagnostics.append(f"стоп: {meta.get('stop_reason')}")
        mode_text = "все страницы" if mode == "all_pages" else f"страница {page}"
        suffix = f" · {'; '.join(diagnostics)}" if diagnostics else ""
        self.tbankrot_status_label.setText(
            f"Найдено {loaded}, режим: {mode_text}, источник {source}{suffix}"
        )
        self.tbankrot_prev_btn.setEnabled(self.tbankrot_current_page > 1 and mode != "all_pages")
        self.tbankrot_next_btn.setEnabled(bool(meta.get("has_more")) and mode != "all_pages")

        existing: set[str] = set()
        external_ids = [lot.external_id for lot in self.tbankrot_results]
        if external_ids:
            with session_scope() as session:
                existing = set(session.scalars(
                    select(ProcessedLot.external_id).where(ProcessedLot.external_id.in_(external_ids))
                ).all())

        self.tbankrot_results_table.setSortingEnabled(False)
        self.tbankrot_results_table.setRowCount(len(self.tbankrot_results))
        for row, lot in enumerate(self.tbankrot_results):
            in_db = lot.external_id in existing
            raw = lot.raw_data or {}
            dates = raw.get("dates") or []
            bidd_end = ""
            if dates:
                bidd_end = " / ".join(str(item.get("text") or "") for item in dates[:2] if item.get("text"))
            link_url = lot.source_url or lot.lot_url
            items = [
                make_text_item("Да" if in_db else ""),
                make_text_item(lot.external_id.replace("tbankrot:", "")),
                make_text_item(lot.title),
                make_text_item(translate_category(lot.category)),
                make_text_item(lot.region_name or lot.address or lot.region_slug or ""),
                make_number_item(lot.current_price or lot.start_price),
                make_text_item(translate_status(lot.auction_status)),
                make_date_item(lot.published_at),
                make_text_item(bidd_end),
                make_text_item("Открыть" if link_url else ""),
            ]
            row_color = QColor("#eaf7ef") if in_db else QColor("white")
            for col, item in enumerate(items):
                item.setData(Qt.UserRole, lot.external_id)
                item.setData(EXTERNAL_ID_ROLE, lot.external_id)
                item.setData(URL_ROLE, link_url)
                item.setBackground(QBrush(row_color))
                self.tbankrot_results_table.setItem(row, col, item)
        self.tbankrot_results_table.setSortingEnabled(True)

    def _tbankrot_lot_by_external_id(self, external_id: str | None) -> NormalizedLot | None:
        if not external_id:
            return None
        for lot in self.tbankrot_results:
            if lot.external_id == external_id:
                return lot
        return None

    def selected_tbankrot_lots(self) -> list[NormalizedLot]:
        rows = sorted({item.row() for item in self.tbankrot_results_table.selectedItems()})
        lots = []
        for row in rows:
            item = self.tbankrot_results_table.item(row, 1)
            external_id = item.data(EXTERNAL_ID_ROLE) if item else None
            lot = self._tbankrot_lot_by_external_id(external_id or (item.data(Qt.UserRole) if item else None))
            if lot:
                lots.append(lot)
        return lots

    def import_selected_tbankrot_lots(self):
        self.import_tbankrot_lots(self.selected_tbankrot_lots())

    def import_all_tbankrot_lots(self):
        self.import_tbankrot_lots(list(self.tbankrot_results))

    def import_tbankrot_lots(self, lots: list[NormalizedLot]):
        if not lots:
            QMessageBox.information(self, "Импорт", "Нет выбранных лотов TBankrot для импорта.")
            return
        external_ids = [lot.external_id for lot in lots]
        with session_scope() as session:
            existed = set(session.scalars(
                select(ProcessedLot.external_id).where(ProcessedLot.external_id.in_(external_ids))
            ).all())
            for lot in lots:
                persist_lot(session, lot)
        added = len([lot for lot in lots if lot.external_id not in existed])
        updated = len(lots) - added
        self.status_bar.showMessage(f"Импорт TBankrot: +{added}, обновлено {updated}", 5000)
        QMessageBox.information(self, "Импорт завершен", f"Добавлено: {added}\nОбновлено: {updated}")
        self.load_lots()
        self.update_dashboard()
        self.render_tbankrot_results()

    def search_tbankrot_next_page(self):
        self.run_tbankrot_search(self.tbankrot_current_page + 1)

    def search_tbankrot_prev_page(self):
        self.run_tbankrot_search(max(1, self.tbankrot_current_page - 1))

    def on_tbankrot_header_clicked(self, column: int):
        numeric_desc_first_cols = {5}
        if self._last_tbankrot_sort_column == column:
            order = Qt.DescendingOrder if self._last_tbankrot_sort_order == Qt.AscendingOrder else Qt.AscendingOrder
        else:
            order = Qt.DescendingOrder if column in numeric_desc_first_cols else Qt.AscendingOrder
        self._last_tbankrot_sort_column = column
        self._last_tbankrot_sort_order = order
        self.tbankrot_results_table.sortItems(column, order)

    def open_tbankrot_result_url(self, row: int, column: int) -> None:
        item = self.tbankrot_results_table.item(row, column) or self.tbankrot_results_table.item(row, 1)
        external_id = item.data(EXTERNAL_ID_ROLE) if item else None
        lot = self._tbankrot_lot_by_external_id(external_id or (item.data(Qt.UserRole) if item else None))
        url = (item.data(URL_ROLE) if item else None) or (lot.source_url if lot else None) or (lot.lot_url if lot else None)
        if url:
            QDesktopServices.openUrl(QUrl.fromUserInput(str(url)))
        else:
            QMessageBox.information(self, "Инфо", "Ссылка на лот отсутствует.")

    def open_tbankrot_link_cell(self, row: int, column: int) -> None:
        if column == 9:
            self.open_tbankrot_result_url(row, column)

    def show_tbankrot_request_diagnostics(self):
        meta = self.tbankrot_meta or {}
        if not meta:
            try:
                filters = self.collect_tbankrot_filters(self.tbankrot_current_page)
                params = TBankrotClient()._build_query_params(filters)
                endpoint = TBankrotClient()._prepare_url(params)
                meta = {"raw_endpoint": endpoint, "raw_params": params}
            except Exception as exc:
                QMessageBox.warning(self, "Диагностика запроса", str(exc))
                return

        lines = [
            f"Endpoint: {meta.get('raw_endpoint') or TBankrotClient.SEARCH_ENDPOINT}",
            f"Params: {json.dumps(meta.get('raw_params') or [], ensure_ascii=False, indent=2)}",
            f"loaded: {meta.get('loaded', len(self.tbankrot_results))}",
            f"pages_loaded: {meta.get('pages_loaded')}",
            f"duplicates: {meta.get('duplicates')}",
            f"stop_reason: {meta.get('stop_reason')}",
        ]
        page_diagnostics = meta.get("page_diagnostics") or []
        if page_diagnostics:
            lines.append("Pages:")
            for item in page_diagnostics[:20]:
                lines.append(
                    f"page {item.get('page')}: items={item.get('items_on_page')}, "
                    f"new={item.get('new_unique')}, duplicates={item.get('duplicates')}"
                )
            if len(page_diagnostics) > 20:
                lines.append(f"... еще страниц: {len(page_diagnostics) - 20}")

        QMessageBox.information(self, "Параметры запроса TBankrot", "\n".join(lines))

    def open_tbankrot_site(self):
        try:
            filters = self.collect_tbankrot_filters(self.tbankrot_current_page)
            params = TBankrotClient()._build_query_params(filters)
            url = TBankrotClient()._prepare_url(params)
        except Exception:
            url = TBankrotClient.BASE_URL
        import webbrowser
        webbrowser.open(url)

    def init_registry_tab(self):
        layout = QVBoxLayout(self.registry_tab)
        
        # Toolbar for Registry
        toolbar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Поиск по названию...")
        self.search_input.setMaximumHeight(35)
        self.search_input.textChanged.connect(lambda: self.load_lots())
        
        self.category_combo = QComboBox()
        self.category_combo.addItems(["Все категории", "Жилая недвижимость", "Коммерческая недвижимость", "Земельные участки", "Транспорт", "Прочее"])
        self.category_combo.setFixedWidth(200)
        self.category_combo.currentIndexChanged.connect(lambda: self.load_lots())
        
        refresh_reg_btn = QPushButton("🔄")
        refresh_reg_btn.setFixedWidth(40)
        refresh_reg_btn.clicked.connect(lambda: self.load_lots())
        
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.category_combo)
        toolbar.addWidget(refresh_reg_btn)
        layout.addLayout(toolbar)

        # Sorting Toolbar
        sort_toolbar = QHBoxLayout()
        sort_toolbar.addWidget(QLabel("Сортировка:"))
        
        self.sort_new_btn = QPushButton("Сначала новые")
        self.sort_new_btn.setCheckable(True)
        self.sort_new_btn.setChecked(True)
        self.sort_new_btn.clicked.connect(lambda: self.set_sort("last_update", "desc"))
        
        self.sort_cheap_btn = QPushButton("Дешевле")
        self.sort_cheap_btn.setCheckable(True)
        self.sort_cheap_btn.clicked.connect(lambda: self.set_sort("current_price", "asc"))
        
        self.sort_expensive_btn = QPushButton("Дороже")
        self.sort_expensive_btn.setCheckable(True)
        self.sort_expensive_btn.clicked.connect(lambda: self.set_sort("current_price", "desc"))
        
        self.sort_group = [self.sort_new_btn, self.sort_cheap_btn, self.sort_expensive_btn]
        for btn in self.sort_group: sort_toolbar.addWidget(btn)
        sort_toolbar.addStretch()
        # Sorting is handled by clicking table columns; the old button toolbar is kept out of the UI.

        self.current_sort = ("last_update", "desc")

        self.lots_splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(self.lots_splitter)

        # Table
        self.lots_table = QTableWidget()
        self.lots_table.setColumnCount(12)
        self.lots_table.setHorizontalHeaderLabels([
            "Код", 
            "Название лота", 
            "Регион", 
            "Цена", 
            "Рыночная цена", 
            "Дисконт", 
            "Площадь здания/помещения", 
            "Площадь участка", 
            "Статус", 
            "Категория", 
            "Риск", 
            "Обновление"
        ])
        self.lots_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.lots_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.lots_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.lots_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.lots_table.setSortingEnabled(True) # --- Enable sorting ---
        # Enable double-click to open lot URL
        self.lots_table.cellDoubleClicked.connect(self.open_lot_url)
        self.lots_table.itemSelectionChanged.connect(self.on_lot_selected)
        self.lots_table.horizontalHeader().sectionClicked.connect(self.on_header_clicked)
        self.lots_splitter.addWidget(self.lots_table)

        # Details
        self.detail_panel = QWidget()
        detail_layout = QVBoxLayout(self.detail_panel)
        
        detail_layout.addWidget(QLabel("<b>📄 Детали лота:</b>"))
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setStyleSheet("background-color: #fdfefe; border: 1px solid #d5dbdb;")
        detail_layout.addWidget(self.detail_text)
        
        detail_layout.addWidget(QLabel("<b>🤖 Заключение AI:</b>"))
        self.detail_ai = QTextEdit()
        self.detail_ai.setReadOnly(True)
        self.detail_ai.setMaximumHeight(180)
        self.detail_ai.setStyleSheet("background-color: #f4faff; border: 1px solid #d6eaf8; color: #21618c;")
        detail_layout.addWidget(self.detail_ai)
        
        # AI & Geo Actions
        actions_layout = QHBoxLayout()
        
        self.ai_single_btn = QPushButton("🤖 Оценить AI")
        self.ai_single_btn.setFixedHeight(35)
        self.ai_single_btn.setStyleSheet("background-color: #e8f6f3; color: #16a085; font-weight: bold;")
        self.ai_single_btn.clicked.connect(self.run_ai_single)
        self.ai_single_btn.setEnabled(False)
        actions_layout.addWidget(self.ai_single_btn)
        
        self.geo_fix_btn = QPushButton("🗺️ Гео")
        self.geo_fix_btn.setFixedHeight(35)
        self.geo_fix_btn.setStyleSheet("background-color: #fef9e7; color: #d4ac0d; font-weight: bold;")
        self.geo_fix_btn.clicked.connect(self.refresh_geo)
        self.geo_fix_btn.setEnabled(False)
        actions_layout.addWidget(self.geo_fix_btn)
        
        detail_layout.addLayout(actions_layout)

        # Review Status Buttons
        review_layout = QHBoxLayout()
        self.review_approved_btn = QPushButton("✅")
        self.review_approved_btn.setToolTip("Принять")
        self.review_approved_btn.clicked.connect(lambda: self.change_review_status("approved"))
        
        self.review_maybe_btn = QPushButton("❓")
        self.review_maybe_btn.setToolTip("Под вопросом")
        self.review_maybe_btn.clicked.connect(lambda: self.change_review_status("maybe"))
        
        self.review_rejected_btn = QPushButton("❌")
        self.review_rejected_btn.setToolTip("Отклонить")
        self.review_rejected_btn.clicked.connect(lambda: self.change_review_status("rejected"))
        
        for btn in [self.review_approved_btn, self.review_maybe_btn, self.review_rejected_btn]:
            btn.setFixedHeight(35)
            btn.setEnabled(False)
            review_layout.addWidget(btn)
        
        detail_layout.addLayout(review_layout)
        
        self.delete_lot_btn = QPushButton("🗑️ Удалить выбранные")
        self.delete_lot_btn.setFixedHeight(40)
        self.delete_lot_btn.setStyleSheet("background-color: #fadbd8; color: #c0392b; font-weight: bold; border-radius: 5px;")
        self.delete_lot_btn.clicked.connect(self.delete_selected_lots)
        self.delete_lot_btn.setEnabled(False)
        detail_layout.addWidget(self.delete_lot_btn)
        
        self.lots_splitter.addWidget(self.detail_panel)
        self.lots_splitter.setSizes([850, 450])

    def set_sort(self, column, order):
        self.current_sort = (column, order)
        for btn in self.sort_group:
            btn.setChecked(False)
        if column == "last_update": self.sort_new_btn.setChecked(True)
        elif column == "current_price" and order == "asc": self.sort_cheap_btn.setChecked(True)
        elif column == "current_price" and order == "desc": self.sort_expensive_btn.setChecked(True)
        self.load_lots()

    def on_header_clicked(self, index: int):
        # Маппинг колонок на поля ProcessedLot
        col_map = {
            0: "external_id",
            1: "title",
            2: "region_slug",
            3: "current_price",
            4: "market_price",
            5: "discount_percent",
            6: "total_area_gba",
            7: "land_area",
            8: "auction_status",
            9: "category",
            10: "risk_score",
            11: "last_update",
        }
        field = col_map.get(index)
        if field:
            new_order = "asc" if self.current_sort[0] != field or self.current_sort[1] == "desc" else "desc"
            self.set_sort(field, new_order)

    def _init_map_tab_legacy_removed(self):
        main_layout = QHBoxLayout(self.map_tab)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Левая панель - карта
        map_container = QWidget()
        map_layout = QVBoxLayout(map_container)
        map_layout.setContentsMargins(0, 0, 0, 0)

        self.web_view = QWebEngineView()
        map_layout.addWidget(self.web_view)

        # Контролы карты
        controls = QHBoxLayout()
        controls.setContentsMargins(10, 5, 10, 5)

        map_btn = QPushButton("🔄 Обновить метки")
        map_btn.setFixedSize(150, 35)
        map_btn.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold; border-radius: 4px;")
        map_btn.clicked.connect(self.update_map)
        controls.addWidget(map_btn)

        self.cad_layer_checkbox = QCheckBox("Кадастровый слой")
        self.cad_layer_checkbox.setStyleSheet("font-weight: bold;")
        self.cad_layer_checkbox.stateChanged.connect(self.toggle_cadastral_layer)
        controls.addWidget(self.cad_layer_checkbox)

        controls.addStretch()
        map_layout.addLayout(controls)

        # Правая панель - поиск по кадастру
        sidebar = QWidget()
        sidebar.setMaximumWidth(350)
        sidebar.setStyleSheet("background: #f8f9fa; border-left: 1px solid #dee2e6;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(15, 15, 15, 15)

        # Заголовок
        header = QLabel("🔍 Поиск по кадастру")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #2c3e50; margin-bottom: 10px;")
        sidebar_layout.addWidget(header)

        # Поле ввода
        self.cadastral_search_input = QLineEdit()
        self.cadastral_search_input.setPlaceholderText("76:23:010101:15008")
        self.cadastral_search_input.setStyleSheet("padding: 8px; font-size: 13px; border: 1px solid #ced4da; border-radius: 4px;")
        sidebar_layout.addWidget(self.cadastral_search_input)

        # Кнопка поиска
        search_btn = QPushButton("🔎 Найти объект")
        search_btn.setFixedHeight(40)
        search_btn.setStyleSheet("background-color: #007bff; color: white; font-weight: bold; border-radius: 4px;")
        search_btn.clicked.connect(self.search_cadastral_object)
        sidebar_layout.addWidget(search_btn)

        # Результаты поиска
        self.cadastral_result_text = QTextEdit()
        self.cadastral_result_text.setReadOnly(True)
        self.cadastral_result_text.setStyleSheet("background: white; border: 1px solid #ced4da; border-radius: 4px; padding: 10px;")
        sidebar_layout.addWidget(self.cadastral_result_text)

        sidebar_layout.addStretch()

        # Добавляем в главный layout
        main_layout.addWidget(map_container, stretch=3)
        main_layout.addWidget(sidebar, stretch=1)

    def init_map_tab(self):
        main_layout = QHBoxLayout(self.map_tab)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setFixedWidth(360)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)

        title = QLabel("Кадастровый поиск")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        sidebar_layout.addWidget(title)

        self.cad_search_input = QLineEdit()
        self.cad_search_input.setPlaceholderText("Кадастровый номер или адрес")
        self.cad_suggestion_model = QStringListModel()
        self.cad_search_completer = QCompleter(self.cad_suggestion_model, self)
        self.cad_search_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.cad_search_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.cad_search_input.setCompleter(self.cad_search_completer)
        self.cad_search_input.textEdited.connect(self.update_cadastre_address_suggestions)
        sidebar_layout.addWidget(self.cad_search_input)

        self.cad_search_btn = QPushButton("Найти объект")
        self.cad_search_btn.clicked.connect(self.search_cadastre_from_gui)
        sidebar_layout.addWidget(self.cad_search_btn)

        self.cad_layer_checkbox = QCheckBox("Показать кадастровую подложку")
        self.cad_layer_checkbox.stateChanged.connect(self.toggle_cad_layer_from_gui)
        sidebar_layout.addWidget(self.cad_layer_checkbox)

        refresh_btn = QPushButton("Обновить метки лотов")
        refresh_btn.clicked.connect(self.refresh_all_map_markers)
        sidebar_layout.addWidget(refresh_btn)

        self.cad_info_text = QTextEdit()
        self.cad_info_text.setReadOnly(True)
        self.cad_info_text.setPlaceholderText("Введите кадастровый номер или адрес")
        sidebar_layout.addWidget(self.cad_info_text, stretch=1)

        main_layout.addWidget(sidebar)

        self.map_view = QWebEngineView()
        self.web_view = self.map_view
        main_layout.addWidget(self.map_view, stretch=1)

        self.update_map()

    def init_yandex_map_tab(self):
        main_layout = QHBoxLayout(self.yandex_map_tab)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setFixedWidth(360)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)

        title = QLabel("Кадастровый поиск")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        sidebar_layout.addWidget(title)

        self.yandex_cad_search_input = QLineEdit()
        self.yandex_cad_search_input.setPlaceholderText("Кадастровый номер или адрес")
        self.yandex_cad_suggestion_model = QStringListModel()
        self.yandex_cad_search_completer = QCompleter(self.yandex_cad_suggestion_model, self)
        self.yandex_cad_search_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.yandex_cad_search_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.yandex_cad_search_input.setCompleter(self.yandex_cad_search_completer)
        self.yandex_cad_search_input.textEdited.connect(self.update_yandex_cadastre_address_suggestions)
        sidebar_layout.addWidget(self.yandex_cad_search_input)

        self.yandex_cad_search_btn = QPushButton("Найти объект")
        self.yandex_cad_search_btn.clicked.connect(self.search_yandex_cadastre_from_gui)
        sidebar_layout.addWidget(self.yandex_cad_search_btn)

        self.yandex_cad_layer_checkbox = QCheckBox("Показать кадастровые границы")
        self.yandex_cad_layer_checkbox.setChecked(True)
        self.yandex_cad_layer_checkbox.stateChanged.connect(self.toggle_yandex_cad_layer_from_gui)
        sidebar_layout.addWidget(self.yandex_cad_layer_checkbox)

        refresh_btn = QPushButton("Обновить метки лотов")
        refresh_btn.clicked.connect(self.refresh_all_map_markers)
        sidebar_layout.addWidget(refresh_btn)

        self.yandex_cad_info_text = QTextEdit()
        self.yandex_cad_info_text.setReadOnly(True)
        self.yandex_cad_info_text.setPlaceholderText("Введите кадастровый номер или адрес")
        sidebar_layout.addWidget(self.yandex_cad_info_text, stretch=1)

        main_layout.addWidget(sidebar)

        self.yandex_map_view = QWebEngineView()
        main_layout.addWidget(self.yandex_map_view, stretch=1)

        self.update_yandex_map()

    def init_tools_tab(self):
        scroll_widget = QWidget()
        layout = QVBoxLayout(scroll_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Section Header Helper
        def add_section(title, color_bg, color_border):
            box = QWidget()
            box.setStyleSheet(f"background: {color_bg}; border: 1px solid {color_border}; border-radius: 8px;")
            box_layout = QVBoxLayout(box)
            lbl = QLabel(f"<b>{title}</b>")
            lbl.setStyleSheet("font-size: 14px; border: none; background: transparent;")
            box_layout.addWidget(lbl)
            return box, box_layout

        # 1. Online Sync (Auto Parser)
        online_box, online_layout = add_section("🌐 Автоматический парсер (Онлайн)", "#e8f8f5", "#a3e4d7")
        desc_online = QLabel("Поиск новых лотов на агрегаторах (GorodTorgi, TBankrot) по заданному региону.")
        desc_online.setStyleSheet("border: none; color: #16a085; background: transparent;")
        online_layout.addWidget(desc_online)
        
        region_layout = QHBoxLayout()
        region_layout.addWidget(QLabel("Регион:"))
        self.region_combo = QComboBox()
        self.region_mapping = {
            "Ярославская обл.": "yaroslavl",
            "Москва": "moskow",
            "Санкт-Петербург": "spb",
            "Ростовская обл.": "rostov",
            "Свердловская обл.": "sverdlovsk",
            "Кировская обл.": "kirov",
            "Псковская обл.": "pskov"
        }
        self.region_combo.addItems(list(self.region_mapping.keys()))
        self.region_combo.setFixedWidth(180)
        region_layout.addWidget(self.region_combo)
        region_layout.addStretch()
        online_layout.addLayout(region_layout)

        self.sync_btn = QPushButton("🚀 Запустить поиск новых лотов")
        self.sync_btn.setFixedHeight(45)
        self.sync_btn.setStyleSheet("background-color: #1abc9c; color: white; font-weight: bold; border-radius: 5px;")
        self.sync_btn.clicked.connect(self.run_online_sync)
        online_layout.addWidget(self.sync_btn)
        # Excel export button
        self.export_btn = QPushButton("📊 Экспорт в Excel")
        self.export_btn.setFixedHeight(45)
        self.export_btn.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold; border-radius: 5px;")
        self.export_btn.clicked.connect(self.export_to_excel)
        online_layout.addWidget(self.export_btn)
        layout.addWidget(online_box)
        
        # 2. Offline Parser (HTML)
        offline_box, offline_layout = add_section("📂 Оффлайн парсер (HTML файлы)", "#fef9e7", "#f9e79f")
        desc_offline = QLabel("Загрузка лотов из сохраненных HTML-страниц (TBankrot и др.).")
        desc_offline.setStyleSheet("border: none; color: #d4ac0d; background: transparent;")
        offline_layout.addWidget(desc_offline)
        
        self.html_btn = QPushButton("📄 Выбрать HTML файл для импорта")
        self.html_btn.setFixedHeight(45)
        self.html_btn.setStyleSheet("background-color: #f1c40f; color: #7d6608; font-weight: bold; border-radius: 5px;")
        self.html_btn.clicked.connect(self.run_offline_parse)
        offline_layout.addWidget(self.html_btn)
        layout.addWidget(offline_box)
        
        # 3. AI Appraisal
        ai_box, ai_layout = add_section("🤖 Искусственный интеллект (OpenAI)", "#ebf5fb", "#aed6f1")
        desc_ai = QLabel("Массовая оценка инвестиционной привлекательности и рисков для новых лотов.")
        desc_ai.setStyleSheet("border: none; color: #2e86c1; background: transparent;")
        ai_layout.addWidget(desc_ai)
        
        self.ai_batch_btn = QPushButton("🧬 Оценить все необработанные лоты")
        self.ai_batch_btn.setFixedHeight(45)
        self.ai_batch_btn.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; border-radius: 5px;")
        self.ai_batch_btn.clicked.connect(self.run_ai_batch)
        ai_layout.addWidget(self.ai_batch_btn)
        
        self.geo_batch_btn = QPushButton("🗺️ Массовое геокодирование")
        self.geo_batch_btn.setFixedHeight(45)
        self.geo_batch_btn.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold; border-radius: 5px;")
        self.geo_batch_btn.clicked.connect(self.run_geo_batch)
        ai_layout.addWidget(self.geo_batch_btn)
        
        layout.addWidget(ai_box)

        # 2.3 UI: AI status overview (simple panel)
        ai_status_box, ai_status_layout = add_section("🧭 AI Статус", "#eef7ff", "#a5c8e8")
        self.ai_status_label = QLabel("Статус: не запущено")
        ai_status_layout.addWidget(self.ai_status_label)
        self.ai_status_progress = QProgressBar()
        self.ai_status_progress.setRange(0, 100)
        self.ai_status_progress.setValue(0)
        ai_status_layout.addWidget(self.ai_status_progress)
        self.export_btn.setVisible(True)  # ensure export exists on UI
        layout.addWidget(ai_status_box)

        # 4. Maintenance
        clean_box, clean_layout = add_section("🧹 Обслуживание базы данных", "#fdf2f2", "#f5c6cb")
        self.cleanup_btn = QPushButton("🗑️ Удалить завершенные торги (архив)")
        self.cleanup_btn.setFixedHeight(40)
        self.cleanup_btn.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; border-radius: 5px;")
        self.cleanup_btn.clicked.connect(self.run_cleanup)
        clean_layout.addWidget(self.cleanup_btn)
        layout.addWidget(clean_box)

        # 5. AI Settings
        from bankrotai.core import get_app_setting
        ai_set_box, ai_set_layout = add_section("⚙️ Настройки AI Провайдера", "#f4f6f7", "#ccd1d1")
        form = QFormLayout()
        
        self.provider_combo = QComboBox()
        for provider_id, label in AI_PROVIDER_OPTIONS:
            self.provider_combo.addItem(label, provider_id)
        current_provider = get_app_setting("ai_provider", "omniroute") or "omniroute"
        provider_index = self.provider_combo.findData(current_provider)
        self.provider_combo.setCurrentIndex(provider_index if provider_index >= 0 else 0)
        self.provider_combo.currentIndexChanged.connect(self.on_ai_provider_changed)
        form.addRow("Провайдер:", self.provider_combo)
        
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        form.addRow("API Ключ:", self.api_key_input)
        
        self.model_search_input = QComboBox()
        form.addRow("Модель поиска:", self.model_search_input)
        
        save_settings_btn = QPushButton("💾 Сохранить настройки AI")
        self.on_ai_provider_changed()
        save_settings_btn.clicked.connect(self.save_ai_settings)
        ai_set_layout.addLayout(form)
        ai_set_layout.addWidget(save_settings_btn)
        layout.addWidget(ai_set_box)

        layout.addStretch()
        
        # Set tools_tab content
        main_tools_layout = QVBoxLayout(self.tools_tab)
        main_tools_layout.addWidget(scroll_widget)

    def update_dashboard(self):
        try:
            region_display = self.region_combo.currentText()
            city_slug = self.region_mapping.get(region_display, "yaroslavl")
            
            with session_scope() as session:
                total = session.query(func.count(ProcessedLot.id)).scalar()
                active = session.query(func.count(ProcessedLot.id)).filter(ProcessedLot.auction_status == "active").scalar()
                with_rating = session.query(func.count(ProcessedLot.id)).filter(ProcessedLot.rating.isnot(None)).scalar()
                
                from bankrotai.db import RegionSyncState
                sync_state = session.query(RegionSyncState).filter(RegionSyncState.city_slug == city_slug).first()
                last_sync = sync_state.last_success_at.strftime("%d.%m.%Y %H:%M") if sync_state and sync_state.last_success_at else "никогда"
                
                text = (
                    f"📈 <b>Общая статистика ({region_display}):</b>\n\n"
                    f"• Всего лотов в базе: {total}\n"
                    f"• Активных торгов: {active}\n"
                    f"• Оценено Искусственным Интеллектом: {with_rating}\n\n"
                    f"🔄 Последняя онлайн синхронизация: {last_sync}"
                )
                self.stats_label.setText(text)
        except Exception as e:
            self.stats_label.setText(f"Ошибка статистики: {e}")
        self.load_lots() # Refresh table as well


    def load_lots(self):
        search_text = self.search_input.text().strip()
        category_filter = self.category_combo.currentText()
        sort_col, sort_order = self.current_sort

        # Disable sorting while loading items
        self.lots_table.setSortingEnabled(False)

        with session_scope() as session:
            query = select(ProcessedLot)
            
            if search_text:
                query = query.where(ProcessedLot.title.ilike(f"%{search_text}%"))
            
            if category_filter != "Все категории":
                # Маппинг категорий на внутренние значения
                cat_map = {
                    "Жилая недвижимость": ["apartment", "house", "living"],
                    "Коммерческая недвижимость": [
                        "commercial", 
                        "commercial_room", 
                        "commercial_building", 
                        "commercial_building_with_land", 
                        "complex", 
                        "office", 
                        "retail"
                    ],
                    "Земельные участки": ["land"],
                    "Транспорт": ["car", "transport", "vehicle"],
                    "Прочее": ["equipment", "parking", "unfinished", "other"]
                }
                allowed_cats = cat_map.get(category_filter, [])
                if allowed_cats:
                    query = query.where(ProcessedLot.category.in_(allowed_cats))

            # Сортировка по умолчанию (БД)
            col_attr = getattr(ProcessedLot, sort_col)
            if sort_order == "desc":
                query = query.order_by(desc(col_attr))
            else:
                query = query.order_by(col_attr)

            lots = session.scalars(query).all()
            self.lots_table.setRowCount(len(lots))
            for i, lot in enumerate(lots):
                # Helper for numeric items
                def num_item(val, suffix=""):
                    if val is None: return QTableWidgetItem("—")
                    try:
                        # Convert to float then to int to ensure no decimals and no scientific notation
                        iv = int(float(val))
                        # Use fixed-point formatting with space as thousands separator
                        formatted_val = f"{iv:,}".replace(",", " ")
                        item = QTableWidgetItem(f"{formatted_val}{suffix}")
                        item.setData(Qt.EditRole, iv)
                        return item
                    except (ValueError, TypeError):
                        return QTableWidgetItem(str(val))

                def format_land_area(area_m2: float | None) -> str: 
                    if not area_m2: 
                        return "" 
                    sotki = area_m2 / 100 
                    return f"{area_m2:,.0f} м² / {sotki:.2f} сот.".replace(",", " ")

                items = [
                    QTableWidgetItem(str(lot.external_id)),
                    QTableWidgetItem(lot.title),
                    QTableWidgetItem(lot.region_slug or ""),
                    num_item(lot.current_price, " ₽"),
                    num_item(lot.market_price, " ₽"),
                    num_item(lot.discount_percent, "%"),
                    QTableWidgetItem(f"{lot.total_area_gba:,.1f} м²".replace(",", " ") if lot.total_area_gba else ""),
                    QTableWidgetItem(format_land_area(lot.land_area)),
                    QTableWidgetItem(translate_status(lot.auction_status)),
                    QTableWidgetItem(translate_category(lot.category)),
                    QTableWidgetItem(str(lot.risk_score) if lot.risk_score is not None else ""),
                    QTableWidgetItem(lot.last_update.strftime("%d.%m.%Y %H:%M"))
                ]
                
                # Review Status Color
                color = QColor("white")
                if lot.review_status == "approved": color = QColor("#d4edda")
                elif lot.review_status == "rejected": color = QColor("#f8d7da")
                elif lot.review_status == "maybe": color = QColor("#fff3cd")
                
                for j, item in enumerate(items):
                    item.setBackground(QBrush(color))
                    self.lots_table.setItem(i, j, item)

        # Re-enable sorting
        self.lots_table.setSortingEnabled(True)

    def on_lot_selected(self):
        selected_items = self.lots_table.selectedItems()
        if not selected_items:
            self.delete_lot_btn.setEnabled(False)
            self.ai_single_btn.setEnabled(False)
            self.geo_fix_btn.setEnabled(False)
            self.geo_fix_btn.setText("🗺️ Гео")
            for btn in [self.review_approved_btn, self.review_maybe_btn, self.review_rejected_btn]:
                btn.setEnabled(False)
            return
        rows = sorted(list(set(item.row() for item in selected_items)))
        count = len(rows)
        self.delete_lot_btn.setEnabled(True)
        self.delete_lot_btn.setText(f"🗑️ Удалить выбранные ({count})")
        
        # AI evaluation enabled for 1 or more lots
        self.ai_single_btn.setEnabled(True)
        self.ai_single_btn.setText(f"🤖 Оценить AI ({count})" if count > 1 else "🤖 Оценить AI")
        
        if count == 1:
            self.geo_fix_btn.setEnabled(True)
            self.geo_fix_btn.setText("🗺️ Гео")
            for btn in [self.review_approved_btn, self.review_maybe_btn, self.review_rejected_btn]:
                btn.setEnabled(True)
            
            ext_id = self.lots_table.item(rows[0], 0).text()
            with session_scope() as session:
                lot = session.scalar(select(ProcessedLot).where(ProcessedLot.external_id == ext_id))
                if lot:
                    self.current_selected_lot_id = lot.id
                    
                    # Helper for integer formatting in text
                    def fmt_int(val):
                        if val is None: return "—"
                        try:
                            return f"{int(float(val)):,}".replace(",", " ")
                        except (ValueError, TypeError):
                            return str(val)

                    info = [
                        f"🏷️ ЛОТ: {lot.title}",
                        f"🆔 КОД: {lot.external_id}",
                        f"📍 АДРЕС: {lot.address or 'не указан'}",
                        f"💰 ЦЕНА: {fmt_int(lot.current_price)} ₽",
                        f"📊 СТАТУС: {translate_status(lot.auction_status)}",
                        f"📈 РЕЙТИНГ: {fmt_int(lot.rating)}",
                    ]
                    
                    # Добавляем новые инвестиционные поля если они заполнены
                    if lot.object_name: info.append(f"🏢 ОБЪЕКТ: {lot.object_name}")
                    if lot.property_type: info.append(f"🏗️ ТИП: {lot.property_type}")
                    if lot.total_area_gba: info.append(f"📐 ПЛОЩАДЬ (GBA): {fmt_int(lot.total_area_gba)} м²")
                    if lot.cadastral_number: info.append(f"🔢 КАДАСТР: {lot.cadastral_number}")
                    if lot.floors: info.append(f"🏢 ЭТАЖЕЙ: {fmt_int(lot.floors)}")
                    if lot.legal_status: info.append(f"⚖️ СТАТУС: {lot.legal_status}")
                    
                    info.append("-" * 20)
                    
                    # Clean description from HTML if any, to avoid accidental italics
                    desc_text = lot.description or ""
                    if "<" in desc_text and ">" in desc_text:
                        desc_text = re.sub('<[^<]+?>', '', desc_text)
                    
                    info.append(f"📑 ОПИСАНИЕ:\n{desc_text}")
                    
                    # Use setPlainText to be 100% sure no HTML formatting is applied
                    self.detail_text.setPlainText("\n".join(info))
                    self.detail_ai.setText(lot.ai_recommendation or "AI анализ не проводился.")
        else:
            self.geo_fix_btn.setEnabled(True)
            self.geo_fix_btn.setText(f"Гео ({count})")
            for btn in [self.review_approved_btn, self.review_maybe_btn, self.review_rejected_btn]:
                btn.setEnabled(False)
            self.detail_text.setPlainText(f"Выбрано объектов: {count}")
            self.detail_ai.clear()

    def open_lot_url(self, row: int, column: int) -> None:
        ext_id = self.lots_table.item(row, 0).text() if self.lots_table.item(row, 0) else None
        if not ext_id:
            return
        with session_scope() as session:
            lot = session.scalar(select(ProcessedLot).where(ProcessedLot.external_id == ext_id))
            url = (lot.source_url or lot.lot_url) if lot else None
            if url:
                QDesktopServices.openUrl(QUrl.fromUserInput(str(url)))
            else:
                QMessageBox.information(self, "Инфо", "Ссылка на лот отсутствует.")

    def export_to_excel(self) -> None: 
        """Экспорт текущей таблицы в Excel""" 
        default_name = f"bankrotai_export_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.xlsx" 
        desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation) 
        default_path = f"{desktop}/{default_name}" 

        path, _ = QFileDialog.getSaveFileName( 
            self, 
            "Сохранить Excel файл", 
            default_path, 
            "Excel files (*.xlsx)" 
        ) 
        if not path: 
            return 

        try: 
            with session_scope() as session: 
                search_text = self.search_input.text().strip() 
                category_filter = self.category_combo.currentText() 
                sort_col, sort_order = self.current_sort 

                query = select(ProcessedLot) 

                if search_text: 
                    query = query.where(ProcessedLot.title.ilike(f"%{search_text}%")) 

                if category_filter != "Все категории": 
                    cat_map = { 
                        "Жилая недвижимость": ["apartment", "house", "living"], 
                        "Коммерческая недвижимость": [
                            "commercial", 
                            "commercial_room", 
                            "commercial_building", 
                            "commercial_building_with_land", 
                            "complex", 
                            "office", 
                            "retail"
                        ], 
                        "Земельные участки": ["land"], 
                        "Транспорт": ["car", "transport", "vehicle"], 
                        "Прочее": ["equipment", "parking", "unfinished", "other"], 
                    } 
                    allowed = cat_map.get(category_filter, []) 
                    if allowed: 
                        query = query.where(ProcessedLot.category.in_(allowed)) 

                col_attr = getattr(ProcessedLot, sort_col) 
                query = query.order_by(desc(col_attr) if sort_order == "desc" else col_attr) 

                lots = session.scalars(query).all() 

                # Полностью материализуем все нужные данные пока сессия открыта 
                data = [] 
                for lot in lots: 
                    # Helper for safe integer conversion
                    def to_int(val):
                        try:
                            return int(float(val)) if val is not None else None
                        except (ValueError, TypeError):
                            return None

                    row = { 
                        "Код": lot.external_id, 
                        "Название": lot.title, 
                        "Описание": lot.description, 
                        "Категория": translate_category(lot.category), 
                        "Регион": lot.region_slug, 
                        "Адрес": lot.address, 
                        "Кадастровый номер": lot.cadastral_number, 
                        "Все кадастровые номера": ", ".join((lot.cadastral_numbers or [])) if hasattr(lot, "cadastral_numbers") and lot.cadastral_numbers else None,
                        "Текущая цена": to_int(lot.current_price), 
                        "Рыночная цена": to_int(lot.market_price), 
                        "Дисконт %": to_int(lot.discount_percent), 
                        "Риск (1-10)": to_int(lot.risk_score), 
                        "Рейтинг": to_int(lot.rating), 
                        "Статус торгов": translate_status(lot.auction_status), 
                        "Ссылка": lot.lot_url, 
                        "Площадь здания/помещения, м²": lot.total_area_gba, 
                        "Площадь участка, м²": lot.land_area, 
                        "Площадь участка, сот.": round(lot.land_area / 100, 2) if lot.land_area else None, 
                        "Этажность": to_int(lot.floors), 
                        "Юридический статус": lot.legal_status, 
                        "Статус проверки": lot.review_status, 
                        "Обновление": lot.last_update.strftime("%d.%m.%Y %H:%M") if lot.last_update else None, 
                    } 
                    data.append(row) 

            # === Экспорт после закрытия сессии === 
            wb = Workbook() 
            ws = wb.active 
            ws.title = "Реестр лотов" 

            if not data:
                QMessageBox.information(self, "Инфо", "Нет данных для экспорта.")
                return

            headers = list(data[0].keys()) 
            ws.append(headers) 

            # Identify numeric columns for formatting
            numeric_headers = {
                "Текущая цена", "Рыночная цена", "Дисконт %", 
                "Риск (1-10)", "Рейтинг", "Площадь здания/помещения, м²", 
                "Площадь участка, м²", "Площадь участка, сот.", "Этажность"
            }

            for row_dict in data: 
                row_data = [row_dict.get(h) for h in headers]
                ws.append(row_data)
                
                # Format numeric cells in the last added row
                curr_row = ws.max_row
                for idx, header in enumerate(headers):
                    if header in numeric_headers:
                        cell = ws.cell(row=curr_row, column=idx + 1)
                        if cell.value is not None:
                            # Use number format with thousands separator and no scientific notation
                            # Force as integer format
                            cell.number_format = '#,##0'

            # Auto-adjust column widths
            for col in ws.columns:
                max_length = 0
                column = col[0].column_letter
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = (max_length + 2)
                ws.column_dimensions[column].width = min(adjusted_width, 50) # limit width

            wb.save(path) 
            self.status_bar.showMessage(f"Экспорт завершён: {path}", 5000) 
            QMessageBox.information(self, "Успех", f"Файл успешно сохранён:\n{path}") 

        except PermissionError: 
            QMessageBox.critical(self, "Ошибка прав доступа", 
                "Нет прав на запись в выбранную папку.\n\nПопробуйте сохранить на Рабочий стол.") 
        except Exception as e: 
            logger.exception("Export error") 
            QMessageBox.critical(self, "Ошибка экспорта", f"Произошла ошибка:\n{str(e)}") 

    def delete_selected_lots(self):
        selected_items = self.lots_table.selectedItems()
        if not selected_items: return
        rows = sorted(list(set(item.row() for item in selected_items)))
        ext_ids = [self.lots_table.item(r, 0).text() for r in rows]
        if QMessageBox.question(self, "Удаление", f"Удалить {len(ext_ids)} лотов?") == QMessageBox.Yes:
            with session_scope() as session:
                lots = session.scalars(select(ProcessedLot.id).where(ProcessedLot.external_id.in_(ext_ids))).all()
                delete_lots_batch(session, list(lots))
            self.load_lots()
            self.update_dashboard()

    def get_selected_lot_ids(self) -> list[int]:
        selected_items = self.lots_table.selectedItems()
        if not selected_items:
            return []

        rows = sorted({item.row() for item in selected_items})
        ext_ids = [
            self.lots_table.item(row, 0).text()
            for row in rows
            if self.lots_table.item(row, 0)
        ]
        if not ext_ids:
            return []

        with session_scope() as session:
            return list(
                session.scalars(
                    select(ProcessedLot.id).where(ProcessedLot.external_id.in_(ext_ids))
                ).all()
            )

    def start_geo_worker(
        self,
        *,
        lot_ids: list[int] | None = None,
        refresh_existing: bool = False,
        limit: int | None = None,
    ):
        if getattr(self, "geo_worker", None) and self.geo_worker.isRunning():
            QMessageBox.information(self, "GEO", "Геокодирование уже выполняется. Дождитесь завершения текущей задачи.")
            return
        self.geo_fix_btn.setEnabled(False)
        if hasattr(self, "geo_batch_btn"):
            self.geo_batch_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.start_task_progress("geo", "GEO")

        self.geo_worker = GeoWorker(
            limit=limit,
            lot_ids=lot_ids,
            refresh_existing=refresh_existing,
        )
        self.geo_worker.progress.connect(self.status_bar.showMessage)
        self.geo_worker.progress_percent.connect(self.progress_bar.setValue)
        self.geo_worker.progress_percent.connect(lambda value: self.update_task_progress("geo", value))
        self.geo_worker.finished.connect(self.on_geo_finished)
        self.geo_worker.error.connect(self.on_worker_error)
        self.geo_worker.start()

    def run_online_sync(self):
        if getattr(self, "geo_worker", None) and self.geo_worker.isRunning():
            QMessageBox.warning(
                self,
                "Фоновая задача",
                "Сейчас идет массовое геокодирование. Дождитесь завершения GEO перед запуском поиска новых лотов.",
            )
            return
        if getattr(self, "sync_worker", None) and self.sync_worker.isRunning():
            return
        self.sync_btn.setEnabled(False)
        self.sync_btn.setText("⏳ Синхронизация...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0) # Indeterminate
        
        region_display = self.region_combo.currentText()
        city_slug = self.region_mapping.get(region_display, "yaroslavl")
        
        self.sync_worker = SyncWorker(city_slug)
        self.sync_worker.progress.connect(self.status_bar.showMessage)
        self.sync_worker.finished.connect(self.on_sync_finished)
        self.sync_worker.error.connect(self.on_worker_error)
        self.sync_worker.start()

    def on_sync_finished(self, count: int):
        self.sync_btn.setEnabled(True)
        self.sync_btn.setText("🚀 Запустить поиск новых лотов")
        self.progress_bar.setVisible(False)
        self.status_bar.showMessage(f"Синхронизация завершена. Найдено лотов: {count}", 5000)
        QMessageBox.information(self, "Готово", f"Синхронизация завершена! Найдено лотов: {count}")
        self.load_lots()
        self.update_dashboard()

    def run_offline_parse(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выбрать HTML файл", "", "HTML Files (*.html *.htm)")
        if not file_path: return
        
        self.html_btn.setEnabled(False)
        self.status_bar.showMessage(f"Импорт из файла: {file_path}...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        
        self.import_worker = ImportWorker(file_path, "yaroslavl")
        self.import_worker.progress.connect(self.status_bar.showMessage)
        self.import_worker.finished.connect(self.on_import_finished)
        self.import_worker.error.connect(self.on_worker_error)
        self.import_worker.start()

    def on_import_finished(self, new: int, upd: int, skip: int):
        self.html_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        msg = f"Импорт завершен.\nДобавлено новых: {new}\nОбновлено: {upd}\nПропущено (проверены): {skip}"
        self.status_bar.showMessage(f"Импорт: +{new}, ~{upd}, ={skip}", 5000)
        QMessageBox.information(self, "Результат импорта", msg)
        self.load_lots()
        self.update_dashboard()

    def get_appraiser(self) -> OpenAIAppraiser | None:
        try:
            if self._appraiser is None:
                self._appraiser = OpenAIAppraiser()
            return self._appraiser
        except Exception as e:
            logger.error(f"Failed to initialize AI Appraiser: {e}")
            QMessageBox.critical(self, "Ошибка AI", f"Не удалось инициализировать модуль AI:\n\n{str(e)}")
            return None

    def run_ai_single(self):
        selected_items = self.lots_table.selectedItems()
        if not selected_items: return
        
        rows = sorted(list(set(item.row() for item in selected_items)))
        ext_ids = [self.lots_table.item(r, 0).text() for r in rows]
        
        appraiser = self.get_appraiser()
        if not appraiser: return

        with session_scope() as session:
            lots = session.scalars(select(ProcessedLot.id).where(ProcessedLot.external_id.in_(ext_ids))).all()
            lot_ids = list(lots)

        if not lot_ids: return

        self.ai_single_btn.setEnabled(False)
        self.ai_last_failed_count = 0
        self.ai_status_label.setText("Запуск оценки...") 
        self.ai_status_progress.setValue(0) 
        self.status_bar.showMessage(f"AI Оценка выбранных лотов ({len(lot_ids)})...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        
        self.ai_worker = AIWorker(appraiser=appraiser, lot_ids=lot_ids)
        self.ai_worker.progress.connect(self.status_bar.showMessage)
        self.ai_worker.progress.connect(self.ai_status_label.setText) 
        self.ai_worker.progress_percent.connect(self.ai_status_progress.setValue) 
        self.ai_worker.progress_percent.connect(self.progress_bar.setValue)
        self.ai_worker.progress_percent.connect(lambda value: self.update_task_progress("ai", value))
        self.ai_worker.lot_finished.connect(self.on_ai_lot_finished)
        self.ai_worker.lot_failed.connect(self.on_ai_lot_failed)
        self.ai_worker.finished.connect(self.on_ai_finished)
        self.ai_worker.error.connect(self.on_worker_error)
        self.ai_worker.start()

    def run_ai_batch(self):
        appraiser = self.get_appraiser()
        if not appraiser: return

        self.ai_batch_btn.setEnabled(False)
        self.ai_last_failed_count = 0
        self.ai_status_label.setText("Запуск пакетной оценки...") 
        self.ai_status_progress.setValue(0) 
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.start_task_progress("ai", "AI")
        
        self.ai_worker = AIWorker(appraiser=appraiser, limit=15) # Увеличим лимит до 15
        self.ai_worker.progress.connect(self.status_bar.showMessage)
        self.ai_worker.progress.connect(self.ai_status_label.setText) 
        self.ai_worker.progress_percent.connect(self.ai_status_progress.setValue) 
        self.ai_worker.progress_percent.connect(self.progress_bar.setValue)
        self.ai_worker.progress_percent.connect(lambda value: self.update_task_progress("ai", value))
        self.ai_worker.lot_finished.connect(self.on_ai_lot_finished)
        self.ai_worker.lot_failed.connect(self.on_ai_lot_failed)
        self.ai_worker.finished.connect(self.on_ai_finished)
        self.ai_worker.error.connect(self.on_worker_error)
        self.ai_worker.start()

    def run_geo_batch(self):
        self.start_geo_worker(limit=None, refresh_existing=False)

    def refresh_all_map_markers(self):
        self.status_bar.showMessage("Обновление геометок лотов из базы...")
        self.start_geo_worker(limit=None, refresh_existing=False)

    def on_geo_finished(self, count: int):
        self.finish_task_progress("geo")
        if hasattr(self, "geo_batch_btn"):
            self.geo_batch_btn.setEnabled(True)
        self.geo_fix_btn.setEnabled(bool(self.lots_table.selectedItems()))
        self.progress_bar.setVisible(False)
        if count == 0:
            self.status_bar.showMessage("Все лоты уже имеют гео-координаты", 5000)
            QMessageBox.information(self, "Инфо", "Все лоты уже имеют гео-координаты.")
        else:
            self.status_bar.showMessage(f"Геокодирование завершено. Обработано: {count}", 5000)
            QMessageBox.information(self, "Готово", f"Массовое геокодирование завершено. Обработано лотов: {count}")
        self.update_map()
        if hasattr(self, "yandex_map_view"):
            self.update_yandex_map()

    def on_ai_lot_finished(self, lot_id: int, processed_count: int):
        self.load_lots()
        self.update_dashboard()
        self.status_bar.showMessage(f"AI оценка готова для лота ID {lot_id}. Обработано: {processed_count}", 5000)
        self.ai_status_label.setText(f"Готова оценка лота ID {lot_id}. Обработано: {processed_count}")

    def on_ai_lot_failed(self, lot_id: int, error_msg: str, failed_count: int):
        self.ai_last_failed_count = failed_count
        short_error = (error_msg or "").replace("\n", " ")[:160]
        self.status_bar.showMessage(
            f"AI ошибка лота ID {lot_id}; продолжаю. Ошибок: {failed_count}",
            7000,
        )
        self.ai_status_label.setText(
            f"Пропущен лот ID {lot_id}. Ошибок: {failed_count}. {short_error}"
        )

    def on_ai_finished(self, count: int):
        self.finish_task_progress("ai")
        self.ai_batch_btn.setEnabled(True)
        self.ai_single_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.ai_status_label.setText("Готово") 
        self.ai_status_progress.setValue(0) 
        failed_count = getattr(self, "ai_last_failed_count", 0)
        if count == 0 and failed_count:
            message = f"AI оценка завершена с ошибками. Успешно: 0, ошибок: {failed_count}"
            self.status_bar.showMessage(message, 5000)
            QMessageBox.information(self, "Готово", message)
        elif count == 0:
            self.status_bar.showMessage("Нет лотов для оценки", 5000)
            QMessageBox.information(self, "Инфо", "Нет лотов для оценки.")
        elif failed_count:
            message = f"AI оценка завершена. Успешно: {count}, ошибок: {failed_count}"
            self.status_bar.showMessage(message, 5000)
            QMessageBox.information(self, "Готово", message)
        else:
            self.status_bar.showMessage(f"AI Оценка завершена. Обработано: {count}", 5000)
            QMessageBox.information(self, "Готово", f"AI оценка завершена. Обработано лотов: {count}")
        self.load_lots()
        self.update_dashboard()

    def on_worker_error(self, error_msg: str):
        # Сброс состояния всех кнопок
        self.sync_btn.setEnabled(True)
        self.sync_btn.setText("🚀 Запуск поиск новых лотов")
        self.html_btn.setEnabled(True)
        self.ai_batch_btn.setEnabled(True)
        self.ai_single_btn.setEnabled(True)
        if hasattr(self, "geo_batch_btn"):
            self.geo_batch_btn.setEnabled(True)
        if hasattr(self, "geo_fix_btn"):
            self.geo_fix_btn.setEnabled(bool(self.lots_table.selectedItems()))
        self.finish_task_progress("ai")
        self.finish_task_progress("geo")
        self.finish_task_progress("sync")
        self.finish_task_progress("import")
        self.progress_bar.setVisible(False)
        self.ai_status_label.setText("Ошибка") 
        self.ai_status_progress.setValue(0) 
        
        self.status_bar.showMessage(f"Ошибка: {error_msg[:50]}...", 10000)
        
        if "429" in error_msg:
            QMessageBox.warning(self, "Лимит запросов", 
                "Достигнут лимит запросов к AI (Error 429).\n\n"
                "Система автоматически делала повторные попытки, но лимит все еще активен. "
                "Пожалуйста, подождите 1-2 минуты или проверьте настройки API ключа.")
        else:
            QMessageBox.critical(self, "Ошибка воркера", f"Произошла ошибка при выполнении фоновой задачи:\n\n{error_msg}")

    def on_ai_provider_changed(self):
        from bankrotai.core import get_app_setting
        provider = self.provider_combo.currentData() or "omniroute"
        current_key = get_app_setting(f"{provider}_api_key", "")
        current_model = get_app_setting(f"{provider}_model", "")

        self.api_key_input.setText(current_key or "")
        self.model_search_input.clear()
        for model_id, label in AI_MODEL_OPTIONS.get(provider, []):
            self.model_search_input.addItem(label, model_id)

        model_index = self.model_search_input.findData(current_model)
        self.model_search_input.setCurrentIndex(model_index if model_index >= 0 else 0)

    def save_ai_settings(self):
        provider = self.provider_combo.currentData() or "omniroute"
        api_key = self.api_key_input.text().strip()
        model = self.model_search_input.currentData() or ""

        from bankrotai.core import set_app_setting
        set_app_setting("ai_provider", provider)
        if api_key:
            set_app_setting(f"{provider}_api_key", api_key)
        if model:
            set_app_setting(f"{provider}_model", model)
        if provider == "omniroute":
            set_app_setting("omniroute_protocol", "openai")
            
        self._appraiser = None # Сброс кеша для создания нового с новыми настройками
        self.status_bar.showMessage("Настройки AI сохранены", 3000)
        QMessageBox.information(self, "Успех", "Настройки AI успешно сохранены.")

    def change_review_status(self, status: str):
        if not self.current_selected_lot_id: return
        with session_scope() as session:
            lot = session.get(ProcessedLot, self.current_selected_lot_id)
            if lot:
                lot.review_status = status
                session.commit()
        self.status_bar.showMessage(f"Статус изменен: {status}", 3000)
        self.load_lots()

    def refresh_geo(self):
        if not self.current_selected_lot_id: return
        self.status_bar.showMessage("Обновление координат...")
        with session_scope() as session:
            lot = session.get(ProcessedLot, self.current_selected_lot_id)
            if lot:
                # Удаляем старые координаты
                from bankrotai.db import LotGeoSnapshot
                session.query(LotGeoSnapshot).filter_by(lot_id=lot.id).delete()
                from bankrotai.geo import enrich_lot_geo
                enrich_lot_geo(session, lot)
                session.commit()
        self.status_bar.showMessage("Геокодирование завершено", 3000)
        self.update_map()

    def refresh_geo(self):
        lot_ids = self.get_selected_lot_ids()
        if not lot_ids:
            return
        self.status_bar.showMessage(f"Обновление геометок выбранных лотов: {len(lot_ids)}")
        self.start_geo_worker(lot_ids=lot_ids, refresh_existing=True, limit=None)

    def run_cleanup(self):
        if QMessageBox.question(self, "Очистка", "Удалить все завершенные торги?") == QMessageBox.Yes:
            with session_scope() as session:
                count = cleanup_closed_lots(session)
            QMessageBox.information(self, "Готово", f"Удалено лотов: {count}")
            self.load_lots()
            self.update_dashboard()

    def _update_map_legacy_removed(self):
        self.status_bar.showMessage("Загрузка карты (Leaflet/OSM)...")
        lots_with_geo = []
        try:
            with session_scope() as session:
                stmt = select(LotGeoSnapshot.centroid_lat, LotGeoSnapshot.centroid_lon, ProcessedLot.title, ProcessedLot.current_price, ProcessedLot.address, ProcessedLot.cadastral_number).join(ProcessedLot, LotGeoSnapshot.lot_id == ProcessedLot.id).where(LotGeoSnapshot.centroid_lat.isnot(None)).limit(700)
                for lat, lon, title, price, addr, cad in session.execute(stmt):
                    lots_with_geo.append({
                        "lat": float(lat),
                        "lng": float(lon),
                        "title": str(title)[:80],
                        "price": f"{float(price or 0):,.0f} ₽",
                        "address": str(addr or "")[:100],
                        "cadastral": str(cad or "")
                    })
        except Exception as e:
            print(f"Map error: {e}")

        if not lots_with_geo:
            lots_with_geo = [{"lat": 57.6261, "lng": 39.8845, "title": "Ярославль (Центр)", "price": "—", "address": "Ярославль", "cadastral": ""}]

        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
    <style>
        html, body, #map {{ width: 100%; height: 100%; margin: 0; padding: 0; }}
        .leaflet-popup-content-wrapper {{ border-radius: 8px; font-family: sans-serif; }}
        .popup-title {{ font-weight: bold; color: #2c3e50; font-size: 14px; margin-bottom: 5px; }}
        .popup-price {{ color: #27ae60; font-weight: bold; font-size: 13px; }}
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        var map = L.map('map').setView([57.62, 39.88], 10);
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }}).addTo(map);

        var cadastralLayer = L.tileLayer.wms('https://pkk.rosreestr.ru/arcgis/services/Cadastre/Cadastre/MapServer/WmsServer?', {{
            layers: '1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24',
            format: 'image/png',
            transparent: true,
            opacity: 0.7,
            attribution: 'Росреестр'
        }});

        window.setCadLayerVisible = function(visible) {{
            if (visible) {{
                map.addLayer(cadastralLayer);
            }} else {{
                map.removeLayer(cadastralLayer);
            }}
        }};

        window.showCadastreObject = function(data) {{
            if (data.centroid_lat && data.centroid_lon) {{
                map.setView([data.centroid_lat, data.centroid_lon], 16);

                // Если есть геометрия - рисуем полигон
                if (data.geometry_json && data.geometry_json.type === 'Polygon') {{
                    var coords = data.geometry_json.coordinates[0].map(c => [c[1], c[0]]);
                    L.polygon(coords, {{color: 'red', weight: 2}}).addTo(map)
                        .bindPopup('<b>' + (data.name || 'Объект') + '</b><br>' + (data.address || ''));
                }} else {{
                    // Иначе просто маркер
                    L.marker([data.centroid_lat, data.centroid_lon]).addTo(map)
                        .bindPopup('<b>' + (data.name || 'Объект') + '</b><br>' + (data.address || ''))
                        .openPopup();
                }}
            }}
        }};

        var lots = {json.dumps(lots_with_geo, ensure_ascii=False)};
        var markers = L.markerClusterGroup({{ chunkedLoading: true, maxClusterRadius: 50 }});

        lots.forEach(function(lot) {{
            var marker = L.marker([lot.lat, lot.lng])
                .bindPopup('<div class="popup-title">' + lot.title + '</div>' +
                           '<div class="popup-price">Цена: ' + lot.price + '</div>' +
                           '<div style="font-size: 11px; color: #7f8c8d; margin-top: 5px;">' + lot.address + '</div>' +
                           (lot.cadastral ? '<div style="font-size: 10px; color: #95a5a6; margin-top: 3px;">КН: ' + lot.cadastral + '</div>' : ''));
            markers.addLayer(marker);
        }});

        map.addLayer(markers);
        if (lots.length > 0) {{
            map.fitBounds(markers.getBounds(), {{ padding: [30, 30] }});
        }}
    </script>
</body>
</html>'''
        self.web_view.setHtml(html, QUrl("https://unpkg.com"))
        self.status_bar.showMessage("Карта обновлена", 3000)

    def toggle_cadastral_layer(self):
        visible = self.cad_layer_checkbox.isChecked()
        js = f"window.setCadLayerVisible({str(visible).lower()});"
        self.web_view.page().runJavaScript(js)

    def search_cadastral_object(self):
        cadastral_number = self.cadastral_search_input.text().strip()
        if not cadastral_number:
            QMessageBox.warning(self, "Ошибка", "Введите кадастровый номер")
            return

        self.cadastral_result_text.setPlainText("Поиск...")
        self.status_bar.showMessage(f"Поиск объекта {cadastral_number}...")

        from bankrotai.geo import CADASTRAL_GEOCODER
        result = CADASTRAL_GEOCODER.search(cadastral_number)

        if not result:
            self.cadastral_result_text.setPlainText(f"❌ Объект не найден\n\nКадастровый номер: {cadastral_number}\n\nВозможные причины:\n- Неверный формат номера\n- Объект не зарегистрирован в ЕГРН\n- Ошибка связи с сервером Росреестра")
            self.status_bar.showMessage("Объект не найден", 3000)
            return

        # Формируем текст результата
        info_lines = [
            f"✅ Объект найден",
            f"",
            f"Запрос: {cadastral_number}",
            f"Источник: {result.get('source', 'pkk')}",
            f"Уверенность: {result.get('confidence', 'unknown')}",
            f"",
            f"📍 ОСНОВНАЯ ИНФОРМАЦИЯ",
            f"Объект: {result.get('object_type') or '—'}",
            f"Кадастровый номер: {result.get('cadastral_number') or '—'}",
            f"Название: {result.get('name') or '—'}",
            f"Адрес: {result.get('address') or '—'}",
            f"",
        ]

        if result.get('centroid_lat') and result.get('centroid_lon'):
            info_lines.append(f"Координаты: {result['centroid_lat']:.6f}, {result['centroid_lon']:.6f}")
        else:
            info_lines.append("Координаты: не найдены")

        if result.get('geometry_json'):
            info_lines.append("Границы: есть координаты границ")
        else:
            info_lines.append("Границы: без координат границ")

        info_lines.append("")
        info_lines.append("📊 ДЕТАЛЬНАЯ ИНФОРМАЦИЯ")

        # Дополнительные поля
        fields = {
            "Назначение": result.get('purpose'),
            "Статус": result.get('status'),
            "Площадь": f"{result.get('area_value')} {result.get('area_unit', '')}" if result.get('area_value') else None,
            "Кадастровая стоимость": f"{result.get('cad_cost')} руб." if result.get('cad_cost') else None,
            "Этажность": result.get('floors'),
            "Год постройки": result.get('year_built'),
            "Год ввода в эксплуатацию": result.get('year_commissioning'),
            "Кадастровый квартал": result.get('cadastral_quarter'),
            "Дата создания записи": result.get('date_create'),
            "Дата оценки стоимости": result.get('date_cost'),
            "ОКН": "Да" if result.get('cultural_heritage') else None,
        }

        for label, value in fields.items():
            if value:
                info_lines.append(f"{label}: {value}")
            else:
                info_lines.append(f"{label}: —")

        self.cadastral_result_text.setPlainText("\n".join(info_lines))
        self.status_bar.showMessage("Объект найден", 3000)

        # Показываем на карте
        js = f"window.showCadastreObject({json.dumps(result, ensure_ascii=False)});"
        self.web_view.page().runJavaScript(js)

    def update_map(self):
        with session_scope() as session:
            latest_geo = (
                select(
                    LotGeoSnapshot.lot_id,
                    func.max(LotGeoSnapshot.id).label("geo_id"),
                )
                .where(
                    LotGeoSnapshot.centroid_lat.isnot(None),
                    LotGeoSnapshot.centroid_lon.isnot(None),
                )
                .group_by(LotGeoSnapshot.lot_id)
                .subquery()
            )
            stmt = (
                select(ProcessedLot, LotGeoSnapshot)
                .join(latest_geo, latest_geo.c.lot_id == ProcessedLot.id)
                .join(LotGeoSnapshot, LotGeoSnapshot.id == latest_geo.c.geo_id)
                .limit(2000)
            )

            lots = []
            for lot, geo in session.execute(stmt):
                lots.append(
                    {
                        "id": lot.id,
                        "title": lot.title,
                        "price": float(lot.current_price) if lot.current_price else None,
                        "market_price": float(lot.market_price) if lot.market_price else None,
                        "discount": lot.discount_percent,
                        "risk": lot.risk_score,
                        "rating": lot.rating,
                        "address": lot.address,
                        "cadastral_number": lot.cadastral_number,
                        "category": lot.category,
                        "status": lot.auction_status,
                        "lat": geo.centroid_lat,
                        "lon": geo.centroid_lon,
                        "geo_source": geo.geo_source,
                        "geo_confidence": geo.geo_confidence,
                        "geometry": geo.geometry_json,
                        "metadata": geo.metadata_json,
                        "url": lot.lot_url,
                    }
                )

        html = self.build_map_html(lots)
        self.map_view.setHtml(html, QUrl("https://local.bankrotai/"))
        self.status_bar.showMessage("Карта обновлена", 3000)

    def update_yandex_map(self):
        with session_scope() as session:
            latest_geo = (
                select(
                    LotGeoSnapshot.lot_id,
                    func.max(LotGeoSnapshot.id).label("geo_id"),
                )
                .where(
                    LotGeoSnapshot.centroid_lat.isnot(None),
                    LotGeoSnapshot.centroid_lon.isnot(None),
                )
                .group_by(LotGeoSnapshot.lot_id)
                .subquery()
            )
            stmt = (
                select(ProcessedLot, LotGeoSnapshot)
                .join(latest_geo, latest_geo.c.lot_id == ProcessedLot.id)
                .join(LotGeoSnapshot, LotGeoSnapshot.id == latest_geo.c.geo_id)
                .limit(2000)
            )

            lots = []
            for lot, geo in session.execute(stmt):
                lots.append(
                    {
                        "id": lot.id,
                        "title": lot.title,
                        "price": float(lot.current_price) if lot.current_price else None,
                        "market_price": float(lot.market_price) if lot.market_price else None,
                        "discount": lot.discount_percent,
                        "risk": lot.risk_score,
                        "rating": lot.rating,
                        "address": lot.address,
                        "cadastral_number": lot.cadastral_number,
                        "category": lot.category,
                        "status": lot.auction_status,
                        "lat": geo.centroid_lat,
                        "lon": geo.centroid_lon,
                        "geo_source": geo.geo_source,
                        "geo_confidence": geo.geo_confidence,
                        "geometry": geo.geometry_json,
                        "metadata": geo.metadata_json,
                        "url": lot.lot_url,
                    }
                )

        self.yandex_map_view.setHtml(self.build_yandex_map_html(lots), QUrl("https://local.bankrotai/"))
        self.status_bar.showMessage("Яндекс-карта обновлена", 3000)

    def get_map_icon_urls(self) -> dict[str, str]:
        global _MAP_ICON_DATA_URL_CACHE
        if _MAP_ICON_DATA_URL_CACHE is not None:
            return dict(_MAP_ICON_DATA_URL_CACHE)

        candidates = [
            Path.cwd() / "image",
            Path(__file__).resolve().parents[2] / "image",
        ]
        if getattr(sys, "frozen", False):
            exe_path = Path(sys.executable).resolve()
            candidates.extend([
                exe_path.parent / "image",
                exe_path.parent.parent / "image",
                Path(getattr(sys, "_MEIPASS", exe_path.parent)) / "image",
            ])
        image_dir = next((path for path in candidates if path.exists()), candidates[0])
        urls: dict[str, str] = {}
        for key, filename in MAP_ICON_FILENAMES.items():
            path = image_dir / filename
            if not path.exists():
                urls[key] = ""
                logger.warning("Map icon is missing: %s", path)
                continue
            image = QImage(str(path))
            if image.isNull():
                urls[key] = ""
                logger.warning("Map icon cannot be read: %s", path)
                continue
            scaled = image.scaled(68, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            scaled.save(buffer, "PNG")
            encoded = base64.b64encode(bytes(buffer.data())).decode("ascii")
            urls[key] = f"data:image/png;base64,{encoded}"
        _MAP_ICON_DATA_URL_CACHE = dict(urls)
        return urls

    def build_yandex_map_html(self, lots: list[dict]) -> str:
        lots_json = json.dumps(lots, ensure_ascii=False)
        icon_urls_json = json.dumps(self.get_map_icon_urls(), ensure_ascii=False)
        return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>BankrotAI Yandex Map</title>
<script src="https://api-maps.yandex.ru/2.1/?lang=ru_RU" type="text/javascript"></script>
<style>
html, body, #map {{
    width: 100%;
    height: 100%;
    margin: 0;
    padding: 0;
}}
.hint {{
    position: absolute;
    z-index: 10;
    left: 12px;
    top: 12px;
    background: rgba(255,255,255,.94);
    border: 1px solid #d8dde6;
    border-radius: 6px;
    padding: 8px 10px;
    font: 13px Arial, sans-serif;
    color: #2e3c54;
}}
</style>
</head>
<body>
<div id="map"></div>
<div id="hint" class="hint">Загрузка Яндекс.Карт...</div>
<script>
const lots = {lots_json};
const iconUrls = {icon_urls_json};
let map;
let lotCollection;
let boundaryCollection;
let selectedObjectCollection;
let boundaryVisible = true;

function escapeHtml(text) {{
    if (text === null || text === undefined) return '';
    return String(text)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}}

function formatPrice(value) {{
    if (!value) return '—';
    return new Intl.NumberFormat('ru-RU').format(value) + ' ₽';
}}

function iconKey(lot) {{
    const category = String(lot.category || '').toLowerCase();
    const title = String(lot.title || '').toLowerCase();
    if (title.includes('аренд') || category.includes('lease') || category.includes('rent')) return 'rent';
    if (['car', 'vehicle', 'transport'].includes(category)) return 'auto';
    if (category === 'land') return 'land';
    if ([
        'apartment', 'house', 'commercial', 'commercial_room', 'commercial_building',
        'commercial_building_with_land', 'real_estate', 'parking', 'unfinished', 'complex',
        'office', 'retail'
    ].includes(category)) return 'realEstate';
    return 'other';
}}

function yandexIconOptions(lot) {{
    const url = iconUrls[iconKey(lot)] || iconUrls.other;
    if (!url) return {{ preset: 'islands#blueCircleDotIcon' }};
    return {{
        iconLayout: 'default#image',
        iconImageHref: url,
        iconImageSize: [34, 44],
        iconImageOffset: [-17, -44]
    }};
}}

function geoJsonCoordinatesToYandex(coords) {{
    if (!Array.isArray(coords)) return coords;
    if (coords.length === 2 && typeof coords[0] === 'number' && typeof coords[1] === 'number') {{
        return [coords[1], coords[0]];
    }}
    return coords.map(geoJsonCoordinatesToYandex);
}}

function addGeometry(geometry, options) {{
    if (!geometry || !geometry.type || !geometry.coordinates) return;
    if (geometry.type === 'Polygon') {{
        const polygon = new ymaps.Polygon(
            geoJsonCoordinatesToYandex(geometry.coordinates),
            {{}},
            options
        );
        boundaryCollection.add(polygon);
    }} else if (geometry.type === 'MultiPolygon') {{
        geometry.coordinates.forEach(poly => {{
            const polygon = new ymaps.Polygon(
                geoJsonCoordinatesToYandex(poly),
                {{}},
                options
            );
            boundaryCollection.add(polygon);
        }});
    }}
}}

function addLots() {{
    lotCollection.removeAll();
    boundaryCollection.removeAll();

    lots.forEach(lot => {{
        if (!lot.lat || !lot.lon) return;
        const placemark = new ymaps.Placemark(
            [lot.lat, lot.lon],
            {{
                balloonContentHeader: escapeHtml(lot.title),
                balloonContentBody:
                    '<b>Цена:</b> ' + formatPrice(lot.price) + '<br>' +
                    '<b>Рынок:</b> ' + formatPrice(lot.market_price) + '<br>' +
                    '<b>Дисконт:</b> ' + (lot.discount ?? '—') + '%<br>' +
                    '<b>Риск:</b> ' + (lot.risk ?? '—') + '<br>' +
                    '<b>Рейтинг:</b> ' + (lot.rating ?? '—') + '<br>' +
                    '<b>Кадастр:</b> ' + escapeHtml(lot.cadastral_number || '—') + '<br>' +
                    '<b>Адрес:</b> ' + escapeHtml(lot.address || '—')
            }},
            yandexIconOptions(lot)
        );
        lotCollection.add(placemark);
        if (lot.geometry) {{
            addGeometry(lot.geometry, {{
                strokeColor: '#2468d8',
                strokeWidth: 2,
                fillColor: 'rgba(36,104,216,0.08)'
            }});
        }}
    }});

    map.geoObjects.add(lotCollection);
    map.geoObjects.add(boundaryCollection);
    if (lotCollection.getLength() > 0) {{
        map.setBounds(lotCollection.getBounds(), {{ checkZoomRange: true, zoomMargin: 35 }});
    }}
}}

function showCadastreObject(data) {{
    selectedObjectCollection.removeAll();
    const body =
        '<b>Кадастр:</b> ' + escapeHtml(data.cadastral_number || '—') + '<br>' +
        '<b>Название:</b> ' + escapeHtml(data.title || '—') + '<br>' +
        '<b>Адрес:</b> ' + escapeHtml(data.address || '—') + '<br>' +
        '<b>Границы:</b> ' + (data.has_boundary ? 'получены' : 'без координат границ') + '<br>' +
        '<b>Источник:</b> ' + escapeHtml(data.source || '—');

    if (data.geometry) {{
        const beforeCount = selectedObjectCollection.getLength();
        const options = {{
            strokeColor: '#d92323',
            strokeWidth: 4,
            fillColor: 'rgba(217,35,35,0.16)'
        }};
        const oldBoundary = boundaryCollection;
        boundaryCollection = selectedObjectCollection;
        addGeometry(data.geometry, options);
        boundaryCollection = oldBoundary;
        if (selectedObjectCollection.getLength() > beforeCount) {{
            map.geoObjects.add(selectedObjectCollection);
            map.setBounds(selectedObjectCollection.getBounds(), {{ checkZoomRange: true, zoomMargin: 45 }});
            return;
        }}
    }}

    if (data.lat && data.lon) {{
        const marker = new ymaps.Placemark(
            [data.lat, data.lon],
            {{
                balloonContentHeader: escapeHtml(data.object_type || 'Объект'),
                balloonContentBody: body
            }},
            yandexIconOptions({{ category: 'real_estate' }})
        );
        selectedObjectCollection.add(marker);
        map.geoObjects.add(selectedObjectCollection);
        map.setCenter([data.lat, data.lon], 17);
        marker.balloon.open();
    }}
}}

function setCadLayerVisible(visible) {{
    boundaryVisible = visible;
    if (visible) {{
        map.geoObjects.add(boundaryCollection);
    }} else {{
        map.geoObjects.remove(boundaryCollection);
    }}
}}

window.showCadastreObject = showCadastreObject;
window.setCadLayerVisible = setCadLayerVisible;

ymaps.ready(function () {{
    map = new ymaps.Map('map', {{
        center: [57.6261, 39.8845],
        zoom: 8,
        controls: ['zoomControl', 'typeSelector', 'fullscreenControl']
    }});
    lotCollection = new ymaps.GeoObjectCollection();
    boundaryCollection = new ymaps.GeoObjectCollection();
    selectedObjectCollection = new ymaps.GeoObjectCollection();
    document.getElementById('hint').style.display = 'none';
    addLots();
}});
</script>
</body>
</html>
"""


    def build_map_html(self, lots: list[dict]) -> str:
        lots_json = json.dumps(lots, ensure_ascii=False)
        icon_urls_json = json.dumps(self.get_map_icon_urls(), ensure_ascii=False)
        wms_base_url = f"http://127.0.0.1:{self.cadastral_wms_proxy_port or 0}/nspd"

        return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>BankrotAI Map</title>

<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css">
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css">

<style>
html, body, #map {{
    height: 100%;
    width: 100%;
    margin: 0;
    padding: 0;
}}

.popup-title {{
    font-weight: bold;
    margin-bottom: 6px;
}}
</style>
</head>

<body>
<div id="map"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>

<script>
const lots = {lots_json};
const iconUrls = {icon_urls_json};

const map = L.map('map').setView([57.6261, 39.8845], 8);

const osm = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
}}).addTo(map);

const cadastralLandLayer = L.tileLayer.wms(
    '{wms_base_url}/land',
    {{
        layers: '36048',
        version: '1.3.0',
        format: 'image/png',
        transparent: true,
        opacity: 0.65,
        attribution: 'НСПД / Росреестр'
    }}
);

const cadastralBuildingsLayer = L.tileLayer.wms(
    '{wms_base_url}/buildings',
    {{
        layers: '36328',
        version: '1.3.0',
        format: 'image/png',
        transparent: true,
        opacity: 0.65,
        attribution: 'НСПД / Росреестр'
    }}
);

const markers = L.markerClusterGroup();
const boundaries = L.layerGroup().addTo(map);

let selectedObjectLayer = null;
let selectedObjectMarker = null;

function escapeHtml(text) {{
    if (text === null || text === undefined) return '';
    return String(text)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}}

function formatPrice(value) {{
    if (!value) return '—';
    return new Intl.NumberFormat('ru-RU').format(value) + ' ₽';
}}

function iconKey(lot) {{
    const category = String(lot.category || '').toLowerCase();
    const title = String(lot.title || '').toLowerCase();
    if (title.includes('аренд') || category.includes('lease') || category.includes('rent')) return 'rent';
    if (['car', 'vehicle', 'transport'].includes(category)) return 'auto';
    if (category === 'land') return 'land';
    if ([
        'apartment', 'house', 'commercial', 'commercial_room', 'commercial_building',
        'commercial_building_with_land', 'real_estate', 'parking', 'unfinished', 'complex',
        'office', 'retail'
    ].includes(category)) return 'realEstate';
    return 'other';
}}

function makeIcon(lot) {{
    const url = iconUrls[iconKey(lot)] || iconUrls.other;
    if (url) {{
        return L.icon({{
            iconUrl: url,
            iconSize: [34, 44],
            iconAnchor: [17, 44],
            popupAnchor: [0, -42]
        }});
    }}
    return L.divIcon({{ className: '', html: '<div style="width:14px;height:14px;border-radius:50%;background:#2468d8;border:2px solid white;box-shadow:0 0 4px rgba(0,0,0,.45);"></div>', iconSize: [18, 18], iconAnchor: [9, 9] }});
}}

function addLots() {{
    markers.clearLayers();
    boundaries.clearLayers();

    lots.forEach(lot => {{
        if (!lot.lat || !lot.lon) return;

        const marker = L.marker([lot.lat, lot.lon], {{
            icon: makeIcon(lot)
        }});

        const popup = `
            <div class="popup-title">${{escapeHtml(lot.title)}}</div>
            <div><b>Цена:</b> ${{formatPrice(lot.price)}}</div>
            <div><b>Рынок:</b> ${{formatPrice(lot.market_price)}}</div>
            <div><b>Дисконт:</b> ${{lot.discount ?? '—'}}%</div>
            <div><b>Риск:</b> ${{lot.risk ?? '—'}}</div>
            <div><b>Рейтинг:</b> ${{lot.rating ?? '—'}}</div>
            <div><b>Кадастр:</b> ${{escapeHtml(lot.cadastral_number || '—')}}</div>
            <div><b>Адрес:</b> ${{escapeHtml(lot.address || '—')}}</div>
        `;

        marker.bindPopup(popup);
        markers.addLayer(marker);

        if (lot.geometry) {{
            L.geoJSON(lot.geometry, {{
                style: {{
                    weight: 1,
                    opacity: 0.5,
                    fillOpacity: 0.05
                }}
            }}).addTo(boundaries);
        }}
    }});

    map.addLayer(markers);

    if (markers.getLayers().length > 0) {{
        map.fitBounds(markers.getBounds(), {{ padding: [30, 30] }});
    }}
}}

function showCadastreObject(data) {{
    if (selectedObjectLayer) {{
        map.removeLayer(selectedObjectLayer);
        selectedObjectLayer = null;
    }}

    if (selectedObjectMarker) {{
        map.removeLayer(selectedObjectMarker);
        selectedObjectMarker = null;
    }}

    const popup = `
        <div class="popup-title">${{escapeHtml(data.object_type || 'Объект')}}</div>
        <div><b>Кадастр:</b> ${{escapeHtml(data.cadastral_number || '—')}}</div>
        <div><b>Название:</b> ${{escapeHtml(data.title || '—')}}</div>
        <div><b>Адрес:</b> ${{escapeHtml(data.address || '—')}}</div>
        <div><b>Границы:</b> ${{data.has_boundary ? 'получены' : 'без координат границ'}}</div>
        <div><b>Источник:</b> ${{escapeHtml(data.source || '—')}}</div>
    `;

    if (data.geometry) {{
        selectedObjectLayer = L.geoJSON(data.geometry, {{
            style: {{
                weight: 4,
                opacity: 1,
                fillOpacity: 0.18
            }}
        }}).addTo(map);

        selectedObjectLayer.bindPopup(popup).openPopup();
        map.fitBounds(selectedObjectLayer.getBounds(), {{ padding: [40, 40] }});
        return;
    }}

    if (data.lat && data.lon) {{
        selectedObjectMarker = L.marker([data.lat, data.lon], {{
            icon: makeIcon({{ category: 'real_estate' }})
        }}).addTo(map);
        selectedObjectMarker.bindPopup(popup).openPopup();
        map.setView([data.lat, data.lon], 17);
    }}
}}

function setCadLayerVisible(visible) {{
    if (visible) {{
        if (!map.hasLayer(cadastralLandLayer)) map.addLayer(cadastralLandLayer);
        if (!map.hasLayer(cadastralBuildingsLayer)) map.addLayer(cadastralBuildingsLayer);
    }} else {{
        if (map.hasLayer(cadastralLandLayer)) map.removeLayer(cadastralLandLayer);
        if (map.hasLayer(cadastralBuildingsLayer)) map.removeLayer(cadastralBuildingsLayer);
    }}
}}

window.showCadastreObject = showCadastreObject;
window.setCadLayerVisible = setCadLayerVisible;

addLots();
</script>
</body>
</html>
"""

    def search_cadastre_from_gui(self):
        query = self.normalize_cadastre_query(self.cad_search_input.text().strip())
        if not query:
            QMessageBox.warning(self, "Поиск", "Введите кадастровый номер или адрес")
            return

        self.cad_search_btn.setEnabled(False)
        self.cad_search_btn.setText("Ищу...")
        self.cad_info_text.setPlainText("Ищу объект...")
        self.status_bar.showMessage(f"Кадастровый поиск: {query}")

        self.cadastre_search_worker = CadastreSearchWorker(query)
        self.cadastre_search_worker.finished.connect(self.on_cadastre_search_finished)
        self.cadastre_search_worker.error.connect(self.on_cadastre_search_error)
        self.cadastre_search_worker.start()

    def normalize_cadastre_query(self, query: str) -> str:
        if not query:
            return ""
        if re.match(r"^\d{2}:\d{2}:\d{6,7}:\d+$", query):
            return query
        lower = query.lower()
        known_place = any(
            place in lower
            for place in ("ярослав", "рыбинск", "архангельск", "москва", "санкт-петербург", "ленинградская")
        )
        if known_place:
            return query
        return f"Ярославль, {query}"

    def update_cadastre_address_suggestions(self, text: str):
        q = text.strip()
        if not q or re.match(r"^\d{2}:\d{2}:\d{0,7}:?\d*$", q):
            self.cad_suggestion_model.setStringList([])
            return

        lower = q.lower()
        if any(place in lower for place in ("ярослав", "рыбинск", "архангельск", "москва", "санкт-петербург")):
            suggestions = [q]
        else:
            suggestions = [
                f"Ярославль, {q}",
                f"Ярославская область, Ярославль, {q}",
                f"Рыбинск, {q}",
            ]
        self.cad_suggestion_model.setStringList(suggestions)

    def on_cadastre_search_finished(self, result):
        self.cad_search_btn.setEnabled(True)
        self.cad_search_btn.setText("Найти объект")
        self.status_bar.showMessage("Кадастровый поиск завершен", 3000)
        self.show_cadastre_info(result)
        self.show_cadastre_object_on_map(result)

    def on_cadastre_search_error(self, error_msg: str):
        self.cad_search_btn.setEnabled(True)
        self.cad_search_btn.setText("Найти объект")
        self.status_bar.showMessage("Ошибка кадастрового поиска", 5000)
        self.cad_info_text.setPlainText(f"Ошибка поиска: {error_msg}")

    def show_cadastre_info(self, result):
        if result.error:
            self.cad_info_text.setPlainText(
                f"Ошибка: {result.error}\n\n"
                f"Запрос: {result.query}"
            )
            return

        lines = []

        lines.append(f"Запрос: {result.query}")
        lines.append(f"Источник: {result.source}")
        lines.append(f"Уверенность: {result.confidence}")
        lines.append("")

        lines.append(f"Объект: {result.object_type or '—'}")
        lines.append(f"Кадастровый номер: {result.cadastral_number or '—'}")
        lines.append(f"Название: {result.title or '—'}")
        lines.append(f"Адрес: {result.address or '—'}")
        lines.append("")

        if result.lat and result.lon:
            lines.append(f"Координаты: {result.lat:.6f}, {result.lon:.6f}")
        else:
            lines.append("Координаты: —")

        lines.append("Границы: получены" if result.has_boundary else "Границы: без координат границ")
        lines.append("")
        lines.append("Информация:")

        for key, value in (result.info or {}).items():
            lines.append(f"{key}: {value if value not in (None, '') else '—'}")

        self.cad_info_text.setPlainText("\n".join(lines))

    def show_cadastre_object_on_map(self, result):
        payload = {
            "query": result.query,
            "cadastral_number": result.cadastral_number,
            "object_type": result.object_type,
            "title": result.title,
            "address": result.address,
            "lat": result.lat,
            "lon": result.lon,
            "geometry": result.geometry_json,
            "has_boundary": result.has_boundary,
            "source": result.source,
            "confidence": result.confidence,
            "info": result.info,
        }

        js = f"window.showCadastreObject({json.dumps(payload, ensure_ascii=False)});"
        self.map_view.page().runJavaScript(js)

    def toggle_cad_layer_from_gui(self):
        visible = self.cad_layer_checkbox.isChecked()
        js = f"window.setCadLayerVisible({str(visible).lower()});"
        self.map_view.page().runJavaScript(js)

    def search_yandex_cadastre_from_gui(self):
        query = self.normalize_cadastre_query(self.yandex_cad_search_input.text().strip())
        if not query:
            QMessageBox.warning(self, "Поиск", "Введите кадастровый номер или адрес")
            return

        self.yandex_cad_search_btn.setEnabled(False)
        self.yandex_cad_search_btn.setText("Ищу...")
        self.yandex_cad_info_text.setPlainText("Ищу объект...")
        self.status_bar.showMessage(f"Кадастровый поиск: {query}")

        self.yandex_cadastre_search_worker = CadastreSearchWorker(query)
        self.yandex_cadastre_search_worker.finished.connect(self.on_yandex_cadastre_search_finished)
        self.yandex_cadastre_search_worker.error.connect(self.on_yandex_cadastre_search_error)
        self.yandex_cadastre_search_worker.start()

    def update_yandex_cadastre_address_suggestions(self, text: str):
        q = text.strip()
        if not q or re.match(r"^\d{2}:\d{2}:\d{0,7}:?\d*$", q):
            self.yandex_cad_suggestion_model.setStringList([])
            return

        lower = q.lower()
        if any(place in lower for place in ("ярослав", "рыбинск", "архангельск", "москва", "санкт-петербург")):
            suggestions = [q]
        else:
            suggestions = [
                f"Ярославль, {q}",
                f"Ярославская область, Ярославль, {q}",
                f"Рыбинск, {q}",
            ]
        self.yandex_cad_suggestion_model.setStringList(suggestions)

    def on_yandex_cadastre_search_finished(self, result):
        self.yandex_cad_search_btn.setEnabled(True)
        self.yandex_cad_search_btn.setText("Найти объект")
        self.status_bar.showMessage("Кадастровый поиск завершен", 3000)
        self.show_yandex_cadastre_info(result)
        self.show_yandex_cadastre_object_on_map(result)

    def on_yandex_cadastre_search_error(self, error_msg: str):
        self.yandex_cad_search_btn.setEnabled(True)
        self.yandex_cad_search_btn.setText("Найти объект")
        self.status_bar.showMessage("Ошибка кадастрового поиска", 5000)
        self.yandex_cad_info_text.setPlainText(f"Ошибка поиска: {error_msg}")

    def show_yandex_cadastre_info(self, result):
        if result.error:
            self.yandex_cad_info_text.setPlainText(
                f"Ошибка: {result.error}\n\n"
                f"Запрос: {result.query}"
            )
            return

        lines = [
            f"Запрос: {result.query}",
            f"Источник: {result.source}",
            f"Уверенность: {result.confidence}",
            "",
            f"Объект: {result.object_type or '—'}",
            f"Кадастровый номер: {result.cadastral_number or '—'}",
            f"Название: {result.title or '—'}",
            f"Адрес: {result.address or '—'}",
            "",
        ]

        if result.lat and result.lon:
            lines.append(f"Координаты: {result.lat:.6f}, {result.lon:.6f}")
        else:
            lines.append("Координаты: —")

        lines.append("Границы: получены" if result.has_boundary else "Границы: без координат границ")
        lines.append("")
        lines.append("Информация:")
        for key, value in (result.info or {}).items():
            lines.append(f"{key}: {value if value not in (None, '') else '—'}")

        self.yandex_cad_info_text.setPlainText("\n".join(lines))

    def show_yandex_cadastre_object_on_map(self, result):
        payload = {
            "query": result.query,
            "cadastral_number": result.cadastral_number,
            "object_type": result.object_type,
            "title": result.title,
            "address": result.address,
            "lat": result.lat,
            "lon": result.lon,
            "geometry": result.geometry_json,
            "has_boundary": result.has_boundary,
            "source": result.source,
            "confidence": result.confidence,
            "info": result.info,
        }
        js = f"window.showCadastreObject({json.dumps(payload, ensure_ascii=False)});"
        self.yandex_map_view.page().runJavaScript(js)

    def toggle_yandex_cad_layer_from_gui(self):
        visible = self.yandex_cad_layer_checkbox.isChecked()
        js = f"window.setCadLayerVisible({str(visible).lower()});"
        self.yandex_map_view.page().runJavaScript(js)

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
