"""
ui/main_window.py – Główne okno aplikacji (dark mode).
"""
import copy
import json
import logging
from datetime import datetime
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLineEdit, QLabel,
    QStatusBar, QToolBar, QMessageBox, QMenu,
    QDateEdit, QFrame, QAbstractItemView, QApplication,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle
)
from PySide6.QtCore import Qt, QSize, QDate, Slot, QSettings, QTimer, QPoint, QUrl
from PySide6.QtGui import QAction, QKeySequence, QColor, QBrush, QDesktopServices

from config import APP_NAME, APP_VERSION, MAIN_WINDOW_MIN_WIDTH, MAIN_WINDOW_MIN_HEIGHT
from database.db_manager import DatabaseManager
from database.models import ServiceRecord

logger = logging.getLogger(__name__)

# (etykieta, atrybut/wirtualny, domyślnie widoczna)
ALL_COLUMNS = [
    ("ID",                       "id",                False),
    ("Data",                     "_datetime",         True),
    ("Firma",                    "company_name",      True),
    ("Flota",                    "fleet_name",        True),
    ("Nr rej.",                  "license_plate",     True),
    ("Nr boczny",                "side_number",       True),
    ("Marka pojazdu",            "vehicle_brand",     True),
    ("Typ pojazdu",              "vehicle_type",      False),
    ("Model urządzenia",         "device_model",      True),
    ("ID",                       "device_id",         True),
    ("SIM",                      "sim_number",        True),
    ("CCID",                     "_ccid",             True),
    ("Monter",                   "technician_name",   True),
    ("Typ",                      "record_type",       True),
    ("Urządzenia dodatkowe",     "_extras",           True),
    ("Komentarz do protokołu",   "comment",           True),
    ("Komentarz prywatny",       "_private_comment",  False),
    ("Przebieg",                 "mileage",           False),
    ("Gdzie rejestrator",        "recorder_location", False),
    ("Tacho/D8",                 "firmware_tacho",    False),
    ("CAN",                      "_can",              False),
    ("Czas dyżuru",              "duty_time_min",     False),
    ("Dyżur",                    "_duty_checked",     False),
    ("Odebrano",                 "_odebrane",         True),
    ("JSON",                     "_json",             True),
]

_DEFAULT_VISIBLE = frozenset(attr for _, attr, vis in ALL_COLUMNS if vis)
_ALL_ATTRS_ORDERED = [attr for _, attr, _ in ALL_COLUMNS]

_COL_WIDTHS = {
    "id": 50,             "_datetime": 135,
    "company_name": 200,  "fleet_name": 60,     "license_plate": 90,
    "side_number": 70,    "vehicle_brand": 140, "vehicle_type": 90,
    "device_model": 120,  "device_id": 130,     "sim_number": 140,
    "_ccid": 130,         "technician_name": 120, "record_type": 80,
    "_extras": 180,       "comment": 200,       "_private_comment": 180,
    "mileage": 70,        "recorder_location": 130, "firmware_tacho": 130,
    "_can": 50,           "duty_time_min": 75,
    "_duty_checked": 60,  "_odebrane": 70,      "_json": 80,
}

_CENTER_ATTRS = frozenset({
    "id", "_datetime", "record_type",
    "_can", "duty_time_min", "_json", "_odebrane", "_duty_checked",
})

# Kolumny kopiujące wartość po kliknięciu
_COPY_ATTRS = frozenset({"license_plate", "device_id", "sim_number", "_ccid", "_json"})


# ── Obejścia dla wymuszonych stylów (Dark Mode) i Zaznaczania ─────────────────

class RowColorDelegate(QStyledItemDelegate):
    """Delegat uodporniony na style systemowe, wymusza widoczność migania nawet na zaznaczeniu."""
    def __init__(self, parent=None, is_light=False):
        super().__init__(parent)
        self._is_light = is_light

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        
        bg = index.data(Qt.BackgroundRole)
        is_flashing = index.data(Qt.UserRole + 3)
        
        if is_flashing:
            # MAGIA: Jeśli komórka miga, kłamiemy systemowi, że NIE JEST zaznaczona ani najeżdżana.
            # Dzięki temu czysty zielony kolor przebije się nad niebieskim tłem zaznaczenia!
            opt.state &= ~QStyle.State_Selected
            opt.state &= ~QStyle.State_MouseOver
            if bg:
                painter.fillRect(opt.rect, QColor(bg))
            opt.backgroundBrush = QBrush(Qt.NoBrush)
            
        else:
            is_selected = opt.state & QStyle.State_Selected
            is_hover = opt.state & QStyle.State_MouseOver
            
            if self._is_light:
                if bg:
                    color = QColor(bg)
                    if is_hover: color = QColor("#bbf7d0")
                    if is_selected: color = QColor("#4ade80")
                else:
                    color = QColor("#ffffff")
                    if is_hover: color = QColor("#f1f5f9")
                    if is_selected: color = QColor("#e2e8f0")
            else:
                if bg:
                    color = QColor(bg)
                    if is_hover: color = QColor("#165c38")
                    if is_selected: color = QColor("#3aad6a")
                else:
                    color = QColor("#1a1d23")
                    if is_hover: color = QColor("#22262f")
                    if is_selected: color = QColor("#333847")
                    
            painter.fillRect(opt.rect, color)
            opt.backgroundBrush = QBrush(Qt.NoBrush)

        super().paint(painter, opt, index)


class CustomTableWidget(QTableWidget):
    """Niestandardowa tabela uodporniona na zaznaczanie i przeciąganie po kliknięciu w konkretne kolumny."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._drag_blocked = False

    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        restore_mode = None
        
        if item and item.data(Qt.UserRole + 1) == "no_select":
            self._drag_blocked = True
            restore_mode = self.selectionMode()
            self.setSelectionMode(QAbstractItemView.NoSelection)
        else:
            self._drag_blocked = False
        
        super().mousePressEvent(event)
        
        if restore_mode is not None:
            self.setSelectionMode(restore_mode)

    def mouseMoveEvent(self, event):
        # Blokujemy zaznaczanie przeciąganiem (drag), jeśli rozpoczęliśmy klik od zablokowanej komórki
        if self._drag_blocked:
            return
        super().mouseMoveEvent(event)


# ── Helpery ───────────────────────────────────────────────────────────────────

def _get_cell_value(rec: ServiceRecord, attr: str) -> str:
    if attr == "_datetime":
        if rec.service_date:
            return f"{rec.service_date} {rec.service_hour:02d}:{rec.service_minute:02d}"
        return ""
    if attr == "_ccid":
        return rec.config_json.get("additionalConfig", {}).get("ccid", "")
    if attr == "_extras":
        parts = []
        if rec.has_rfid:
            parts.append("RFID")
        if rec.has_immo:
            parts.append("IMMO")
        if rec.has_tablet:
            parts.append(f"Tablet ({rec.tablet_sn})" if rec.tablet_sn else "Tablet")
        if rec.has_power:
            parts.append("Zasilanie")
        din_cfg = rec.config_json.get("dinConfig", {})
        if din_cfg.get("din1", {}).get("nazwa", "").lower() == "webasto":
            parts.append("Webasto")
        for key in ("din2", "din3", "din4", "din5"):
            name = din_cfg.get(key, {}).get("nazwa", "")
            if name:
                parts.append(name)
        return ", ".join(parts)
    if attr == "_can":
        return "TAK" if rec.can_active else ""
    if attr == "_odebrane":
        return "TAK" if rec.config_json.get("odebrane") else ""
    if attr == "_duty_checked":
        return "Dyżur" if rec.config_json.get("dyzurZaznaczony") else ""
    if attr == "_private_comment":
        return rec.config_json.get("komentarzPrywatny", "")
    if attr == "_json":
        return _build_copy_json(rec)
    if attr == "record_type":
        val = getattr(rec, attr, "")
        return str(val).strip() if val else ""
    val = getattr(rec, attr, "")
    return str(val) if val is not None else ""


def _build_copy_json(rec: ServiceRecord) -> str:
    date_str = ""
    if rec.service_date:
        parts = rec.service_date.split("-")
        if len(parts) == 3:
            date_str = f"{parts[2]}.{parts[1]}.{parts[0]}"
        else:
            date_str = rec.service_date

    d8_val = ""
    model_tacho = ""
    wersja_tacho = ""
    if rec.firmware_tacho:
        parts = rec.firmware_tacho.split(" ", 1)
        model_tacho = parts[0]
        wersja_tacho = " ".join(parts[1:]) if len(parts) > 1 else ""
        from config import TACHO_BRANDS_TACHOREADER, TACHO_BRANDS_FMB640
        if model_tacho in TACHO_BRANDS_TACHOREADER:
            d8_val = "Tachoreader"
        elif model_tacho in TACHO_BRANDS_FMB640:
            d8_val = "FMB640/FMC650"

    add_cfg = rec.config_json.get("additionalConfig", {})
    
    marka_parts = rec.vehicle_brand.split(" ", 1) if rec.vehicle_brand else ["", ""]
    marka = marka_parts[0]
    model = " ".join(marka_parts[1:]) if len(marka_parts) > 1 else ""

    raw_sondy = rec.config_json.get("sondyRaw", {})
    def get_probe(key, db_val):
        if key in raw_sondy:
            return raw_sondy[key]
        if db_val is None or db_val == "":
            return ""
        try:
            f = float(db_val)
            return str(int(f)) if f.is_integer() else str(f)
        except (ValueError, TypeError):
            return str(db_val)

    full = {
        "data": date_str,
        "typ": rec.record_type.strip() if rec.record_type else "",
        "firma": rec.company_name,
        "flota": rec.fleet_name,
        "nrRejestracyjny": rec.license_plate,
        "nrBoczny": rec.side_number,
        "id": rec.device_id,
        "sim": rec.sim_number,
        "ccid": add_cfg.get("ccid", ""),
        "marka": marka,
        "model": model,
        "typPojazdu": rec.vehicle_type,
        "modelUrzadzenia": rec.device_model,
        "monter": rec.technician_name,
        "godzina": str(rec.service_hour or 0),
        "minuta": str(rec.service_minute or 0),
        "d8": d8_val,
        "modelTacho": model_tacho,
        "wersjaTacho": wersja_tacho,
        "gdzieRejestrator": rec.recorder_location,
        "przebieg": str(rec.mileage) if rec.mileage is not None else "",
        "an0Numer": rec.probe1_id,
        "an0Pojemnosc": get_probe("an0Pojemnosc", rec.probe1_capacity),
        "an0Skalowanie": get_probe("an0Skalowanie", rec.probe1_length),
        "an1Numer": rec.probe2_id,
        "an1Pojemnosc": get_probe("an1Pojemnosc", rec.probe2_capacity),
        "an1Skalowanie": get_probe("an1Skalowanie", rec.probe2_length),
        "prawyZbiornik": rec.right_tank_probe,
        "komentarzDoProtokolu": rec.comment,
        "komentarzPrywatny": rec.config_json.get("komentarzPrywatny", ""),
        "tablet": rec.has_tablet,
        "tabletNr": rec.tablet_sn,
        "zasilanie": rec.has_power,
        "rfid": rec.has_rfid,
        "immo": rec.has_immo,
        "canConfig": rec.config_json.get("canConfig", {}),
        "dinConfig": rec.config_json.get("dinConfig", {}),
        "odebrane": rec.config_json.get("odebrane", False),
        "dyzur": rec.config_json.get("dyzurZaznaczony", False),
        "czasDyzuru": rec.duty_time_min or "",
        "przekladkaZ": add_cfg.get("przekladkaRej", "")
    }
    return json.dumps(full, ensure_ascii=False, indent=2)


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self._db = DatabaseManager.instance()
        self._records: list[ServiceRecord] = []
        self._selected_record_id: Optional[int] = None
        self._column_order: list[str] = list(_ALL_ATTRS_ORDERED)
        self._visible_columns: set[str] = set(_DEFAULT_VISIBLE) | {"id"}
        self._active_columns: list[tuple[str, str]] = []
        self._json_col_index: int = -1
        self._loading: bool = False
        self._flash_timers: list = []
        self._open_forms: list = []
        self._is_light = self._db.get_setting("theme_mode", "dark") == "light"
        self._setup_ui()
        self._connect_signals()
        self._restore_settings()
        self._on_filter()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        self.setWindowTitle(f"{APP_NAME}  –  v{APP_VERSION}")
        self.setMinimumSize(MAIN_WINDOW_MIN_WIDTH, MAIN_WINDOW_MIN_HEIGHT)
        self.resize(1400, 820)

        if self._is_light:
            self.setStyleSheet("""
                QMainWindow { background-color: #f8fafc; }
                QWidget { color: #0f172a; }
                QStatusBar { background-color: #f1f5f9; color: #334155; border-top: 1px solid #cbd5e1; }
                QToolBar { background-color: #ffffff; border-bottom: 1px solid #cbd5e1; }
                QMenu { background-color: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; }
                QMenu::item:selected { background-color: #e2e8f0; }
            """)
        else:
            self.setStyleSheet("""
                QMainWindow { background-color: #0f1115; }
                QWidget { color: #e2e8f0; }
                QStatusBar { background-color: #15181e; color: #94a3b8; border-top: 1px solid #2e3340; }
                QToolBar { background-color: #1a1d23; border-bottom: 1px solid #2e3340; }
                QMenu { background-color: #1a1d23; color: #e2e8f0; border: 1px solid #3a4150; }
                QMenu::item:selected { background-color: #333847; }
            """)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._build_toolbar()

        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(12, 10, 12, 10)
        inner_lay.setSpacing(8)
        inner_lay.addWidget(self._build_filter_bar())
        self._table = self._build_table()
        inner_lay.addWidget(self._table, 1)
        root.addWidget(inner, 1)

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._lbl_count = QLabel("Rekordów: 0")
        self._status_bar.addPermanentWidget(self._lbl_count)
        self._status_bar.showMessage("Gotowy")

    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        tb.setIconSize(QSize(14, 14))
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        tb.setFixedHeight(42)
        self.addToolBar(tb)

        self._act_new = QAction("＋  Nowy montaż", self)
        self._act_new.setShortcut(QKeySequence.New)
        self._act_new.setToolTip("Dodaj nowy wpis  [Ctrl+N]")
        tb.addAction(self._act_new)

        tb.addSeparator()

        self._act_edit = QAction("✎  Edytuj", self)
        self._act_edit.setShortcut(QKeySequence("Ctrl+E"))
        self._act_edit.setEnabled(False)
        tb.addAction(self._act_edit)

        self._act_delete = QAction("✕  Usuń", self)
        self._act_delete.setShortcut(QKeySequence.Delete)
        self._act_delete.setEnabled(False)
        tb.addAction(self._act_delete)

        tb.addSeparator()

        self._act_refresh = QAction("⟳  Odśwież", self)
        self._act_refresh.setShortcut(QKeySequence.Refresh)
        tb.addAction(self._act_refresh)

        tb.addSeparator()

        self._act_settings = QAction("⚙  Ustawienia", self)
        self._act_settings.setToolTip("Ustawienia aplikacji  [Ctrl+K]")
        self._act_settings.setShortcut(QKeySequence("Ctrl+K"))
        tb.addAction(self._act_settings)

    def _build_filter_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("filter_bar")
        bar.setFixedHeight(84)
        
        bg_bar = "#ffffff" if self._is_light else "#22262f"
        border = "#cbd5e1" if self._is_light else "#2e3340"
        text = "#0f172a" if self._is_light else "#e2e8f0"
        bg_input = "#f8fafc" if self._is_light else "#2a2f3a"
        border_input = "#94a3b8" if self._is_light else "#3a4150"
        bg_hover = "#e2e8f0" if self._is_light else "#333847"
        lbl_color = "#334155" if self._is_light else "#cbd5e1"
        btn_text = "#0f172a" if self._is_light else "#e2e8f0"
        btn_disabled_bg = "#f1f5f9" if self._is_light else "#1e2229"
        btn_disabled_text = "#94a3b8" if self._is_light else "#3a4150"
        btn_disabled_border = "#cbd5e1" if self._is_light else "#252930"

        bar.setStyleSheet(f"""
            QWidget#filter_bar {{ background-color: {bg_bar}; border: 1px solid {border}; border-radius: 5px; }}
            QLabel {{ background: transparent; border: none; color: {lbl_color}; font-weight: bold; }}
            QLineEdit, QDateEdit, QPushButton {{
              min-height: 26px; max-height: 26px; font-size: 9pt;
              background: {bg_input}; color: {text}; border: 1px solid {border_input}; border-radius: 3px;
            }}
            QLineEdit, QDateEdit {{ padding: 0 6px; }}
            QPushButton {{ padding: 0 16px; color: {btn_text}; }}
            QPushButton:hover {{ background: {bg_hover}; border-color: #64748b; }}
            QPushButton:disabled {{ background: {btn_disabled_bg}; color: {btn_disabled_text}; border-color: {btn_disabled_border}; }}
        """)
        main_lay = QVBoxLayout(bar)
        main_lay.setContentsMargins(16, 8, 16, 8)
        main_lay.setSpacing(8)

        # -- Górny wiersz (Szybkie filtry) --
        q_lay = QHBoxLayout()
        q_lay.setSpacing(8)
        
        lbl_q = QLabel("SZYBKIE FILTRY:")
        lbl_fg = "#334155" if self._is_light else "#ffffff"
        lbl_q.setStyleSheet(
            f"color: {lbl_fg}; font-size: 8pt; font-weight: bold; "
            "letter-spacing: 1px; background: transparent; border: none;"
        )
        q_lay.addWidget(lbl_q)
        
        btn_q_style = f"""
            QPushButton {{
              min-height: 22px; max-height: 22px; font-size: 8.5pt;
              background: {bg_input}; color: {text}; border: 1px solid {border_input}; border-radius: 3px;
              padding: 0 10px;
            }}
            QPushButton:hover {{ background: {bg_hover}; border-color: #64748b; }}
        """

        self._btn_q_today       = QPushButton("Dzisiaj")
        self._btn_q_week        = QPushButton("Ten tydzień")
        self._btn_q_month       = QPushButton("Ostatni miesiąc")
        self._btn_q_prev_month  = QPushButton("Poprzedni miesiąc")
        self._btn_q_year        = QPushButton("Ten rok")
        self._btn_q_duty        = QPushButton("Tylko dyżur")
        self._btn_q_no_duty     = QPushButton("Bez dyżuru")

        for btn in (self._btn_q_today, self._btn_q_week, self._btn_q_month,
                    self._btn_q_prev_month, self._btn_q_year,
                    self._btn_q_duty, self._btn_q_no_duty):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(btn_q_style)
            q_lay.addWidget(btn)

        self._btn_q_today.clicked.connect(self._on_q_today)
        self._btn_q_week.clicked.connect(self._on_q_week)
        self._btn_q_month.clicked.connect(self._on_q_month)
        self._btn_q_prev_month.clicked.connect(self._on_q_prev_month)
        self._btn_q_year.clicked.connect(self._on_q_year)
        self._btn_q_duty.clicked.connect(lambda: self._on_q_duty(True))
        self._btn_q_no_duty.clicked.connect(lambda: self._on_q_duty(False))

        is_duty = self._db.get_setting("show_duty_section", "1") == "1"
        self._btn_q_duty.setVisible(is_duty)
        self._btn_q_no_duty.setVisible(is_duty)

        q_lay.addStretch()
        main_lay.addLayout(q_lay)

        # -- Dolny wiersz (Szukaj, Daty, Akcje) --
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignVCenter)
        lay.setSpacing(12)

        lbl = QLabel("SZUKAJ")
        lbl.setStyleSheet(lbl_q.styleSheet())
        lay.addWidget(lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"background: {border}; border: none; max-width: 1px; min-height: 26px;")
        lay.addWidget(sep)

        self._filter_search = QLineEdit()
        self._filter_search.setPlaceholderText("Wpisz tekst... (lub kolumna:wartość np. dyżur:)")
        self._filter_search.setToolTip(
            "Wpisz tekst, aby przeszukać wszystkie pola.\n"
            "Aby przeszukać konkretną kolumnę, wpisz jej nazwę i dwukropek:\n"
            " • 'firma:abc' -> Szuka 'abc' tylko w kolumnie Firma\n"
            " • 'dyżur:' -> Szuka wierszy, które mają pustą kolumnę Dyżur\n"
            " • 'czas:' -> Szuka pustych w kolumnie Czas dyżuru"
        )
        self._filter_search.setMinimumWidth(350)
        self._filter_search.setClearButtonEnabled(True)
        lay.addWidget(self._filter_search)

        lay.addWidget(QLabel("Od"))
        self._filter_date_from = QDateEdit()
        self._filter_date_from.setCalendarPopup(True)
        self._filter_date_from.setDate(QDate(2020, 1, 1))
        self._filter_date_from.setDisplayFormat("dd.MM.yyyy")
        self._filter_date_from.setFixedWidth(110)
        lay.addWidget(self._filter_date_from)

        lay.addWidget(QLabel("Do"))
        self._filter_date_to = QDateEdit()
        self._filter_date_to.setCalendarPopup(True)
        self._filter_date_to.setDate(QDate.currentDate())
        self._filter_date_to.setDisplayFormat("dd.MM.yyyy")
        self._filter_date_to.setFixedWidth(110)
        lay.addWidget(self._filter_date_to)

        lay.addStretch()

        self._btn_duplicate_row = QPushButton("⧉  Duplikuj")
        self._btn_duplicate_row.setCursor(Qt.PointingHandCursor)
        self._btn_duplicate_row.setEnabled(False)
        self._btn_duplicate_row.setToolTip("Duplikuj zaznaczony wiersz (tylko 1 wiersz)")
        lay.addWidget(self._btn_duplicate_row)

        self._btn_filter = QPushButton("Filtruj")
        self._btn_filter.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self._btn_filter)

        self._btn_clear = QPushButton("Wyczyść")
        self._btn_clear.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self._btn_clear)

        main_lay.addLayout(lay)
        return bar

    def _build_table(self) -> QTableWidget:
        table = CustomTableWidget()
        table.setItemDelegate(RowColorDelegate(table, is_light=self._is_light))
        
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        table.setAlternatingRowColors(False)
        table.setSortingEnabled(True)
        table.verticalHeader().setDefaultSectionSize(24)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(True)
        table.setContextMenuPolicy(Qt.CustomContextMenu)

        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        hdr.setMinimumSectionSize(30)

        self._rebuild_table_columns(table)
        return table

    def _rebuild_table_columns(self, table: QTableWidget = None):
        if table is None:
            table = self._table
            
        self._active_columns = [
            (lbl, attr)
            for attr in self._column_order
            for lbl, a, _ in ALL_COLUMNS
            if a == attr and attr in self._visible_columns
        ]
        
        table.setColumnCount(len(self._active_columns))
        table.setHorizontalHeaderLabels([c[0] for c in self._active_columns])

        if self._is_light:
            table.setStyleSheet("""
                QTableWidget { 
                    background-color: #ffffff; color: #0f172a; alternate-background-color: #f8fafc; 
                    gridline-color: #94a3b8; border: 1px solid #cbd5e1; 
                    selection-background-color: transparent; selection-color: #0f172a; 
                    outline: none;
                }
                QHeaderView::section { background-color: #f8fafc; color: #334155; border: 1px solid #cbd5e1; border-top: none; border-left: none; padding: 4px; font-weight: bold; }
                QTableCornerButton::section { background-color: #f8fafc; border: 1px solid #cbd5e1; border-top: none; border-left: none; }
            """)
        else:
            table.setStyleSheet("""
                QTableWidget { 
                    background-color: #1a1d23; color: #e2e8f0; gridline-color: #2e3340; border: 1px solid #3a4150; 
                    selection-background-color: transparent; selection-color: #ffffff;
                    outline: none;
                }
                QHeaderView::section { background-color: #22262f; color: #cbd5e1; border: 1px solid #3a4150; border-top: none; border-left: none; padding: 4px; }
                QTableCornerButton::section { background-color: #22262f; border: 1px solid #3a4150; border-top: none; border-left: none; }
            """)

        self._json_col_index = -1
        for i, (_, attr) in enumerate(self._active_columns):
            if attr == "id":
                table.setColumnWidth(i, _COL_WIDTHS.get(attr, 100))
                table.setColumnHidden(i, True)
            elif attr == "_json":
                table.setColumnWidth(i, _COL_WIDTHS.get(attr, 100))
                self._json_col_index = i
            else:
                table.setColumnWidth(i, _COL_WIDTHS.get(attr, 100))

    # ---------------------------------------------------------------- Settings

    def _restore_settings(self):
        settings = QSettings("TwojaFirma", "SystemOdbiory")

        _known_attrs = set(_ALL_ATTRS_ORDERED)

        saved_order = settings.value("column_order")
        if saved_order and isinstance(saved_order, list) and saved_order:
            saved_order = [a for a in saved_order if a in _known_attrs]
            for attr in _ALL_ATTRS_ORDERED:
                if attr not in set(saved_order):
                    saved_order.append(attr)
            self._column_order = saved_order

        saved_vis = settings.value("visible_columns")
        if saved_vis:
            if isinstance(saved_vis, list):
                self._visible_columns = (set(saved_vis) & _known_attrs) | {"id", "_odebrane"}
            elif isinstance(saved_vis, str) and saved_vis:
                self._visible_columns = (set(saved_vis.split(",")) & _known_attrs) | {"id", "_odebrane"}
            self._rebuild_table_columns()
        else:
            self._visible_columns.add("_odebrane")

        state = settings.value("table_header_state_v2")
        if state:
            hdr = self._table.horizontalHeader()
            hdr.restoreState(state)
            for i, (_, attr) in enumerate(self._active_columns):
                if attr == "id":
                    hdr.hideSection(i)
                else:
                    hdr.showSection(i)

        date_from = settings.value("filter_date_from")
        if date_from:
            d = QDate.fromString(date_from, "yyyy-MM-dd")
            if d.isValid():
                self._filter_date_from.setDate(d)
                
        date_to = settings.value("filter_date_to")
        if date_to:
            d = QDate.fromString(date_to, "yyyy-MM-dd")
            if d.isValid():
                self._filter_date_to.setDate(d)

    def closeEvent(self, event):
        settings = QSettings("TwojaFirma", "SystemOdbiory")
        settings.setValue("table_header_state_v2",
                          self._table.horizontalHeader().saveState())
        settings.setValue("visible_columns", list(self._visible_columns))
        settings.setValue("column_order", self._column_order)
        settings.setValue("filter_date_from", self._filter_date_from.date().toString("yyyy-MM-dd"))
        settings.setValue("filter_date_to", self._filter_date_to.date().toString("yyyy-MM-dd"))
        
        # --- Auto Backup ---
        try:
            import shutil
            import os
            from config import DB_PATH, BACKUP_DIR
            
            backup_filename = f"auto_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            backup_path = BACKUP_DIR / backup_filename
            shutil.copy2(DB_PATH, backup_path)
            
            # Utrzymuj tylko 10 najnowszych kopii automatycznych
            backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("auto_backup_")])
            while len(backups) > 10:
                oldest = backups.pop(0)
                try: os.remove(BACKUP_DIR / oldest)
                except OSError: pass
        except Exception as e:
            logger.error(f"Nie udało się utworzyć automatycznej kopii zapasowej: {e}")
            
        super().closeEvent(event)

    # --------------------------------------------------------------- Signals

    def _connect_signals(self):
        self._act_new.triggered.connect(self._on_new)
        self._act_edit.triggered.connect(self._on_edit)
        self._act_delete.triggered.connect(self._on_delete)
        self._act_refresh.triggered.connect(self.load_records)
        self._act_settings.triggered.connect(self._on_open_settings)

        self._btn_duplicate_row.clicked.connect(self._on_duplicate_row)
        self._btn_filter.clicked.connect(self._on_filter)
        self._btn_clear.clicked.connect(self._on_clear_filter)
        self._filter_search.returnPressed.connect(self._on_filter)

        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.doubleClicked.connect(self._on_double_clicked)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.itemChanged.connect(self._on_item_changed)

    # ---------------------------------------------------------------- Data

    @Slot()
    def load_records(self, filters: Optional[dict] = None):
        try:
            try:
                all_records = self._db.get_all_records(None)
            except TypeError:
                all_records = self._db.get_all_records()
        except Exception as exc:
            logger.error(f"Błąd pobierania: {exc}", exc_info=True)
            QMessageBox.warning(self, "Błąd", f"Nie można pobrać danych:\n{exc}")
            return

        if filters:
            filtered_records = []
            for r in all_records:
                if "date_from" in filters and r.service_date and r.service_date < filters["date_from"]:
                    continue
                if "date_to" in filters and r.service_date and r.service_date > filters["date_to"]:
                    continue
                if "smart_search" in filters:
                    search_terms = filters["smart_search"].split()
                    match_all = True
                    searchable_text = None

                    for term in search_terms:
                        if ":" in term and not term.startswith(":"):
                            col_hint, val_hint = term.split(":", 1)
                            
                            # Najpierw szukamy dokładnego dopasowania (aby "dyżur:" nie łapało "Czas dyżuru")
                            matched_attrs = [attr for lbl, attr, _ in ALL_COLUMNS if col_hint == lbl.lower()]
                            if not matched_attrs:
                                # Jeśli nie ma dokładnego, szukamy częściowego
                                matched_attrs = [attr for lbl, attr, _ in ALL_COLUMNS if col_hint in lbl.lower()]
                            
                            if matched_attrs:
                                term_matched = False
                                for attr in matched_attrs:
                                    cell_val = str(_get_cell_value(r, attr)).lower().strip()
                                    # Znajduje puste komórki LUB takie, które zawierają daną frazę
                                    if (val_hint == "" and cell_val == "") or (val_hint != "" and val_hint in cell_val):
                                        term_matched = True
                                        break
                                if not term_matched:
                                    match_all = False
                                    break
                                continue
                        
                        if searchable_text is None:
                            searchable_text = " ".join(str(_get_cell_value(r, attr)).lower() for attr in _ALL_ATTRS_ORDERED if attr != "_json")
                        if term not in searchable_text:
                            match_all = False
                            break
                            
                    if not match_all:
                        continue
                filtered_records.append(r)
            self._records = filtered_records
        else:
            self._records = all_records

        self._loading = True
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)

        for rec in self._records:
            row = self._table.rowCount()
            self._table.insertRow(row)

            odebrane = bool(rec.config_json.get("odebrane"))
            dyzur    = bool(rec.config_json.get("dyzurZaznaczony"))

            for col, (_, attr) in enumerate(self._active_columns):
                if attr == "_odebrane":
                    item = QTableWidgetItem()
                    item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    item.setCheckState(Qt.Checked if odebrane else Qt.Unchecked)
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setData(Qt.UserRole + 1, "no_select") 
                else:
                    val = _get_cell_value(rec, attr)

                    if attr == "_json":
                        item = QTableWidgetItem("📋") 
                        item.setData(Qt.UserRole, val)
                        item.setToolTip("Kliknij aby skopiować JSON do schowka")
                        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                        item.setData(Qt.UserRole + 1, "no_select")
                    elif attr in _COPY_ATTRS:
                        item = QTableWidgetItem(val)
                        item.setToolTip(f"Kliknij aby skopiować: {val}")
                        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                        item.setData(Qt.UserRole + 1, "no_select")
                    else:
                        item = QTableWidgetItem(val)
                        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

                item.setData(Qt.UserRole + 2, rec.id)
                
                if attr in _CENTER_ATTRS:
                    item.setTextAlignment(Qt.AlignCenter)

                self._table.setItem(row, col, item)

            if odebrane:
                self._apply_row_color(row, True)
            if dyzur:
                self._apply_dyzur_color(row, True)

        self._table.setSortingEnabled(True)
        self._loading = False
        n = len(self._records)
        self._lbl_count.setText(f"Rekordów: {n}")
        self._status_bar.showMessage(f"Załadowano {n} rekordów.", 3000)

    # --------------------------------------------------------------- Helpers

    def _get_record_id_for_row(self, row: int) -> Optional[int]:
        item = self._table.item(row, 0)
        if item:
            val = item.data(Qt.UserRole + 2)
            if isinstance(val, int):
                return val
                
        id_col = next((i for i, (_, a) in enumerate(self._active_columns) if a == "id"), None)
        if id_col is not None:
            item = self._table.item(row, id_col)
            if item and item.text().isdigit():
                return int(item.text())
        return None

    def _get_selected_rows(self) -> list[int]:
        return sorted(set(idx.row() for idx in self._table.selectionModel().selectedRows()))

    def _get_selected_row_ids(self) -> list[int]:
        return [
            rid for row in self._get_selected_rows() 
            if (rid := self._get_record_id_for_row(row)) is not None
        ]

    def _apply_row_color(self, row: int, checked: bool) -> None:
        mode = self._db.get_setting("odebrane_highlight_mode", "row")
        self._table.blockSignals(True)
        try:
            if not checked:
                for col in range(self._table.columnCount()):
                    item = self._table.item(row, col)
                    if item:
                        item.setData(Qt.BackgroundRole, None)
                        item.setData(Qt.ForegroundRole, None)
                return

            if mode == "none":
                return

            bg = QColor(self._db.get_setting(
                "color_odebrane_light" if self._is_light else "color_odebrane_dark",
                "#dcfce7" if self._is_light else "#1e6640",
            ))

            if mode == "cols" or mode == "firma":
                cols_str = self._db.get_setting("odebrane_highlight_cols", "company_name")
                target_attrs = set(x.strip() for x in cols_str.split(",") if x.strip())
                for col in range(self._table.columnCount()):
                    attr = self._active_columns[col][1]
                    if attr in target_attrs:
                        item = self._table.item(row, col)
                        if item:
                            item.setData(Qt.BackgroundRole, bg)
            else:  # "row"
                for col in range(self._table.columnCount()):
                    item = self._table.item(row, col)
                    if item:
                        item.setData(Qt.BackgroundRole, bg)
                        item.setData(Qt.ForegroundRole, None)
        finally:
            self._table.blockSignals(False)

    def _apply_dyzur_color(self, row: int, duty: bool) -> None:
        if self._db.get_setting("dyzur_highlight_enabled", "1") != "1":
            return
            
        cols_str = self._db.get_setting("dyzur_highlight_cols", "_datetime")
        target_attrs = set(x.strip() for x in cols_str.split(",") if x.strip())
        
        bg = QColor(self._db.get_setting(
            "color_dyzur_light" if self._is_light else "color_dyzur_dark",
            "#fef3c7" if self._is_light else "#78350f",
        ))

        for col in range(self._table.columnCount()):
            attr = self._active_columns[col][1]
            if attr in target_attrs:
                item = self._table.item(row, col)
                if item:
                    if duty:
                        item.setData(Qt.BackgroundRole, bg)
                    else:
                        item.setData(Qt.BackgroundRole, None)

    def _flash_cell(self, row: int, col: int):
        item = self._table.item(row, col)
        if not item:
            return
            
        # Zabezpieczenie przed zacięciem
        if item.data(Qt.UserRole + 3):
            return
            
        orig_bg = item.data(Qt.BackgroundRole)
        orig_fg = item.data(Qt.ForegroundRole)
        
        item.setData(Qt.BackgroundRole, QColor("#4ade80") if self._is_light else QColor("#2d7a42")) 
        item.setData(Qt.ForegroundRole, QColor("#0f172a") if self._is_light else QColor("#ffffff")) 
        item.setData(Qt.UserRole + 3, True) # Informuje Delegata, że migamy
        
        t = QTimer(self)
        t.setSingleShot(True)
        self._flash_timers.append(t)

        def restore():
            # TUTAJ BYŁ BŁĄD - Poprawione na tableWidget()
            if item.tableWidget() is not None:
                item.setData(Qt.BackgroundRole, orig_bg)
                item.setData(Qt.ForegroundRole, orig_fg)
                item.setData(Qt.UserRole + 3, None)
            if t in self._flash_timers:
                self._flash_timers.remove(t)

        t.timeout.connect(restore)
        t.start(200) 

    def _update_buttons(self):
        selected = self._get_selected_row_ids()
        one = len(selected) == 1
        self._act_edit.setEnabled(one)
        self._act_delete.setEnabled(len(selected) > 0)
        self._btn_duplicate_row.setEnabled(one)

    # --------------------------------------------------------------- Slots

    @Slot(int, int)
    def _on_cell_clicked(self, row: int, col: int):
        if self._loading:
            return

        attr = self._active_columns[col][1] if col < len(self._active_columns) else ""

        if attr in _COPY_ATTRS:
            item = self._table.item(row, col)
            if item:
                val = item.data(Qt.UserRole) if attr == "_json" else item.text()
                if val:
                    QApplication.clipboard().setText(val)
                    label_map = {
                        "license_plate": "Nr rejestracyjny",
                        "device_id":     "ID urządzenia",
                        "sim_number":    "SIM",
                        "_ccid":         "CCID",
                        "_json":         "JSON",
                    }
                    self._status_bar.showMessage(
                        f"{label_map.get(attr, attr)} skopiowany do schowka.", 3000
                    )
                    self._flash_cell(row, col)
            return

        self._selected_record_id = self._get_record_id_for_row(row)
        self._update_buttons()

    def _on_double_clicked(self, index):
        row = index.row() if hasattr(index, "row") else self._table.currentRow()
        col = index.column() if hasattr(index, "column") else -1
        
        col_attr = self._active_columns[col][1] if col < len(self._active_columns) else ""
        
        # Ignorujemy dwuklik na kopiowanie ORAZ na checkboxa "odebrane"
        if col_attr in _COPY_ATTRS or col_attr == "_odebrane":
            return
            
        rec_id = self._get_record_id_for_row(row)
        if rec_id is None:
            return
            
        rec = self._db.get_record_by_id(rec_id)
        if not rec:
            QMessageBox.warning(self, "Błąd", "Nie znaleziono rekordu.")
            return
            
        from ui.service_form import ServiceForm
        self._open_form(ServiceForm(record=rec), "Rekord zaktualizowany.")

    @Slot()
    def _on_item_changed(self, item: QTableWidgetItem):
        if self._loading:
            return
        row = item.row()
        col = item.column()
        if col >= len(self._active_columns):
            return
        attr = self._active_columns[col][1]

        if attr == "_odebrane":
            rec_id = self._get_record_id_for_row(row)
            if rec_id is None:
                return
            rec = self._db.get_record_by_id(rec_id)
            if not rec:
                return
            checked = item.checkState() == Qt.Checked
            rec.config_json["odebrane"] = checked
            try:
                self._db.update_record(rec)
            except Exception as exc:
                logger.error(f"Błąd zapisu odebrano: {exc}", exc_info=True)
            self._apply_row_color(row, checked)
            dyzur_checked = bool(rec.config_json.get("dyzurZaznaczony"))
            self._apply_dyzur_color(row, dyzur_checked)

    @Slot(QPoint)
    def _on_context_menu(self, pos: QPoint):
        selected_rows = self._get_selected_rows()
        if not selected_rows:
            return

        menu = QMenu(self)

        if len(selected_rows) == 1:
            rec_id = self._get_record_id_for_row(selected_rows[0])
            rec = self._db.get_record_by_id(rec_id) if rec_id is not None else None
            fleet = (rec.fleet_name or "").strip() if rec else ""
            url = self._db.get_url_for_fleet(fleet) if fleet else ""
            label = f"🌐  Przejdź do floty {fleet}" if fleet else "🌐  Przejdź do floty"
            act_fleet = menu.addAction(label)
            act_fleet.setEnabled(bool(url))
            if url:
                act_fleet.triggered.connect(lambda *_, u=url: QDesktopServices.openUrl(QUrl(u)))
            menu.addSeparator()

        if self._db.get_setting("show_duty_section", "1") == "1":
            act = menu.addAction(f"📋  Kopiuj do dyżurów ({len(selected_rows)} wierszy)")
            act.triggered.connect(lambda: self._copy_duty_info(selected_rows))

        if not menu.isEmpty():
            menu.exec(self._table.viewport().mapToGlobal(pos))

    def _copy_duty_info(self, rows: list):
        lines = []
        for row in rows:
            rec_id = self._get_record_id_for_row(row)
            if rec_id is None:
                continue
            rec = self._db.get_record_by_id(rec_id)
            if rec:
                duty_time = rec.duty_time_min if rec.duty_time_min else ""
                typ_val = rec.record_type.strip() if rec.record_type else ""
                line = f"{typ_val} - {rec.company_name} - {rec.license_plate}\t{duty_time}"
                lines.append(line)
        if lines:
            QApplication.clipboard().setText("\n".join(lines))
            self._status_bar.showMessage(
                f"Skopiowano {len(lines)} wierszy do schowka.", 3000
            )

    @Slot()
    def _on_q_today(self):
        today = QDate.currentDate()
        self._filter_date_from.setDate(today)
        self._filter_date_to.setDate(today)
        self._on_filter()

    @Slot()
    def _on_q_week(self):
        today = QDate.currentDate()
        monday = today.addDays(-(today.dayOfWeek() - 1))
        self._filter_date_from.setDate(monday)
        self._filter_date_to.setDate(monday.addDays(6))
        self._on_filter()

    @Slot()
    def _on_q_month(self):
        today = QDate.currentDate()
        first_day = QDate(today.year(), today.month(), 1)
        last_day = QDate(today.year(), today.month(), today.daysInMonth())
        self._filter_date_from.setDate(first_day)
        self._filter_date_to.setDate(last_day)
        self._on_filter()

    @Slot()
    def _on_q_prev_month(self):
        today = QDate.currentDate()
        first_day = QDate(today.year(), today.month(), 1).addMonths(-1)
        last_day = QDate(first_day.year(), first_day.month(), first_day.daysInMonth())
        self._filter_date_from.setDate(first_day)
        self._filter_date_to.setDate(last_day)
        self._on_filter()

    @Slot()
    def _on_q_year(self):
        today = QDate.currentDate()
        self._filter_date_from.setDate(QDate(today.year(), 1, 1))
        self._filter_date_to.setDate(QDate(today.year(), 12, 31))
        self._on_filter()

    @Slot(bool)
    def _on_q_duty(self, only_duty: bool):
        current_search = self._filter_search.text().strip()
        terms = [t for t in current_search.split() if not t.startswith("dyżur:")]
        if only_duty:
            terms.append("dyżur:dyżur")
        else:
            terms.append("dyżur:")
        self._filter_search.setText(" ".join(terms))
        self._on_filter()

    @Slot()
    def _on_open_settings(self):
        from ui.settings_window import SettingsWindow
        dlg = SettingsWindow(ALL_COLUMNS, self._column_order, self._visible_columns, self)
        dlg.columns_changed.connect(self._on_columns_changed)
        dlg.exec()

        is_duty = self._db.get_setting("show_duty_section", "1") == "1"
        self._btn_q_duty.setVisible(is_duty)
        self._btn_q_no_duty.setVisible(is_duty)

    @Slot(set, list)
    def _on_columns_changed(self, visible: set, order: list):
        self._visible_columns = visible
        self._column_order = order
        self._rebuild_table_columns()
        self.load_records()

    @Slot()
    def _on_duplicate_row(self):
        selected = self._get_selected_row_ids()
        if len(selected) != 1:
            return
        rec = self._db.get_record_by_id(selected[0])
        if not rec:
            return

        now = datetime.now()
        m = int(round(now.minute / 5.0) * 5)
        h = now.hour + (1 if m == 60 else 0)
        h_mod = h % 24
        m_mod = m % 60
        
        new_rec = copy.deepcopy(rec)
        new_rec.id = None
        new_rec.service_date = now.strftime("%Y-%m-%d")
        new_rec.service_hour = h_mod
        new_rec.service_minute = m_mod
        new_rec.license_plate = ""
        new_rec.device_id = ""
        new_rec.sim_number = ""
        new_rec.mileage = None
        new_rec.probe1_id = ""
        new_rec.probe1_capacity = None
        new_rec.probe1_length = None
        new_rec.probe2_id = ""
        new_rec.probe2_capacity = None
        new_rec.probe2_length = None
        new_rec.config_json = copy.deepcopy(rec.config_json)
        new_rec.config_json.get("additionalConfig", {}).pop("ccid", None)
        new_rec.config_json["odebrane"] = False
        
        is_weekend = now.weekday() >= 5
        is_duty_time = h_mod >= 15 or h_mod < 6 or (h_mod == 6 and m_mod <= 55)
        new_rec.config_json["dyzurZaznaczony"] = is_weekend or is_duty_time

        try:
            self._db.insert_record(new_rec)
            self.load_records()
            self._status_bar.showMessage("Rekord zduplikowany.", 3000)
        except Exception as exc:
            logger.error(f"Błąd duplikowania: {exc}", exc_info=True)
            QMessageBox.critical(self, "Błąd", f"Nie udało się zduplikować:\n{exc}")

    def _open_form(self, form, success_msg: str):
        """Otwiera formularz jako niezależne okno (non-modal)."""
        self._open_forms.append(form)
        form.accepted.connect(lambda: (self.load_records(),
                                       self._status_bar.showMessage(success_msg, 3000)))
        form.finished.connect(lambda: self._open_forms.remove(form)
                              if form in self._open_forms else None)
        form.show()

    @Slot()
    def _on_new(self):
        from ui.service_form import ServiceForm
        self._open_form(ServiceForm(), "Nowy rekord dodany.")

    @Slot()
    def _on_edit(self):
        selected = self._get_selected_row_ids()
        if len(selected) == 1:
            rec_id = selected[0]
        elif self._selected_record_id:
            rec_id = self._selected_record_id
        else:
            return
            
        rec = self._db.get_record_by_id(rec_id)
        if not rec:
            QMessageBox.warning(self, "Błąd", "Nie znaleziono rekordu.")
            return
            
        from ui.service_form import ServiceForm
        self._open_form(ServiceForm(record=rec), "Rekord zaktualizowany.")

    @Slot()
    def _on_delete(self):
        selected_ids = self._get_selected_row_ids()
        
        if not selected_ids:
            if self._selected_record_id:
                selected_ids = [self._selected_record_id]
            else:
                return

        count = len(selected_ids)
        msg = (
            f"Usunąć {count} rekord(y/ów)?\nNie można cofnąć."
            if count > 1
            else f"Usunąć rekord ID {selected_ids[0]}?\nNie można cofnąć."
        )
        if QMessageBox.question(
            self, "Usuń rekord", msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            for rid in selected_ids:
                self._db.delete_record(rid)
            self.load_records()
            self._status_bar.showMessage(f"Usunięto {count} rekord(ów).", 3000)

    @Slot()
    def _on_filter(self):
        f: dict = {}
        if s := self._filter_search.text().strip().lower():
            f["smart_search"] = s
        f["date_from"] = self._filter_date_from.date().toString("yyyy-MM-dd")
        f["date_to"]   = self._filter_date_to.date().toString("yyyy-MM-dd")
        self.load_records(f)

    @Slot()
    def _on_clear_filter(self):
        self._filter_search.clear()
        self._filter_date_from.setDate(QDate(2020, 1, 1))
        self._filter_date_to.setDate(QDate.currentDate())
        self._on_filter()

    @Slot()
    def _on_selection_changed(self):
        if self._loading:
            return
        rows = self._table.selectedItems()
        if rows:
            row = self._table.currentRow()
            self._selected_record_id = self._get_record_id_for_row(row)
        else:
            self._selected_record_id = None
        self._update_buttons()