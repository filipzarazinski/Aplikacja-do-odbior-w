"""
ui/widgets/montaz_tab.py
Zoptymalizowany układ z inteligentną logiką pola SIM, zaawansowanymi podpowiedziami (QCompleter)
rozdzielającymi flotę/typ oraz czasem dyżuru w prawym dolnym rogu.
"""

import json
import logging
import re
from typing import Optional, Tuple
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QLineEdit, QComboBox, QTextEdit,
    QCheckBox, QRadioButton, QSpinBox, QDateEdit, QTimeEdit,
    QFrame, QButtonGroup, QTabWidget,
    QSizePolicy, QApplication, QAbstractSpinBox, QCompleter
)
from PySide6.QtCore import Qt, QDate, QTime, Slot, QTimer, QEvent, QStringListModel
from PySide6.QtGui import QPixmap, QPainter, QColor, QIcon

from database.models import ServiceRecord, DinChannel
from database.db_manager import DatabaseManager
from config import (
    VEHICLE_TYPES, D8_OPTIONS,
    DIN_NEEDS_SN_KEYWORDS, CAN_CHECKBOX_LABELS, CAN_JSON_KEYS,
    CAN_CONNECTION_TRUCK, CAN_CONNECTION_CAR,
    TACHO_BRANDS_TACHOREADER, TACHO_BRANDS_FMB640,
)

logger = logging.getLogger(__name__)

# ── Reguły auto-wykrywania modelu z ID ────────────────────────────────────────
# Kolejność ma znaczenie – bardziej szczegółowe wzorce są pierwsze.
_ID_TO_MODEL: list[tuple] = [
    (re.compile(r'^S10-S',    re.I), "Skaut 10 SMA"),
    (re.compile(r'^S10-F',    re.I), "Skaut 10 FAKRA"),
    (re.compile(r'^S8LTE-F',  re.I), "Skaut 8 LTE FAKRA"),
    (re.compile(r'^S8LTE',    re.I), "Skaut 8 LTE"),
    (re.compile(r'^S8(?!LTE)',re.I), "Skaut 8"),
    (re.compile(r'^S5P',      re.I), "Skaut 5 Pro LTE"),
    (re.compile(r'^201\d{7}$'      ), "Skaut 5 Pro"),       # 10 cyfr: 201XXXXXXX
    (re.compile(r'^86\d{8,}$'      ), "FMC650"),             # 10+ cyfr: 86XXXXXXXX
    (re.compile(r'^6\d{4}$'        ), "Skaut6"),             # 5 cyfr: 6XXXX
    (re.compile(r'^5\d{4}$'        ), "Skaut5"),
    (re.compile(r'^4\d{4}$'        ), "Skaut4"),
    (re.compile(r'^3\d{4}$'        ), "Skaut3"),
    (re.compile(r'^2\d{4}$'        ), "Skaut2"),
    (re.compile(r'^1\d{4}$'        ), "Skaut1"),
]

# Skróty wpisywane ręcznie w pole modelu (jak w VBA ComboBox9_Change)
_MODEL_SHORTCUTS: dict[str, str] = {
    "8.2":  "Albatros 8.2",
    "8.3":  "Albatros 8.3",
    "8.5":  "Albatros 8.5",
    "A12":  "FMA120",
    "B12":  "FMB120",
    "140":  "FMB140",
    "204":  "FMB204",
    "640":  "FMB640",
    "1010": "FM1010",
    "5300": "FM5300",
}

# Pobranie motywu bezpośrednio przy ładowaniu pliku
_is_light = DatabaseManager.instance().get_setting("theme_mode", "dark") == "light"

# ── Stałe stylu ───────────────────────────────────────────────────────────────
if _is_light:
    _BG_PANEL  = "#f8fafc"
    _BG_INPUT  = "#ffffff"
    _BG_MAIN   = "#f1f5f9"
    _BORDER    = "#cbd5e1"
    _TEXT      = "#0f172a"
    _TEXT_DIM  = "#334155"
    _TEXT_MUTE = "#64748b"
    _BG_TAB    = "#e2e8f0"
else:
    _BG_PANEL  = "#22262f"
    _BG_INPUT  = "#2a2f3a"
    _BG_MAIN   = "#1a1d23"
    _BORDER    = "#3a4150"
    _TEXT      = "#e2e8f0"
    _TEXT_DIM  = "#94a3b8"
    _TEXT_MUTE = "#64748b"
    _BG_TAB    = "#1e2229"

_H         = 26

_GRP_STYLE = f"""
QGroupBox {{
    background: transparent;
    border: 1px solid {_BORDER};
    border-radius: 4px;
    margin-top: 8px;
    padding: 6px;
    color: {_TEXT_MUTE};
    font-weight: 700;
    font-size: 8pt;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px; top: -1px;
    padding: 0 4px;
    background: {_BG_PANEL};
    color: {_TEXT_MUTE};
}}
"""

_INPUT_STYLE = f"""
    background: {_BG_INPUT}; border: 1px solid {_BORDER}; border-radius: 3px;
    color: {_TEXT}; font-size: 9pt; padding: 2px 6px;
"""

_CB_STYLE = f"QCheckBox {{ background: transparent; color: {_TEXT_DIM}; font-size: 9pt; spacing: 5px; }}"
_RB_STYLE = f"QRadioButton {{ background: transparent; color: {_TEXT_DIM}; font-size: 9pt; spacing: 5px; }}"

def _lbl(t: str, muted: bool = False) -> QLabel:
    l = QLabel(t)
    col = _TEXT_MUTE if muted else _TEXT_DIM
    l.setStyleSheet(f"color: {col}; font-size: 9pt; background: transparent;")
    return l

class CustomLineEdit(QLineEdit):
    """Pole tekstowe, które przyciskiem Tab autouzupełnia pierwszą podpowiedź z QCompletera."""
    def event(self, event):
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Tab:
            c = self.completer()
            if c and c.popup() and c.popup().isVisible():
                index = c.popup().currentIndex()
                if not index.isValid():
                    index = c.completionModel().index(0, 0)
                    
                if index.isValid():
                    text = c.completionModel().data(index)
                    self.setText(text)
                    c.popup().hide()
                    c.activated.emit(text)  # Odpala logikę np. z rozdzielaniem Floty
                    self.focusNextChild()  # Przechodzi do następnego pola
                    return True  # Zatrzymuje domyślne przeskakiwanie okna
        return super().event(event)

def _inp(ph: str = "", w: int = 0) -> CustomLineEdit:
    e = CustomLineEdit(); e.setPlaceholderText(ph)
    e.setMinimumHeight(_H)
    if w: e.setFixedWidth(w)  
    e.setStyleSheet(_INPUT_STYLE)
    return e

def _combo(items=None, editable=False, w=0) -> QComboBox:
    c = QComboBox()
    c.setMinimumHeight(_H)
    if editable: 
        c.setEditable(True)
        c.setInsertPolicy(QComboBox.NoInsert)
        comp = c.completer()
        if comp:
            comp.setFilterMode(Qt.MatchContains)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
    if items: c.addItems(items)
    if w: c.setMinimumWidth(w)
    c.setStyleSheet(
        f"QComboBox{{background:{_BG_INPUT};border:1px solid {_BORDER};border-radius:3px;"
        f"color:{_TEXT};font-size:9pt;padding:2px 6px;}}"
        f"QComboBox:focus{{border-color:#64748b;}}"
        f"QComboBox::drop-down{{border:none;width:20px;background:transparent;}}"
        f"QComboBox::down-arrow{{border-left:4px solid transparent;border-right:4px solid transparent;"
        f"border-top:5px solid {_TEXT_MUTE};width:0;height:0;margin-right:5px;}}"
        f"QComboBox QAbstractItemView{{background:{_BG_INPUT};border:1px solid {_BORDER};"
        f"color:{_TEXT};selection-background-color:#3a4150;}}"
    )
    return c

def _rb(t: str) -> QRadioButton:
    r = QRadioButton(t); r.setStyleSheet(_RB_STYLE); return r

def _cb(t: str) -> QCheckBox:
    c = QCheckBox(t); c.setStyleSheet(_CB_STYLE); return c

if _is_light:
    _SMALL_CB_STYLE = f"""
        QCheckBox {{ background: transparent; }}
        QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid #cbd5e1; border-radius: 2px; background: #ffffff; }}
        QCheckBox::indicator:hover {{ border-color: #94a3b8; }}
        QCheckBox::indicator:checked {{ 
            background: #ffffff; border-color: #cbd5e1; 
            image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%230f172a' stroke-width='4' stroke-linecap='round' stroke-linejoin='round'><polyline points='20 6 9 17 4 12'/></svg>");
        }}
    """
else:
    _SMALL_CB_STYLE = f"""
        QCheckBox {{ background: transparent; }}
        QCheckBox::indicator {{ width: 11px; height: 11px; border: 1px solid #475569; border-radius: 2px; background: #2a2f3a; }}
        QCheckBox::indicator:hover {{ border-color: #94a3b8; }}
        QCheckBox::indicator:checked {{ background: #64748b; border-color: #94a3b8; }}
    """

class MontazTab(QWidget):

    def __init__(self, record=None, edit_mode=False, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{_BG_MAIN};")
        self._db = DatabaseManager.instance()
        self._edit_mode = edit_mode
        self._initialized = False
        self._model_auto_set = False  # True gdy model ustawiony automatycznie z ID
        self._current_fleet_type = ""

        self._build_ui()
        self._populate_dropdowns()
        self._connect_formatters() 
        
        if not edit_mode:
            self._set_rounded_current_time()
            
        self._initialized = True

    def _set_rounded_current_time(self):
        now = datetime.now()
        m = int(round(now.minute / 5.0) * 5)
        h = now.hour
        if m == 60:
            m = 0
            h = (h + 1) % 24
        self._time_edit.setTime(QTime(h, m))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 8) 
        root.setSpacing(8)

        root.addWidget(self._sec_header())
        root.addWidget(self._sec_comments(), stretch=1) 
        root.addWidget(self._sec_middle())
        root.addWidget(self._sec_bottom())

    def _sec_header(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{_BG_PANEL};border-radius:4px;")
        g = QGridLayout(w)
        g.setContentsMargins(8,4,8,4)
        g.setSpacing(6)

        g.setColumnStretch(0, 1) 
        g.setColumnStretch(2, 1) 
        g.setColumnStretch(3, 1) 
        g.setColumnStretch(4, 1) 
        g.setColumnStretch(5, 1)

        typ_w = QWidget(); typ_w.setStyleSheet("background:transparent;")
        typ_l = QHBoxLayout(typ_w); typ_l.setContentsMargins(0,0,0,0); typ_l.setSpacing(10)
        self._typ_grp = QButtonGroup(self)
        self._typ_grp.setExclusive(False)
        self._typ_rbs: dict[str, QRadioButton] = {}
        for name in ["Montaż", "Upgrade", "Przekładka", "Serwis", "Telefon"]:
            rb = _rb(name)
            rb.setAutoExclusive(False)
            rb.clicked.connect(lambda checked, b=rb: self._on_typ_rb_clicked(b))
            self._typ_grp.addButton(rb)
            self._typ_rbs[name] = rb; typ_l.addWidget(rb)
        typ_l.addStretch()
        g.addWidget(typ_w, 0, 0, 1, 6)

        g.addWidget(_lbl("Firma"), 1, 0)
        g.addWidget(_lbl("Flota"), 1, 1) 
        
        rej_lbl_w = QWidget(); rej_lbl_w.setStyleSheet("background:transparent;")
        rej_lbl_lay = QHBoxLayout(rej_lbl_w); rej_lbl_lay.setContentsMargins(0,0,0,0); rej_lbl_lay.setSpacing(4)
        rej_lbl_lay.addWidget(_lbl("Nr rejestracyjny"))
        
        self._plate_format_cb = QCheckBox("")
        self._plate_format_cb.setStyleSheet(_SMALL_CB_STYLE)
        self._plate_format_cb.setChecked(False)
        self._plate_format_cb.setToolTip("Wyłącza wymuszanie dużych liter i braku spacji")
        rej_lbl_lay.addWidget(self._plate_format_cb)
        rej_lbl_lay.addStretch()
        g.addWidget(rej_lbl_w, 1, 2)
        
        id_lbl_w = QWidget(); id_lbl_w.setStyleSheet("background:transparent;")
        id_lbl_lay = QHBoxLayout(id_lbl_w); id_lbl_lay.setContentsMargins(0,0,0,0); id_lbl_lay.setSpacing(4)
        id_lbl_lay.addWidget(_lbl("ID"))
        
        self._id_format_cb = QCheckBox("")
        self._id_format_cb.setStyleSheet(_SMALL_CB_STYLE)
        self._id_format_cb.setChecked(False)
        self._id_format_cb.setToolTip("Wyłącza wymuszanie dużych liter i braku spacji")
        id_lbl_lay.addWidget(self._id_format_cb)
        id_lbl_lay.addStretch()
        g.addWidget(id_lbl_w, 1, 3)

        g.addWidget(_lbl("SIM"), 1, 4)
        g.addWidget(_lbl("Marka/model"), 1, 5)

        self._company_edit = _inp("")
        self._company_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        g.addWidget(self._company_edit, 2, 0, Qt.AlignTop)

        self._fleet_name_edit = _inp("", w=60)
        self._fleet_name_edit.setReadOnly(True)
        self._fleet_name_edit.setStyleSheet(
            f"background:{_BG_PANEL};border:1px solid {_BORDER};border-radius:3px;"
            f"color:{_TEXT_DIM};font-size:9pt;padding:2px 6px;"
        )
        g.addWidget(self._fleet_name_edit, 2, 1, Qt.AlignTop)

        self._plate_edit = _inp("")
        self._add_copy_button(self._plate_edit)
        g.addWidget(self._plate_edit, 2, 2, Qt.AlignTop)

        self._device_id_edit = _inp("")
        self._add_copy_button(self._device_id_edit)
        g.addWidget(self._device_id_edit, 2, 3, Qt.AlignTop)

        self._sim_edit = _inp("")
        self._add_copy_button(self._sim_edit)
        g.addWidget(self._sim_edit, 2, 4, Qt.AlignTop)

        self._brand_edit = _inp("")
        self._brand_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        g.addWidget(self._brand_edit, 2, 5, Qt.AlignTop)

        self._przek_lbl_w = QWidget(); self._przek_lbl_w.setStyleSheet("background:transparent;")
        przek_lbl_lay = QHBoxLayout(self._przek_lbl_w); przek_lbl_lay.setContentsMargins(0,0,0,0); przek_lbl_lay.setSpacing(4)
        przek_lbl_lay.addWidget(_lbl("Przekładka z:", muted=True))
        
        self._przek_format_cb = QCheckBox("")
        self._przek_format_cb.setStyleSheet(_SMALL_CB_STYLE)
        self._przek_format_cb.setChecked(False)
        self._przek_format_cb.setToolTip("Wyłącza wymuszanie dużych liter i braku spacji")
        przek_lbl_lay.addWidget(self._przek_format_cb)
        przek_lbl_lay.addStretch()
        
        self._przek_lbl_w.setVisible(False)
        g.addWidget(self._przek_lbl_w, 3, 0) 
        
        g.addWidget(_lbl("Nr boczny"), 3, 2)
        g.addWidget(_lbl("Model urządzenia"), 3, 3)
        g.addWidget(_lbl("CCID"), 3, 4) 
        g.addWidget(_lbl("Typ pojazdu"), 3, 5)

        self._przek_rej_edit = _inp("", w=120) 
        self._przek_rej_edit.setVisible(False)
        g.addWidget(self._przek_rej_edit, 4, 0, Qt.AlignLeft) 
        
        self._side_edit = _inp(""); g.addWidget(self._side_edit, 4, 2)
        self._device_model_combo = _inp(""); g.addWidget(self._device_model_combo, 4, 3)
        
        self._ccid_edit = _inp(""); g.addWidget(self._ccid_edit, 4, 4) 
        
        self._vehicle_type_combo = _inp("")
        self._vehicle_type_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        g.addWidget(self._vehicle_type_combo, 4, 5)

        self._vehicle_type_combo.textChanged.connect(self._on_vehicle_type_changed)
        return w

    def _sec_comments(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{_BG_PANEL};border-radius:4px;")
        g = QGridLayout(w); g.setContentsMargins(8,4,8,4); g.setSpacing(6)
        
        g.setRowStretch(0, 0) 
        g.setRowStretch(1, 1) 
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)

        g.addWidget(_lbl("Komentarz do protokołu"), 0, 0)
        g.addWidget(_lbl("Komentarz prywatny"), 0, 1)

        self._comment_edit = QTextEdit()
        self._comment_edit.setMinimumHeight(60) 
        self._comment_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._comment_edit.setStyleSheet(f"{_INPUT_STYLE} padding:4px;")
        g.addWidget(self._comment_edit, 1, 0)
        
        self._private_comment_edit = QTextEdit()
        self._private_comment_edit.setMinimumHeight(60) 
        self._private_comment_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._private_comment_edit.setStyleSheet(f"{_INPUT_STYLE} padding:4px;")
        g.addWidget(self._private_comment_edit, 1, 1)

        right_w = QWidget(); right_w.setStyleSheet("background:transparent;")
        rl = QGridLayout(right_w); rl.setContentsMargins(0,0,0,0)
        rl.setVerticalSpacing(8); rl.setHorizontalSpacing(8)
        rl.setRowStretch(0, 1)

        rl.addWidget(_lbl("Data"), 1, 0)
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.setDisplayFormat("dd.MM.yyyy")
        self._date_edit.setMinimumWidth(105)
        self._date_edit.setMinimumHeight(_H)
        self._date_edit.setStyleSheet(_INPUT_STYLE)
        rl.addWidget(self._date_edit, 1, 1)

        rl.addWidget(_lbl("Godzina"), 2, 0)
        self._time_edit = QTimeEdit()
        self._time_edit.setDisplayFormat("HH:mm")
        self._time_edit.setMinimumWidth(80)
        self._time_edit.setMinimumHeight(_H)
        self._time_edit.setAlignment(Qt.AlignCenter)
        self._time_edit.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self._time_edit.setStyleSheet(_INPUT_STYLE)
        rl.addWidget(self._time_edit, 2, 1)

        rl.setRowStretch(3, 1)
        g.addWidget(right_w, 0, 2, 2, 1) 
        return w

    def _sec_middle(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{_BG_PANEL};border-radius:4px;")
        lay = QHBoxLayout(w); lay.setContentsMargins(8,6,8,6); lay.setSpacing(16)

        d8_w = QWidget(); d8_w.setStyleSheet("background:transparent;")
        d8_l = QVBoxLayout(d8_w); d8_l.setContentsMargins(0,0,0,0); d8_l.setSpacing(4)
        d8_l.addWidget(_lbl("D8"))
        
        self._d8_combo = _combo([""] + D8_OPTIONS)
        self._d8_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        d8_l.addWidget(self._d8_combo)

        self._tacho_grp = QGroupBox("TACHO")
        self._tacho_grp.setStyleSheet(_GRP_STYLE)
        tg = QVBoxLayout(self._tacho_grp); tg.setSpacing(4)
        tg_rb_lay = QHBoxLayout()
        self._tach_btng = QButtonGroup(self)
        self._rb_siemens   = _rb("Siemens")
        self._rb_stonerige = _rb("Stonerige")
        for rb in (self._rb_siemens, self._rb_stonerige):
            self._tach_btng.addButton(rb); tg_rb_lay.addWidget(rb)
        tg_rb_lay.addStretch()
        tg.addLayout(tg_rb_lay)
        tg_ver_lay = QHBoxLayout()
        tg_ver_lay.addWidget(_lbl("Wer.:"))
        self._tacho_ver = _inp("") 
        self._tacho_ver.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        tg_ver_lay.addWidget(self._tacho_ver)
        tg.addLayout(tg_ver_lay)
        self._tacho_grp.setVisible(False)
        d8_l.addWidget(self._tacho_grp)

        self._fmb_grp = QGroupBox("TACHO FMB")
        self._fmb_grp.setStyleSheet(_GRP_STYLE)
        fg = QVBoxLayout(self._fmb_grp); fg.setSpacing(4)
        fg_rb_lay = QHBoxLayout()
        self._fmb_btng = QButtonGroup(self)
        self._rb_tel_s  = _rb("Siemens")
        self._rb_tel_sr = _rb("Stoneridge")
        self._rb_tel_i  = _rb("Inne")
        for rb in (self._rb_tel_s, self._rb_tel_sr, self._rb_tel_i):
            self._fmb_btng.addButton(rb); fg_rb_lay.addWidget(rb)
        fg_rb_lay.addStretch()
        fg.addLayout(fg_rb_lay)
        fg_ver_lay = QHBoxLayout()
        fg_ver_lay.addWidget(_lbl("Wer.:"))
        self._tacho_fmb_ver = _inp("")
        self._tacho_fmb_ver.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        fg_ver_lay.addWidget(self._tacho_fmb_ver)
        fg.addLayout(fg_ver_lay)
        self._fmb_grp.setVisible(False)
        d8_l.addWidget(self._fmb_grp)
        
        d8_l.addStretch()
        lay.addWidget(d8_w, stretch=2) 

        tabs = QTabWidget()
        tabs.setFixedWidth(380) 
        tabs.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed) 
        tabs.setFixedHeight(185) 
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{ background:{_BG_INPUT}; border:1px solid {_BORDER}; border-radius:3px; }}
            QTabBar::tab {{ background:{_BG_TAB}; color:{_TEXT_MUTE}; border:1px solid {_BORDER};
                border-bottom:none; border-radius:3px 3px 0 0; padding:4px 14px; margin-right:2px; }}
            QTabBar::tab:selected {{ background:{_BG_INPUT}; color:{_TEXT}; font-weight:600; }}
        """)

        paliwo_w = QWidget(); paliwo_w.setStyleSheet(f"background:{_BG_INPUT};")
        pg = QGridLayout(paliwo_w); pg.setContentsMargins(8,6,8,6); pg.setSpacing(8) 
        pg.setColumnStretch(3, 1) 
        
        pg.addWidget(_lbl(""), 0, 0)
        pg.addWidget(_lbl("An0", muted=True), 0, 1, Qt.AlignCenter)
        pg.addWidget(_lbl("An1", muted=True), 0, 2, Qt.AlignCenter)
        
        for row_i, (lbl_txt, a0_attr, a1_attr) in enumerate([
            ("Numer",     "_probe1_id",  "_probe2_id"),
            ("Pojemność", "_probe1_cap", "_probe2_cap"),
            ("Skalowanie","_probe1_len", "_probe2_len"),
        ], start=1):
            pg.addWidget(_lbl(lbl_txt), row_i, 0)
            e0 = _inp(""); e0.setMinimumWidth(85); setattr(self, a0_attr, e0); pg.addWidget(e0, row_i, 1)
            e1 = _inp(""); e1.setMinimumWidth(85); setattr(self, a1_attr, e1); pg.addWidget(e1, row_i, 2)

        pg.addWidget(_lbl("Który prawy?"), 4, 0)
        prawy_w = QWidget(); prawy_w.setStyleSheet("background:transparent;")
        pl = QHBoxLayout(prawy_w); pl.setContentsMargins(0,0,0,0); pl.setSpacing(10)
        self._prawy_group   = QButtonGroup(self)
        self._rb_prawy_an0  = _rb("An0"); self._rb_prawy_an1 = _rb("An1")
        self._rb_prawy_brak = _rb("—");  self._rb_prawy_brak.setChecked(True)
        for rb in (self._rb_prawy_an0, self._rb_prawy_an1, self._rb_prawy_brak):
            self._prawy_group.addButton(rb); pl.addWidget(rb)
        pl.addStretch()
        pg.addWidget(prawy_w, 4, 1, 1, 2)
        pg.setRowStretch(5, 1)
        tabs.addTab(paliwo_w, "Paliwo")

        can_w = QWidget(); can_w.setStyleSheet(f"background:{_BG_INPUT};")
        can_center_lay = QHBoxLayout(can_w)
        can_center_lay.setContentsMargins(0,0,0,0)
        can_center_lay.addStretch(1) 
        
        can_inner = QWidget()
        cl = QGridLayout(can_inner)
        cl.setContentsMargins(0,10,0,10)
        cl.setSpacing(10)
        
        self._can_active_cb = _cb("Czy CAN jest opomiarowany?")
        cl.addWidget(self._can_active_cb, 0, 0, 1, 3)

        radio_lay = QVBoxLayout()
        self._can_type_grp = QButtonGroup(self)
        self._can_truck_rb = _rb("Ciężarowy"); self._can_car_rb = _rb("Osobowy")
        self._can_type_grp.addButton(self._can_truck_rb)
        self._can_type_grp.addButton(self._can_car_rb)
        radio_lay.addWidget(self._can_truck_rb); radio_lay.addWidget(self._can_car_rb)
        radio_lay.addStretch()
        cl.addLayout(radio_lay, 1, 0, 4, 1)

        self._can_cbs: list[QCheckBox] = [_cb(t) for t in CAN_CHECKBOX_LABELS]
        cl.addWidget(self._can_cbs[0], 1, 1)
        cl.addWidget(self._can_cbs[1], 2, 1)
        cl.addWidget(self._can_cbs[2], 3, 1)
        cl.addWidget(self._can_cbs[3], 4, 1)
        cl.addWidget(self._can_cbs[4], 1, 2)
        cl.addWidget(self._can_cbs[5], 2, 2)
        cl.addWidget(self._can_cbs[6], 3, 2)
        cl.addWidget(self._can_cbs[7], 4, 2)
        cl.setRowStretch(5, 1)
        
        can_center_lay.addWidget(can_inner)
        can_center_lay.addStretch(1) 
        
        tabs.addTab(can_w, "CAN")

        lay.addWidget(tabs, stretch=0) 

        right_w = QWidget(); right_w.setStyleSheet("background:transparent;")
        right_w.setMinimumWidth(200)
        right_l = QVBoxLayout(right_w); right_l.setContentsMargins(0,0,0,0); right_l.setSpacing(4)

        right_l.addWidget(_lbl("Monter"))
        self._technician_combo = _inp("")
        self._technician_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        right_l.addWidget(self._technician_combo)

        right_l.addSpacing(6)

        right_l.addWidget(_lbl("Gdzie rejestrator"))
        self._recorder_loc_edit = _inp("")
        self._recorder_loc_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        right_l.addWidget(self._recorder_loc_edit)
        
        right_l.addSpacing(6)
        
        right_l.addWidget(_lbl("Przebieg"))
        self._mileage_edit = _inp("km")
        self._mileage_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        right_l.addWidget(self._mileage_edit)

        right_l.addSpacing(6)
        self._odebrane_cb = _cb("Odebrane?")
        right_l.addWidget(self._odebrane_cb)
        
        right_l.addStretch()
        lay.addWidget(right_w, stretch=2)

        self._can_active_cb.toggled.connect(self._on_can_active_changed)
        self._can_truck_rb.clicked.connect(self._on_can_truck_selected)
        self._can_car_rb.clicked.connect(self._on_can_car_selected)
        self._set_can_controls_enabled(False)
        self._d8_combo.currentTextChanged.connect(self._on_d8_changed)

        return w

    def _sec_bottom(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{_BG_PANEL};border-radius:4px;")
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed) 
        
        lay = QHBoxLayout(w); lay.setContentsMargins(8,4,8,6); lay.setSpacing(12)

        # 1. Lewa kolumna - Sekcja DIN
        din_w = QWidget(); din_w.setStyleSheet("background:transparent;")
        din_l = QVBoxLayout(din_w); din_l.setContentsMargins(0,0,0,0); din_l.setSpacing(6)

        dg = QGridLayout()
        dg.setVerticalSpacing(8) 
        dg.setHorizontalSpacing(6)

        dg.addWidget(_lbl("Nazwa", True), 0, 0, Qt.AlignVCenter)
        dg.addWidget(_lbl("Din", True), 0, 1, Qt.AlignVCenter)
        dg.addWidget(_lbl("Stan", True), 0, 2, Qt.AlignVCenter)

        self._webasto_cb = _cb("Webasto")
        dg.addWidget(self._webasto_cb, 1, 0, Qt.AlignVCenter)
        self._webasto_din_combo = self._make_din_type_edit()
        dg.addWidget(self._webasto_din_combo, 1, 1, Qt.AlignVCenter)
        wb_state = QWidget(); wb_state.setStyleSheet("background:transparent;")
        wsl = QHBoxLayout(wb_state); wsl.setContentsMargins(0,0,0,0); wsl.setSpacing(4)
        self._webasto_level_grp = QButtonGroup(self)
        self._webasto_low_rb  = _rb("Niski"); self._webasto_high_rb = _rb("Wysoki")
        for rb in (self._webasto_low_rb, self._webasto_high_rb):
            self._webasto_level_grp.addButton(rb); wsl.addWidget(rb)
        dg.addWidget(wb_state, 1, 2, Qt.AlignVCenter)

        self._din_rows: list[dict] = []
        for i in range(4):
            row = i + 2
            func, func_model = self._make_din_func_edit()
            dg.addWidget(func, row, 0, Qt.AlignVCenter)

            din_type = self._make_din_type_edit()
            dg.addWidget(din_type, row, 1, Qt.AlignVCenter)

            state_w = QWidget(); state_w.setStyleSheet("background:transparent;")
            sl = QHBoxLayout(state_w); sl.setContentsMargins(0,0,0,0); sl.setSpacing(4)
            grp = QButtonGroup(self)
            low = _rb("Niski"); high = _rb("Wysoki")
            grp.addButton(low); grp.addButton(high)
            sl.addWidget(low); sl.addWidget(high)
            dg.addWidget(state_w, row, 2, Qt.AlignVCenter)

            sn = _inp("S/N", 110); sn.setVisible(False)
            dg.addWidget(sn, row, 3, Qt.AlignVCenter)

            rd = {"func":func,"func_model":func_model,"din_type":din_type,"high_rb":high,"low_rb":low,"level_grp":grp,"sn_edit":sn}
            self._din_rows.append(rd)
            func.textChanged.connect(lambda t, r=rd: self._on_din_function_changed(t, r))

        din_l.addLayout(dg)
        din_l.addStretch() 
        
        lay.addWidget(din_w, 0, Qt.AlignTop)

        # 2. Środkowa kolumna - Urządzenia dodatkowe (Z wyrównaniem do góry)
        mid_w = QWidget(); mid_w.setStyleSheet("background:transparent;")
        mid_l = QVBoxLayout(mid_w); mid_l.setContentsMargins(0,0,0,0); mid_l.setSpacing(8)
        
        t_lay = QHBoxLayout()
        self._tablet_cb = _cb("Tablet")
        t_lay.addWidget(self._tablet_cb, 0, Qt.AlignVCenter)
        lbl_nr = _lbl("nr")
        lbl_nr.setContentsMargins(0,0,0,0)
        t_lay.addWidget(lbl_nr, 0, Qt.AlignVCenter)
        self._tablet_nr_edit = _inp("", w=40) 
        t_lay.addWidget(self._tablet_nr_edit, 0, Qt.AlignVCenter)
        t_lay.addStretch()
        mid_l.addLayout(t_lay)

        self._power_cb = _cb("Wyprowadzenie zasilania")
        mid_l.addWidget(self._power_cb)

        ir_lay = QHBoxLayout()
        self._immo_cb = _cb("immo"); self._rfid_cb = _cb("RFID")
        ir_lay.addWidget(self._immo_cb); ir_lay.addWidget(self._rfid_cb)
        ir_lay.addStretch()
        mid_l.addLayout(ir_lay)
        mid_l.addStretch()
        lay.addWidget(mid_w, 0, Qt.AlignTop)

        lay.addStretch(1)

        # 3. Prawa kolumna (Cięcie do prawej strony) - Dyżury
        cz_w = QWidget(); cz_w.setStyleSheet("background:transparent;")
        cz_l = QVBoxLayout(cz_w); cz_l.setContentsMargins(0,0,0,0); cz_l.setSpacing(4)
        
        self._duty_cb = _cb("Dyżur")
        cz_l.addWidget(self._duty_cb)

        cz_l.addWidget(_lbl("Czas dyżuru"))
        
        self._duty_time_edit = _inp("np. 00:30", w=70)
        cz_l.addWidget(self._duty_time_edit)
        
        # Ukrywanie sekcji na podstawie ustawienia w bazie
        if self._db.get_setting("show_duty_section", "1") == "0":
            cz_w.setVisible(False)

        lay.addWidget(cz_w, 0, Qt.AlignBottom | Qt.AlignRight) 

        self._power_cb.toggled.connect(lambda c: self._tablet_cb.setChecked(True) if c else None)
        self._tablet_nr_edit.textChanged.connect(lambda t: self._tablet_cb.setChecked(True) if t.strip() else None)
        self._webasto_cb.toggled.connect(self._on_webasto_changed)
        return w

    def _add_copy_button(self, edit: QLineEdit) -> None:
        """Dodaje ikonę kopiowania wewnątrz QLineEdit po prawej stronie."""
        px = QPixmap(13, 13)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setPen(QColor("#64748b"))
        p.drawRect(1, 3, 7, 8)
        p.drawRect(4, 1, 7, 8)
        p.end()
        act = edit.addAction(QIcon(px), QLineEdit.TrailingPosition)
        act.setToolTip("Kopiuj do schowka")
        act.triggered.connect(lambda: self._copy_and_flash(edit))

    def _copy_and_flash(self, edit: QLineEdit):
        text = edit.text().strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        bg = "#bbf7d0" if _is_light else "#1d6b35"
        border = "#4ade80" if _is_light else "#2e7d32"
        text_color = "#0f172a" if _is_light else _TEXT
        edit.setStyleSheet(
            f"background:{bg}; border:1px solid {border}; border-radius:3px;"
            f"color:{text_color}; font-size:9pt; padding:2px 6px;"
        )
        QTimer.singleShot(800, lambda: edit.setStyleSheet(_INPUT_STYLE))

    @Slot(str)
    def _detect_model_from_id(self, text: str):
        """Automatycznie wykrywa model urządzenia na podstawie wpisanego ID."""
        if not self._initialized:
            return
        t = text.strip()
        for pattern, model in _ID_TO_MODEL:
            if pattern.match(t):
                self._model_auto_set = True
                self._device_model_combo.blockSignals(True)
                self._device_model_combo.setText(model)
                self._device_model_combo.blockSignals(False)
                return
        self._model_auto_set = False

    @Slot(str)
    def _on_model_text_changed(self, text: str):
        """Obsługuje ręczną zmianę modelu: resetuje flagę auto i rozwija skróty."""
        if not self._initialized:
            return
        self._model_auto_set = False
        expanded = _MODEL_SHORTCUTS.get(text.strip())
        if expanded:
            self._device_model_combo.blockSignals(True)
            self._device_model_combo.setText(expanded)
            self._device_model_combo.blockSignals(False)

    def _connect_formatters(self):
        self._sim_edit.textChanged.connect(self._format_sim)
        self._plate_edit.textChanged.connect(self._format_plate)
        self._device_id_edit.textChanged.connect(self._format_id)
        self._przek_rej_edit.textChanged.connect(self._format_przek)
        self._ccid_edit.textChanged.connect(self._on_ccid_changed)
        self._ccid_edit.returnPressed.connect(self._on_ccid_enter_pressed)
        
        # Podpięcie automatycznego aktualizowania logiki floty na podstawie wpisanego tekstu
        self._fleet_name_edit.textChanged.connect(self._on_fleet_name_changed)
        
        # Podpięcie automatycznego dyżuru pod zmianę daty i godziny
        self._time_edit.timeChanged.connect(self._update_duty_auto_check)
        self._date_edit.dateChanged.connect(self._update_duty_auto_check)

        self._plate_format_cb.stateChanged.connect(lambda: self._format_plate(self._plate_edit.text()))
        self._id_format_cb.stateChanged.connect(lambda: self._format_id(self._device_id_edit.text()))
        self._przek_format_cb.stateChanged.connect(lambda: self._format_przek(self._przek_rej_edit.text()))

        # Auto-wykrywanie modelu z ID + skróty w polu modelu
        self._device_id_edit.textChanged.connect(self._detect_model_from_id)
        self._device_model_combo.textChanged.connect(self._on_model_text_changed)

    @Slot(str)
    def _on_fleet_name_changed(self, text: str):
        """Automatycznie dostosowuje podpowiedzi DIN oraz checkboxy na podstawie wpisanej Floty."""
        if not self._initialized: return
        
        t = text.strip().upper()
        fleet_type = "VIP" if ("TAURON" in t or "PGE" in t) else "Zwykłe"
        
        if fleet_type != self._current_fleet_type:
            self._current_fleet_type = fleet_type
            if fleet_type == "VIP":
                self._immo_cb.setChecked(True)
                self._rfid_cb.setChecked(True)
            else:
                self._immo_cb.setChecked(False)
                self._rfid_cb.setChecked(False)
                
        self._reload_din_functions(text.strip())

    @Slot()
    def _update_duty_auto_check(self):
        """Automatyczne zaznaczanie dyżuru dla weekendów oraz poza godzinami pracy (15:00 - 06:55)."""
        date_val = self._date_edit.date()
        time_val = self._time_edit.time()
        
        is_weekend = date_val.dayOfWeek() >= 6  # 6 = Sobota, 7 = Niedziela
        is_duty_time = time_val >= QTime(15, 0) or time_val <= QTime(6, 55)
        
        self._duty_cb.setChecked(is_weekend or is_duty_time)

    @Slot(str)
    def _on_ccid_changed(self, text: str):
        ccid = text.strip()
        if len(ccid) >= 15 and not self._sim_edit.text().strip():
            sim = self._db.get_sim_by_ccid(ccid)
            if sim:
                self._sim_edit.setText(sim)

    @Slot()
    def _on_ccid_enter_pressed(self):
        ccid = self._ccid_edit.text().strip()
        if not ccid:
            return
            
        sim = self._db.get_sim_by_ccid(ccid)
        self._sim_edit.clear()
        if sim:
            self._sim_edit.setText(str(sim))
            try: self.window().statusBar().showMessage(f"Zaktualizowano SIM dla CCID: {ccid}", 3000)
            except: pass

    @Slot(str)
    def _format_sim(self, text: str):
        if not text:
            return
        if not text.startswith("+"):
            self._sim_edit.blockSignals(True)
            clean = text.replace("+", "")
            self._sim_edit.setText("+" + clean)
            self._sim_edit.blockSignals(False)

    @Slot(str)
    def _format_plate(self, text: str):
        if not self._plate_format_cb.isChecked():
            clean = text.upper().replace(" ", "")
            if text != clean:
                self._plate_edit.blockSignals(True)
                cursor = self._plate_edit.cursorPosition()
                diff = len(text) - len(clean)
                self._plate_edit.setText(clean)
                self._plate_edit.setCursorPosition(max(0, cursor - diff))
                self._plate_edit.blockSignals(False)

    @Slot(str)
    def _format_id(self, text: str):
        if not self._id_format_cb.isChecked():
            clean = text.upper().replace(" ", "")
            if text != clean:
                self._device_id_edit.blockSignals(True)
                cursor = self._device_id_edit.cursorPosition()
                diff = len(text) - len(clean)
                self._device_id_edit.setText(clean)
                self._device_id_edit.setCursorPosition(max(0, cursor - diff))
                self._device_id_edit.blockSignals(False)

    @Slot(str)
    def _format_przek(self, text: str):
        if not self._przek_format_cb.isChecked():
            clean = text.upper().replace(" ", "")
            if text != clean:
                self._przek_rej_edit.blockSignals(True)
                cursor = self._przek_rej_edit.cursorPosition()
                diff = len(text) - len(clean)
                self._przek_rej_edit.setText(clean)
                self._przek_rej_edit.setCursorPosition(max(0, cursor - diff))
                self._przek_rej_edit.blockSignals(False)

    def _populate_dropdowns(self):
        model_names = self._db.get_device_model_names()
        dm_comp = QCompleter(model_names, self)
        dm_comp.setCaseSensitivity(Qt.CaseInsensitive)
        dm_comp.setFilterMode(Qt.MatchContains)
        self._device_model_combo.setCompleter(dm_comp)
        self._add_dropdown_action(self._device_model_combo, dm_comp)
        self._reload_din_functions("")

        vt_comp = QCompleter(VEHICLE_TYPES, self)
        vt_comp.setCaseSensitivity(Qt.CaseInsensitive)
        vt_comp.setFilterMode(Qt.MatchContains)
        self._vehicle_type_combo.setCompleter(vt_comp)
        self._add_dropdown_action(self._vehicle_type_combo, vt_comp)

        tech_names = self._db.get_technician_names()
        t_comp = QCompleter(tech_names, self)
        t_comp.setCaseSensitivity(Qt.CaseInsensitive)
        t_comp.setFilterMode(Qt.MatchContains)
        self._technician_combo.setCompleter(t_comp)
        self._add_dropdown_action(self._technician_combo, t_comp)

        loki = [loc for _, loc in self._db.get_all_recorder_locations() if loc]
        if loki:
            l_comp = QCompleter(sorted(list(set(loki))), self)
            l_comp.setCaseSensitivity(Qt.CaseInsensitive)
            l_comp.setFilterMode(Qt.MatchContains)
            self._recorder_loc_edit.setCompleter(l_comp)
            self._add_dropdown_action(self._recorder_loc_edit, l_comp)

        try:
            companies_fleets = set()
            brands_types = set()

            cursor = self._db._conn.cursor()

            try:
                cursor.execute("SELECT name, fleet_name FROM companies WHERE is_active=1")
                for row in cursor.fetchall():
                    comp = str(row[0] or "").strip()
                    fleet = str(row[1] or "").strip()
                    if not comp:
                        continue
                    if fleet:
                        companies_fleets.add(f"{comp} ({fleet})")
                    else:
                        companies_fleets.add(comp)
            except Exception as e:
                logger.error(f"Błąd SQL przy firmach: {e}")

            try:
                cursor.execute("SELECT brand_model, vehicle_type FROM vehicle_models")
                for row in cursor.fetchall():
                    brand = str(row[0] or "").strip()
                    vtype = str(row[1] or "").strip()
                    if not brand:
                        continue
                    if vtype:
                        brands_types.add(f"{brand} ({vtype})")
                    else:
                        brands_types.add(brand)
            except Exception as e:
                logger.error(f"Błąd SQL przy pojazdach: {e}")

            if companies_fleets:
                c_comp = QCompleter(sorted(list(companies_fleets)), self)
                c_comp.setCaseSensitivity(Qt.CaseInsensitive)
                c_comp.setFilterMode(Qt.MatchContains)
                self._company_edit.setCompleter(c_comp)
                c_comp.activated.connect(self._on_company_activated)
                self._add_dropdown_action(self._company_edit, c_comp)

            if brands_types:
                b_comp = QCompleter(sorted(list(brands_types)), self)
                b_comp.setCaseSensitivity(Qt.CaseInsensitive)
                b_comp.setFilterMode(Qt.MatchContains)
                self._brand_edit.setCompleter(b_comp)
                b_comp.activated.connect(self._on_brand_activated)
                self._add_dropdown_action(self._brand_edit, b_comp)

        except Exception as exc:
            logger.error(f"Nie udało się załadować podpowiedzi: {exc}")
            
    @Slot(str)
    def _on_company_activated(self, text: str):
        if " (" in text and text.endswith(")"):
            comp, fleet = text.rsplit(" (", 1) 
            fleet = fleet[:-1] 
            QTimer.singleShot(0, lambda: self._set_company_fleet(comp, fleet))
            
    def _set_company_fleet(self, comp: str, fleet: str):
        self._company_edit.setText(comp)
        self._fleet_name_edit.setText(fleet)

    @Slot(str)
    def _on_brand_activated(self, text: str):
        if " (" in text and text.endswith(")"):
            brand, vtype = text.rsplit(" (", 1)
            vtype = vtype[:-1]
            QTimer.singleShot(0, lambda: self._set_brand_type(brand, vtype))

    def _set_brand_type(self, brand: str, vtype: str):
        self._brand_edit.setText(brand)
        self._vehicle_type_combo.setText(vtype)

    def _make_din_func_edit(self) -> tuple:
        edit = CustomLineEdit()
        edit.setMinimumHeight(_H)
        edit.setMinimumWidth(255)
        edit.setStyleSheet(_INPUT_STYLE)

        model = QStringListModel([], edit)
        comp = QCompleter(model, edit)
        comp.setFilterMode(Qt.MatchContains)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        edit.setCompleter(comp)

        self._add_dropdown_action(edit, comp)

        return edit, model

    def _make_din_type_edit(self) -> CustomLineEdit:
        edit = CustomLineEdit()
        edit.setFixedHeight(_H)
        edit.setFixedWidth(34)
        edit.setAlignment(Qt.AlignCenter)
        edit.setStyleSheet(_INPUT_STYLE + " padding: 2px 2px;")
        return edit

    def _add_dropdown_action(self, edit: QLineEdit, comp: QCompleter) -> None:
        px = QPixmap(10, 7)
        px.fill(Qt.transparent)
        painter = QPainter(px)
        painter.setPen(QColor("#64748b"))
        painter.drawLine(0, 1, 5, 6)
        painter.drawLine(5, 6, 10, 1)
        painter.end()
        act = edit.addAction(QIcon(px), QLineEdit.TrailingPosition)
        act.setToolTip("Pokaż wszystkie")
        act.triggered.connect(lambda _=False, e=edit, c=comp: self._show_dropdown(e, c))

    def _show_dropdown(self, edit: QLineEdit, comp: QCompleter):
        if not edit.text():
            comp.setCompletionPrefix("")
        comp.complete()

    def _reload_din_functions(self, fleet_name: str):
        funcs = self._db.get_extra_devices_for_fleet(fleet_name.strip() or "fm1")
        funcs_set = set(funcs)
        for row in self._din_rows:
            row["func_model"].setStringList(funcs)
            cur = row["func"].text()
            if cur and cur not in funcs_set:
                row["func"].setText("")

    @Slot(bool)
    def _on_typ_rb_clicked(self, clicked_btn):
        if clicked_btn.isChecked():
            for btn in self._typ_grp.buttons():
                if btn != clicked_btn:
                    btn.setChecked(False)
                    
            if hasattr(self, "_duty_time_edit"):
                typ_text = clicked_btn.text()
                if typ_text in ("Montaż", "Upgrade", "Przekładka"):
                    self._duty_time_edit.setText("0:30")
                elif typ_text == "Serwis":
                    self._duty_time_edit.setText("0:20")
                elif typ_text == "Telefon":
                    self._duty_time_edit.setText("")
        
        is_przekladka = self._typ_rbs.get("Przekładka") and self._typ_rbs["Przekładka"].isChecked()
        self._przek_lbl_w.setVisible(is_przekladka)
        self._przek_rej_edit.setVisible(is_przekladka)

    @Slot(str)
    def _on_vehicle_type_changed(self, vehicle_type: str):
        if not self._initialized: return
        vt = vehicle_type.strip().upper()
        if vt == "CIĘŻAROWY":
            self._can_car_rb.blockSignals(True); self._can_truck_rb.blockSignals(True)
            self._can_car_rb.setChecked(False);  self._can_truck_rb.setChecked(True)
            self._can_car_rb.blockSignals(False); self._can_truck_rb.blockSignals(False)
            self._can_active_cb.setChecked(True); self._on_can_truck_selected()
        elif vt == "OSOBOWY":
            self._can_truck_rb.blockSignals(True); self._can_car_rb.blockSignals(True)
            self._can_truck_rb.setChecked(False);  self._can_car_rb.setChecked(True)
            self._can_truck_rb.blockSignals(False); self._can_car_rb.blockSignals(False)
            self._can_active_cb.setChecked(True);  self._on_can_car_selected()

    @Slot(bool)
    def _on_can_active_changed(self, active: bool):
        self._set_can_controls_enabled(active)
        if active and not self._can_truck_rb.isChecked() and not self._can_car_rb.isChecked():
            self._can_truck_rb.setChecked(True); self._on_can_truck_selected()
        elif not active:
            self._can_truck_rb.setChecked(False); self._can_car_rb.setChecked(False)
            for cb in self._can_cbs:
                if cb: cb.setChecked(False)

    def _set_can_controls_enabled(self, e: bool):
        self._can_truck_rb.setEnabled(e); self._can_car_rb.setEnabled(e)
        for cb in self._can_cbs:
            if cb: cb.setEnabled(e)

    @Slot()
    def _on_can_truck_selected(self):
        if not self._can_truck_rb.isChecked(): return
        for i, cb in enumerate(self._can_cbs):
            if cb: cb.setChecked(i < 7)

    @Slot()
    def _on_can_car_selected(self):
        if not self._can_car_rb.isChecked(): return
        for i, cb in enumerate(self._can_cbs):
            if cb: cb.setChecked(i < 4)

    @Slot(str)
    def _on_d8_changed(self, v: str):
        self._tacho_grp.setVisible(v == "Tachoreader")
        self._fmb_grp.setVisible(v == "FMB640/FMC650")

    @Slot(bool)
    def _on_webasto_changed(self, checked: bool):
        self._webasto_din_combo.setEnabled(checked)
        self._webasto_high_rb.setEnabled(checked)
        self._webasto_low_rb.setEnabled(checked)
        if checked:
            if not self._webasto_din_combo.text():
                self._webasto_din_combo.setText("6")
            if not self._webasto_high_rb.isChecked() and not self._webasto_low_rb.isChecked():
                self._webasto_high_rb.setChecked(True)
        else:
            self._webasto_din_combo.setText("")
            self._webasto_level_grp.setExclusive(False)
            self._webasto_high_rb.setChecked(False)
            self._webasto_low_rb.setChecked(False)
            self._webasto_level_grp.setExclusive(True)

    @Slot(str)
    def _on_din_function_changed(self, text: str, rd: dict):
        needs = any(kw in text.lower() for kw in DIN_NEEDS_SN_KEYWORDS)
        rd["sn_edit"].setVisible(needs)
        if not needs:
            rd["sn_edit"].clear()
        if text.strip():
            if not rd["high_rb"].isChecked() and not rd["low_rb"].isChecked():
                rd["high_rb"].setChecked(True)
        else:
            rd["din_type"].setText("")
            rd["level_grp"].setExclusive(False)
            rd["high_rb"].setChecked(False)
            rd["low_rb"].setChecked(False)
            rd["level_grp"].setExclusive(True)

    def build_config_json(self, rec: ServiceRecord) -> dict:
        conn = CAN_CONNECTION_TRUCK if self._can_truck_rb.isChecked() else \
               CAN_CONNECTION_CAR   if self._can_car_rb.isChecked() else ""
        can_cfg = {"isCan": "true" if self._can_active_cb.isChecked() else "",
                   "canConnection": conn}
        for i, key in enumerate(CAN_JSON_KEYS):
            cb = self._can_cbs[i]
            can_cfg[key] = "tak" if (cb and cb.isChecked()) else ""

        din_cfg = {}
        if self._webasto_cb.isChecked():
            din_cfg["din1"] = {"nazwa":"webasto", "bit":self._webasto_din_combo.text(),
                               "stan":"wysoki" if self._webasto_high_rb.isChecked() else "niski"}
                               
        for i, (key, rd) in enumerate(zip(["din2","din3","din4","din5"], self._din_rows)):
            if rd["func"].text():
                din_cfg[key] = {"nazwa":rd["func"].text(), "bit":rd["din_type"].text(),
                                "stan":"wysoki" if rd["high_rb"].isChecked() else "niski",
                                "sn":rd["sn_edit"].text()}

        add_cfg = {}
        if self._immo_cb.isChecked(): add_cfg["immo"] = "1"
        if self._rfid_cb.isChecked(): add_cfg["rfid"] = "1"
        
        add_cfg["ccid"] = self._ccid_edit.text().strip()
        if self._przek_lbl_w.isVisible():
            add_cfg["przekladkaRej"] = self._przek_rej_edit.text()

        return {"canConfig":can_cfg, "dinConfig":din_cfg, "additionalConfig":add_cfg,
                "tabletZaznaczony":"true" if self._tablet_cb.isChecked() else "false",
                "tabletKomentarz":self._tablet_nr_edit.text(),
                "tabletZasilanie":"1" if self._power_cb.isChecked() else "0",
                "rfidZaznaczony":"true" if self._rfid_cb.isChecked() else "false",
                "immoZaznaczony":"true" if self._immo_cb.isChecked() else "false"}

    def get_json_string(self) -> str:
        tmp = ServiceRecord()
        self.collect_to_record(tmp)
        
        date_str = ""
        if tmp.service_date:
            parts = tmp.service_date.split("-")
            if len(parts) == 3:
                date_str = f"{parts[2]}.{parts[1]}.{parts[0]}"
            else:
                date_str = tmp.service_date

        d8_val = self._d8_combo.currentText()
        model_tacho = ""
        wersja_tacho = ""
        if d8_val == "Tachoreader":
            model_tacho = "Siemens" if self._rb_siemens.isChecked() else "Stonerige" if self._rb_stonerige.isChecked() else ""
            wersja_tacho = self._tacho_ver.text().strip()
        elif d8_val == "FMB640/FMC650":
            model_tacho = "Siemens" if self._rb_tel_s.isChecked() else "Stoneridge" if self._rb_tel_sr.isChecked() else "Inne" if self._rb_tel_i.isChecked() else ""
            wersja_tacho = self._tacho_fmb_ver.text().strip()

        add_cfg = tmp.config_json.get("additionalConfig", {})
        
        marka_parts = tmp.vehicle_brand.split(" ", 1) if tmp.vehicle_brand else ["", ""]
        marka = marka_parts[0]
        model = " ".join(marka_parts[1:]) if len(marka_parts) > 1 else ""

        full = {
            "data": date_str,
            "typ": tmp.record_type.strip() if tmp.record_type else "",
            "firma": tmp.company_name,
            "flota": tmp.fleet_name,
            "nrRejestracyjny": tmp.license_plate,
            "nrBoczny": tmp.side_number,
            "id": tmp.device_id,
            "sim": tmp.sim_number,
            "ccid": add_cfg.get("ccid", ""),
            "marka": marka,
            "model": model,
            "typPojazdu": tmp.vehicle_type,
            "modelUrzadzenia": tmp.device_model,
            "monter": tmp.technician_name,
            "godzina": str(tmp.service_hour or 0),
            "minuta": str(tmp.service_minute or 0),
            "d8": d8_val,
            "modelTacho": model_tacho,
            "wersjaTacho": wersja_tacho,
            "gdzieRejestrator": tmp.recorder_location,
            "przebieg": str(tmp.mileage) if tmp.mileage is not None else "",
            "an0Numer": tmp.probe1_id,
            "an0Pojemnosc": tmp.config_json.get("sondyRaw", {}).get("an0Pojemnosc", ""),
            "an0Skalowanie": tmp.config_json.get("sondyRaw", {}).get("an0Skalowanie", ""),
            "an1Numer": tmp.probe2_id,
            "an1Pojemnosc": tmp.config_json.get("sondyRaw", {}).get("an1Pojemnosc", ""),
            "an1Skalowanie": tmp.config_json.get("sondyRaw", {}).get("an1Skalowanie", ""),
            "prawyZbiornik": tmp.right_tank_probe,
            "komentarzDoProtokolu": tmp.comment,
            "komentarzPrywatny": tmp.config_json.get("komentarzPrywatny", ""),
            "tablet": tmp.has_tablet,
            "tabletNr": tmp.tablet_sn,
            "zasilanie": tmp.has_power,
            "rfid": tmp.has_rfid,
            "immo": tmp.has_immo,
            "canConfig": tmp.config_json.get("canConfig", {}),
            "dinConfig": tmp.config_json.get("dinConfig", {}),
            "odebrane": tmp.config_json.get("odebrane", False),
            "dyzur": tmp.config_json.get("dyzurZaznaczony", False),
            "czasDyzuru": tmp.duty_time_min or "",
            "przekladkaZ": add_cfg.get("przekladkaRej", "")
        }
        return json.dumps(full, ensure_ascii=False, indent=2)

    def load_from_record(self, rec: ServiceRecord):
        was = self._initialized; self._initialized = False
        try:
            if rec.service_date:
                d = QDate.fromString(rec.service_date, "yyyy-MM-dd")
                if d.isValid(): self._date_edit.setDate(d)
            if self._edit_mode:
                self._time_edit.setTime(QTime(rec.service_hour or 0, rec.service_minute or 0))
            self._company_edit.setText(rec.company_name)
            self._fleet_name_edit.setText(rec.fleet_name) 
            self._plate_edit.setText(rec.license_plate)
            self._side_edit.setText(rec.side_number)
            self._device_id_edit.setText(rec.device_id)
            self._sim_edit.setText(rec.sim_number)
            self._brand_edit.setText(rec.vehicle_brand) 
            self._vehicle_type_combo.setText(rec.vehicle_type)
            self._mileage_edit.setText(str(rec.mileage) if rec.mileage else "")
            self._device_model_combo.setText(rec.device_model)
            self._technician_combo.setText(rec.technician_name)
            self._comment_edit.setPlainText(rec.comment)
            self._private_comment_edit.setPlainText(rec.config_json.get("komentarzPrywatny",""))
            self._duty_time_edit.setText(str(rec.duty_time_min) if rec.duty_time_min else "")
            self._recorder_loc_edit.setText(rec.recorder_location or "")
            self._odebrane_cb.setChecked(rec.config_json.get("odebrane", False))
            
            ft_text = (rec.fleet_name or "").strip().upper()
            self._current_fleet_type = "VIP" if ("TAURON" in ft_text or "PGE" in ft_text) else "Zwykłe"
            self._reload_din_functions(rec.fleet_name or "")
            
            for btn in self._typ_grp.buttons(): btn.setChecked(False)
            typ_val = rec.record_type.strip() if rec.record_type else ""
            if typ_val in self._typ_rbs:
                self._typ_rbs[typ_val].setChecked(True)
                if typ_val == "Przekładka":
                    self._przek_lbl_w.setVisible(True)
                    self._przek_rej_edit.setVisible(True)
            if rec.firmware_tacho:
                parts = rec.firmware_tacho.split(" ",1)
                brand = parts[0]; ver = parts[1] if len(parts)>1 else ""
                if brand in TACHO_BRANDS_TACHOREADER:
                    self._d8_combo.setCurrentText("Tachoreader")
                    if brand=="Siemens": self._rb_siemens.setChecked(True)
                    else: self._rb_stonerige.setChecked(True)
                    self._tacho_ver.setText(ver)
                elif brand in TACHO_BRANDS_FMB640:
                    self._d8_combo.setCurrentText("FMB640/FMC650")
                    if brand=="Siemens": self._rb_tel_s.setChecked(True)
                    elif brand=="Stoneridge": self._rb_tel_sr.setChecked(True)
                    else: self._rb_tel_i.setChecked(True)
                    self._tacho_fmb_ver.setText(ver)
            can_cfg = rec.config_json.get("canConfig",{})
            is_can = can_cfg.get("isCan")=="true"
            self._can_active_cb.setChecked(is_can)
            self._set_can_controls_enabled(is_can)
            conn = can_cfg.get("canConnection","")
            if conn==CAN_CONNECTION_TRUCK: self._can_truck_rb.setChecked(True)
            elif conn==CAN_CONNECTION_CAR: self._can_car_rb.setChecked(True)
            for i, key in enumerate(CAN_JSON_KEYS):
                cb = self._can_cbs[i]
                if cb: cb.setChecked(can_cfg.get(key,"")=="tak")
            din_cfg = rec.config_json.get("dinConfig",{})
            d1 = din_cfg.get("din1",{})
            if d1:
                self._webasto_cb.setChecked(True)
                self._webasto_din_combo.setText(str(d1.get("bit","")))
                if d1.get("stan")=="wysoki": self._webasto_high_rb.setChecked(True)
                else: self._webasto_low_rb.setChecked(True)
            for i, key in enumerate(["din2","din3","din4","din5"]):
                d = din_cfg.get(key,{})
                if d and i<len(self._din_rows):
                    r = self._din_rows[i]
                    r["func"].setText(d.get("nazwa",""))
                    r["din_type"].setText(d.get("bit",""))
                    if d.get("stan")=="wysoki": r["high_rb"].setChecked(True)
                    else: r["low_rb"].setChecked(True)
                    r["sn_edit"].setText(d.get("sn",""))
            add_cfg = rec.config_json.get("additionalConfig",{})
            self._immo_cb.setChecked(add_cfg.get("immo")=="1")
            self._rfid_cb.setChecked(add_cfg.get("rfid")=="1")
            self._tablet_cb.setChecked(rec.has_tablet)
            self._tablet_nr_edit.setText(rec.tablet_sn)
            self._power_cb.setChecked(rec.has_power)
            self._ccid_edit.setText(add_cfg.get("ccid", ""))
            if "przekladkaRej" in add_cfg:
                self._przek_rej_edit.setText(add_cfg.get("przekladkaRej", ""))
            elif "przekladkaFirma" in add_cfg:
                self._przek_rej_edit.setText(add_cfg.get("przekladkaFirma", ""))
            self._probe1_id.setText(rec.probe1_id)
            
            def get_probe_load(key, float_val):
                raw = rec.config_json.get("sondyRaw", {})
                if key in raw:
                    return raw[key]
                if not float_val:
                    return ""
                try:
                    return str(int(float_val)) if float(float_val).is_integer() else str(float_val)
                except (ValueError, TypeError):
                    return str(float_val)

            self._probe1_cap.setText(get_probe_load("an0Pojemnosc", rec.probe1_capacity))
            self._probe1_len.setText(get_probe_load("an0Skalowanie", rec.probe1_length))
            self._probe2_id.setText(rec.probe2_id)
            self._probe2_cap.setText(get_probe_load("an1Pojemnosc", rec.probe2_capacity))
            self._probe2_len.setText(get_probe_load("an1Skalowanie", rec.probe2_length))
            prawy = rec.right_tank_probe
            if prawy=="An0": self._rb_prawy_an0.setChecked(True)
            elif prawy=="An1": self._rb_prawy_an1.setChecked(True)
            else: self._rb_prawy_brak.setChecked(True)
            
            dyzur_zapisany = rec.config_json.get("dyzurZaznaczony")
            if dyzur_zapisany is not None:
                self._duty_cb.setChecked(dyzur_zapisany)
            else:
                is_weekend = False
                if rec.service_date:
                    d = QDate.fromString(rec.service_date, "yyyy-MM-dd")
                    if d.isValid() and d.dayOfWeek() >= 6:
                        is_weekend = True
                
                h, m = rec.service_hour or 0, rec.service_minute or 0
                is_duty_time = h >= 15 or h < 6 or (h == 6 and m <= 55)
                self._duty_cb.setChecked(is_weekend or is_duty_time)

        finally:
            self._initialized = was

    def collect_to_record(self, rec: ServiceRecord) -> Tuple[bool,str]:
        if not any([
            self._company_edit.text().strip(),
            self._fleet_name_edit.text().strip(),
            self._plate_edit.text().strip(),
            self._przek_rej_edit.text().strip(),
            self._side_edit.text().strip(),
            self._device_id_edit.text().strip(),
            self._sim_edit.text().strip(),
            self._ccid_edit.text().strip(),
            self._brand_edit.text().strip(),
            self._vehicle_type_combo.text().strip(),
            self._device_model_combo.text().strip(),
            self._technician_combo.text().strip(),
            self._recorder_loc_edit.text().strip(),
            self._mileage_edit.text().strip(),
            self._duty_time_edit.text().strip(),
            self._tablet_nr_edit.text().strip(),
            self._probe1_id.text().strip(),
            self._probe2_id.text().strip(),
            self._comment_edit.toPlainText().strip(),
            self._private_comment_edit.toPlainText().strip(),
        ]):
            return False, "Wypełnij co najmniej jedno pole poza datą i godziną."
        rec.service_date   = self._date_edit.date().toString("yyyy-MM-dd")
        rec.service_hour   = self._time_edit.time().hour()
        rec.service_minute = self._time_edit.time().minute()
        rec.company_name   = self._company_edit.text().strip()
        rec.fleet_name     = self._fleet_name_edit.text().strip() 
        rec.license_plate  = self._plate_edit.text().strip().upper()
        rec.side_number    = self._side_edit.text().strip()
        rec.device_id      = self._device_id_edit.text().strip()
        rec.sim_number     = self._sim_edit.text().strip()
        rec.vehicle_brand  = self._brand_edit.text().strip() 
        rec.vehicle_type   = self._vehicle_type_combo.text()
        m = self._mileage_edit.text().strip()
        rec.mileage        = int(m) if m.isdigit() else None
        rec.device_model   = self._device_model_combo.text().strip()
        rec.technician_name = self._technician_combo.text().strip()
        rec.comment        = self._comment_edit.toPlainText().strip()
        rec.duty_time_min  = self._duty_time_edit.text().strip()
        rec.recorder_location = self._recorder_loc_edit.text().strip()
        typ_names = ["Montaż", "Upgrade", "Przekładka", "Serwis", "Telefon"]
        rec.record_type = " "
        for name in typ_names:
            if self._typ_rbs[name].isChecked():
                rec.record_type = name; break
        d8 = self._d8_combo.currentText()
        if d8=="Tachoreader":
            brand = "Siemens" if self._rb_siemens.isChecked() else \
                    "Stonerige" if self._rb_stonerige.isChecked() else ""
            rec.firmware_tacho = f"{brand} {self._tacho_ver.text().strip()}".strip()
        elif d8=="FMB640/FMC650":
            brand = "Siemens" if self._rb_tel_s.isChecked() else \
                    "Stoneridge" if self._rb_tel_sr.isChecked() else \
                    "Inne" if self._rb_tel_i.isChecked() else ""
            rec.firmware_tacho = f"{brand} {self._tacho_fmb_ver.text().strip()}".strip()
        else:
            rec.firmware_tacho = ""
        rec.can_active = self._can_active_cb.isChecked()
        rec.can_vehicle_type = "Ciężarowy" if self._can_truck_rb.isChecked() else \
                               "Osobowy"   if self._can_car_rb.isChecked() else ""
        rec.can_checkboxes = [cb.isChecked() if cb else False for cb in self._can_cbs]
        for i, row in enumerate([rec.din1, rec.din2, rec.din3]):
            rd = self._din_rows[i]
            row.function = rd["func"].text()
            row.din_type = rd["din_type"].text()
            row.level_high = rd["high_rb"].isChecked()
            row.level_low  = rd["low_rb"].isChecked()
            row.serial_number = rd["sn_edit"].text().strip()
        rec.has_rfid  = self._rfid_cb.isChecked()
        rec.has_immo  = self._immo_cb.isChecked()
        rec.has_tablet = self._tablet_cb.isChecked()
        rec.tablet_sn  = self._tablet_nr_edit.text().strip()
        rec.has_power  = self._power_cb.isChecked()
        rec.probe1_id = self._probe1_id.text().strip()
        rec.probe2_id = self._probe2_id.text().strip()
        for attr, ww in [("probe1_capacity",self._probe1_cap),("probe1_length",self._probe1_len),
                         ("probe2_capacity",self._probe2_cap),("probe2_length",self._probe2_len)]:
            try: setattr(rec, attr, float(ww.text()) if ww.text().strip() else None)
            except ValueError: setattr(rec, attr, None)
        if self._rb_prawy_an0.isChecked():   rec.right_tank_probe = "An0"
        elif self._rb_prawy_an1.isChecked(): rec.right_tank_probe = "An1"
        else:                                rec.right_tank_probe = ""
        
        rec.config_json = self.build_config_json(rec)
        rec.config_json["komentarzPrywatny"] = self._private_comment_edit.toPlainText().strip()
        rec.config_json["odebrane"] = self._odebrane_cb.isChecked()
        rec.config_json["dyzurZaznaczony"] = self._duty_cb.isChecked()
        
        rec.config_json["sondyRaw"] = {
            "an0Pojemnosc": self._probe1_cap.text().strip(),
            "an0Skalowanie": self._probe1_len.text().strip(),
            "an1Pojemnosc": self._probe2_cap.text().strip(),
            "an1Skalowanie": self._probe2_len.text().strip(),
        }
        
        return True, ""