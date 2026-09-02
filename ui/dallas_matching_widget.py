"""
ui/dallas_matching_widget.py
------------------------------
Dopasowanie listy pojazdów firmy (załącznik xlsx/xls) z eksportem urządzeń
z serwera (csv) po ID rejestratora / Imei. Bez zapisu do bazy - stan żyje
tylko w oknie, dopóki jest otwarte.
"""
import base64
import csv
import json
import os
import re
import urllib.parse
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog, QMessageBox, QApplication, QDialog, QMenu,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle,
    QListWidget, QListWidgetItem, QLayout, QSizePolicy,
)
from PySide6.QtCore import Qt, QPoint, QUrl, QTimer, QRect, QSize
from PySide6.QtGui import QColor, QBrush, QDesktopServices

from database.db_manager import DatabaseManager
from ui.dallas_import import parse_attachment, parse_server_export, build_matched_rows, _normalize_sim
from ui.main_window import FilterableHeaderView, ColumnFilterPopup

_DEFAULT_PANEL_KEY = "Panel - GPS"
_STARS_SMS_TEXT = "PIN= DALLS=**************** RESTART"
_CLEAR_STARS_SMS_TEXT = "PIN= DALLAS= RESTART"
_STARS_CMD_ON_TEXT = (
    "BQABAAADxPo=:iQBEACoqKioqKioqKioqKioqKioAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA^"
)
_STARS_CMD_OFF_TEXT = (
    "BQABAAADxPo=:iQBEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA^"
)


def _file_mtime_label(path: str) -> str:
    """Data modyfikacji pliku (kiedy został zapisany/wyeksportowany) - żeby było
    widać w aplikacji, czy wczytany eksport nie jest przypadkiem nieaktualny."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return ""
    return datetime.fromtimestamp(mtime).strftime("%d.%m.%Y %H:%M")


def _truncate_filename(name: str, limit: int = 20) -> str:
    """Skraca długą nazwę pliku do `limit` znaków, dopisując '..{rozszerzenie}' -
    żeby w etykietach zostało miejsce na resztę informacji (data, brak w eksporcie itd.)."""
    if len(name) <= limit:
        return name
    ext = os.path.splitext(name)[1].lstrip(".")
    return f"{name[:limit]}..{ext}" if ext else f"{name[:limit]}.."


_FLASH_ROLE = Qt.UserRole + 3  # ta sama konwencja co RowColorDelegate w tabeli głównej


class _RowBgDelegate(QStyledItemDelegate):
    """Wymusza uwzględnienie Qt.BackgroundRole - globalny styl aplikacji definiuje
    QTableWidget::item, co w Qt powoduje całkowite zignorowanie item.setBackground()
    ustawianego programowo, dopóki delegat nie namaluje tła ręcznie (jak w tabeli głównej).

    Miganie (kopiowanie do schowka) NIE nadpisuje Qt.BackgroundRole - to osobna,
    tymczasowa flaga (_FLASH_ROLE) sprawdzana tutaj przy malowaniu. Dzięki temu
    "prawdziwy" kolor wiersza (dopasowanie CCRC itp.) nigdy nie jest nadpisywany
    i nie gubi się przy odświeżaniu kolorów w trakcie migania.

    Zaznaczenie/hover malowane DOKŁADNIE tak samo jak w tabeli głównej (RowColorDelegate
    w main_window.py) - płaski kolor nadpisujący tło wiersza, bez przyciemniania i ramki,
    żeby zachowanie było spójne w całej aplikacji."""

    def __init__(self, parent=None, is_light: bool = False):
        super().__init__(parent)
        self._is_light = is_light

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.backgroundBrush = QBrush(Qt.NoBrush)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        bg = index.data(Qt.BackgroundRole)

        if index.data(_FLASH_ROLE):
            # MAGIA: tak jak w tabeli głównej - okłamujemy system, że komórka nie jest
            # zaznaczona/najechana, żeby czysty kolor migania zawsze przebił się na wierch.
            opt.state &= ~QStyle.State_Selected
            opt.state &= ~QStyle.State_MouseOver
            painter.fillRect(opt.rect, QColor("#4ade80"))
            opt.backgroundBrush = QBrush(Qt.NoBrush)
        else:
            is_selected = bool(opt.state & QStyle.State_Selected)
            is_hover = bool(opt.state & QStyle.State_MouseOver)
            # bg jest QBrush (tak zapisuje je item.setBackground()) - QColor(QBrush) rzuca
            # wyjątkiem ("QVariant must be holding a QColor"), trzeba wyciągnąć kolor z brusha.
            if self._is_light:
                if is_selected:
                    color = QColor("#cbd5e1")
                elif is_hover:
                    color = QColor("#e2e8f0")
                else:
                    color = QBrush(bg).color() if bg else QColor("#ffffff")
            else:
                if is_selected:
                    color = QColor("#333847")
                elif is_hover:
                    color = QColor("#22262f")
                else:
                    color = QBrush(bg).color() if bg else QColor("#1a1d23")

            opt.state &= ~QStyle.State_Selected
            opt.state &= ~QStyle.State_MouseOver
            painter.fillRect(opt.rect, color)
            opt.backgroundBrush = QBrush(Qt.NoBrush)

        super().paint(painter, opt, index)

_COLUMNS = [
    ("Nr rejestracyjny", "plate"),
    ("ID rejestratora", "device_id"),
    ("SIM (załącznik)", "sim"),
    ("Producent", "manufacturer"),
    ("Model", "model"),
    ("Firmware", "firmware"),
    ("CCRC", "ccrc"),
    ("Firmware status", "firmware_status"),
    ("Ostatnia data GPS", "last_gps"),
    ("DALLAS", "dallas"),
    ("Data błędu wysyłania listy", "dallas_error_date"),
    ("Status", "status"),
]

_ROW_DATA_ROLE = Qt.UserRole + 1

# Kolumny, których kliknięcie kopiuje wartość komórki do schowka.
_COPY_ATTRS = frozenset({"plate", "device_id", "sim", "ccrc"})

# Pamięć w procesie (NIE w bazie danych) - każde nowo otwarte okno "Podgrywanie Dallas"
# startuje od ostatnio wczytanych danych/wpisanych CCRC, dopóki cała aplikacja nie zostanie
# zamknięta (restart aplikacji = czysty stan, tak jak wcześniej). Współdzielona między
# wszystkimi (niezależnymi) instancjami DallasMatchingWidget w ramach jednego procesu.
_SESSION_STATE: dict = {}

# W przeciwieństwie do reszty stanu (dane wczytanych plików, wpisane CCRC), układ kolumn
# (kolejność/widoczność) i "Koloruj wg CCRC" to preferencja UI, nie dane klienta - więc
# ta jedna rzecz jest trwale zapamiętywana w bazie (Ustawienia aplikacji), tak samo jak
# układ kolumn w tabeli głównej. Przetrwa restart aplikacji, nie tylko zamknięcie okna.
_COL_SETTINGS_KEY = "dallas_columns_config"

_JOB_TYPE_DALLAS = "Wgranie listy kart kierowców"
# Organizacja różni się treścią między panelami (PGE: "Addsecure Poland", Tauron: "Addsecure"),
# ale w każdym panelu lista ma tylko jedną pozycję - skrypt w przeglądarce wybiera ją
# samodzielnie (pierwszą/jedyną z listy), więc nie musimy tu znać dokładnej nazwy.

# Firmware 4.002.x / 4.003.x wymaga listy w formacie "binary", pozostałe - "text".
_BINARY_FW_PREFIXES = ("4.002", "4.003")


def _split_values(text: str) -> set:
    return {v for v in re.split(r"[,;\s]+", text.strip()) if v}


def _firmware_category(firmware: str) -> str:
    fw = (firmware or "").strip()
    return "binary" if fw.startswith(_BINARY_FW_PREFIXES) else "text"


_SKAUT1_MAJOR_RE = re.compile(r"^\D*0*(\d+)\.\d+\.(\d+)$")
_SKAUT10_ID_PREFIXES = ("s10-s", "s10-f")

# Wartości spotykane w załącznikach zamiast prawdziwego ID rejestratora, gdy pojazd
# go nie ma (np. "brak", czasem z resztką cudzysłowu/apostrofu jak "brak'", albo sama
# kropka "."). Traktujemy je tak samo jak pustą komórkę.
_MISSING_ID_TOKENS = {"brak", "."}


def _is_missing_device_id(device_id: str) -> bool:
    dev = (device_id or "").strip().strip("'\"").strip().lower()
    return not dev or dev in _MISSING_ID_TOKENS


def _is_skaut1_firmware(firmware: str, device_id: str = "") -> bool:
    """Firmware 1.XXX.XXX = Skaut1. UWAGA - dwie pułapki:
    - 10.XXX.XXX (i 11., 12. itd.) to NIE Skaut1, dlatego sprawdzamy dokładnie
      pierwszy segment wersji (liczbowo, ignorując ewentualny prefiks/wiodące zera).
    - Skaut10 też ma firmware zaczynające się od "1." (np. 1.003.0486!), więc dodatkowo
      odróżniamy go po: (a) prefiksie ID rejestratora "S10-S"/"S10-F", oraz (b) trzecim
      segmencie wersji - Skaut1 ma 3 cyfry (np. "162"), Skaut10 ma 4 cyfry (np. "0486")."""
    fw = (firmware or "").strip()
    if not fw:
        return False
    m = _SKAUT1_MAJOR_RE.match(fw)
    if not m or m.group(1) != "1" or len(m.group(2)) != 3:
        return False
    dev_id = (device_id or "").strip().lower()
    if dev_id.startswith(_SKAUT10_ID_PREFIXES):
        return False
    return True


def _is_non_setivo(row: dict) -> bool:
    """Podgrywanie kart Dallas obsługuje wyłącznie rejestratory Setivo - kolumna
    'Producent' z eksportu serwera pozwala wykryć inne marki (Albatros, Teltonika,
    Topfly, Queclink, Meitrack...), dla których ta funkcjonalność nie ma zastosowania."""
    manufacturer = (row.get("manufacturer") or "").strip()
    return bool(manufacturer) and manufacturer.lower() != "setivo"


# TYMCZASOWE: na panelu Tauron filtrowanie rejestratora w kroku "Urządzenia" nie działa,
# więc wysyłamy tam jedno zadanie na urządzenie po kolei, zamiast jednego zadania z
# wieloma IMEI. gps.framelogic.pl miał tę samą przypadłość, ale dostał serwerową
# aktualizację z przyciskiem "jobs.addToList", który naprawia to u źródła - USUNIĘTE stąd
# (patrz needsPerDeviceWorkaround() / supportsAddToListButton() w skrypcie Tampermonkey
# "Ponowne wgranie listy DALLAS" - musi być zaktualizowany w komplecie z tą stałą, inaczej
# aplikacja i skrypt się rozjadą, tak jak się rozjechały przy wersji 1.9).
_PER_DEVICE_FLEETS = frozenset({"tauron"})
_PER_DEVICE_URL_HOSTS = ("10.1.255.124",)


def _build_macro_url(jobs_url: str, imeis: list, file_name: str, org: str | None = None,
                      per_device: bool = False) -> str:
    """jobs_url - pełny link do sekcji zadań danej floty (słownik Linki flot,
    klucz "Panel - {flota} (zadania)"), np. "https://fmgps.gkpge.pl/#/job".
    Dopisujemy tylko '?macro=...' PRZED fragmentem hash - reszta linku (w tym
    dokładna ścieżka po '#') pozostaje dokładnie taka, jak zapisana w słowniku."""
    payload = {
        "imeis": imeis, "file": file_name, "org": org,
        "type": _JOB_TYPE_DALLAS, "perDevice": per_device,
    }
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    query = urllib.parse.urlencode({"macro": encoded})
    if "#" in jobs_url:
        base, hash_part = jobs_url.split("#", 1)
        hash_part = "#" + hash_part
    else:
        base, hash_part = jobs_url, ""
    base = base.rstrip("/")
    return f"{base}/?{query}{hash_part}"


class FlowLayout(QLayout):
    """Układ 'zawijający' - rozmieszcza dzieci w rzędzie i automatycznie przenosi
    kolejne do nowej linii, gdy brakuje szerokości (jak zawijanie tekstu, ale dla
    widgetów). Standardowy wzorzec Qt (Flow Layout Example), potrzebny tu, żeby
    rząd przycisków filtrów mógł się zwężać bez ucinania/ściskania tekstu -
    zamiast tego przyciski przechodzą do kolejnej linii."""

    def __init__(self, parent=None, margin: int = 0, h_spacing: int = 6, v_spacing: int = 6):
        super().__init__(parent)
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._items: list = []
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def horizontalSpacing(self) -> int:
        return self._h_spacing

    def verticalSpacing(self) -> int:
        return self._v_spacing

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y = effective.x(), effective.y()
        line_height = 0

        for item in self._items:
            widget = item.widget()
            if widget is not None and not widget.isVisibleTo(widget.parentWidget() or widget):
                continue
            item_size = item.sizeHint()
            next_x = x + item_size.width() + self._h_spacing
            if next_x - self._h_spacing > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y += line_height + self._v_spacing
                next_x = x + item_size.width() + self._h_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))
            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y() + margins.bottom()


class _ColumnManagerPopup(QDialog):
    """Popup do zarządzania kolumnami tabeli: pokaż/ukryj, zmiana kolejności (drag&drop
    lub przyciski ▲▼), plus opcja 'Koloruj wg CCRC' (przeniesiona tu z głównego widoku).
    Wizualnie spójny z zakładką Ustawienia -> Tabela główna -> Kolumny."""

    def __init__(self, attrs_in_order: list, hidden_attrs: set, ccrc_checked: bool,
                 is_light: bool = False, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.Tool)
        self.setMinimumWidth(420)
        self.setMinimumHeight(620)

        from ui.settings_window import _ColCheckDelegate

        border = "#cbd5e1" if is_light else "#3a4150"
        bg = "#ffffff" if is_light else "#1a1d23"
        bg2 = "#f8fafc" if is_light else "#0f1115"
        fg = "#0f172a" if is_light else "#e2e8f0"
        hdr_bg = "#f1f5f9" if is_light else "#22262f"
        btn_bg = "#f1f5f9" if is_light else "#333847"
        btn_hov = "#e2e8f0" if is_light else "#3d4457"
        muted = "#64748b" if is_light else "#94a3b8"

        self.setStyleSheet(f"""
            _ColumnManagerPopup {{ background-color: {bg}; border: 1px solid {border}; }}
            QLabel#hdr {{
                background-color: {hdr_bg}; color: {fg}; font-weight: 600; font-size: 8.5pt;
                padding: 5px 8px; border-bottom: 1px solid {border};
            }}
            QLabel {{ color: {fg}; }}
            QListWidget {{ background-color: {bg2}; color: {fg}; border: 1px solid {border};
                border-radius: 4px; outline: none; font-size: 9pt; }}
            QListWidget::item {{ padding: 4px 6px; }}
            QListWidget::indicator {{ width: 16px; height: 16px; background: transparent; border: none; }}
            QListWidget::indicator:checked {{ background: transparent; border: none; }}
            QCheckBox {{ color: {fg}; }}
            QPushButton {{
                background-color: {btn_bg}; color: {fg};
                border: 1px solid {border}; border-radius: 4px;
                padding: 4px 12px; min-height: 24px;
            }}
            QPushButton:hover {{ background-color: {btn_hov}; }}
            QPushButton#btn_primary {{ background-color: #3b82f6; color: #ffffff; border-color: #2563eb; }}
            QPushButton#btn_primary:hover {{ background-color: #2563eb; }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 10)
        lay.setSpacing(0)

        hdr_lbl = QLabel("Ustawienia")
        hdr_lbl.setObjectName("hdr")
        lay.addWidget(hdr_lbl)

        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(10, 10, 10, 0)
        inner_lay.setSpacing(8)
        lay.addWidget(inner, 1)

        self._ccrc_check = QCheckBox("Koloruj wg CCRC")
        self._ccrc_check.setChecked(ccrc_checked)
        # Domyślny styl aplikacji nie rysuje "ptaszka" (tylko subtelną zmianę tła) -
        # tutaj potrzebny jest jednoznaczny, kontrastowy stan zaznaczenia.
        self._ccrc_check.setStyleSheet(
            f"QCheckBox{{color:{fg};font-size:8.5pt;}}"
            "QCheckBox::indicator{width:13px;height:13px;border-radius:3px;"
            f"border:1.5px solid {border};background:{bg2};}}"
            "QCheckBox::indicator:checked{background:#22c55e;border-color:#16a34a;}"
        )
        inner_lay.addWidget(self._ccrc_check)

        hint = QLabel("Zaznacz widoczne kolumny · przeciągaj lub użyj przycisków ▲▼ aby zmienić kolejność")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{muted}; font-size:8.5pt;")
        inner_lay.addWidget(hint)

        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.InternalMove)
        self._list.setDefaultDropAction(Qt.MoveAction)
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setItemDelegate(_ColCheckDelegate(is_light, self._list))
        inner_lay.addWidget(self._list, 1)

        label_by_attr = {attr: label for label, attr in _COLUMNS}
        for attr in attrs_in_order:
            item = QListWidgetItem(label_by_attr.get(attr, attr))
            item.setData(Qt.UserRole, attr)
            item.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsSelectable
                | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled
            )
            item.setCheckState(Qt.Unchecked if attr in hidden_attrs else Qt.Checked)
            self._list.addItem(item)

        move_row = QHBoxLayout()
        btn_up = QPushButton("▲  Wyżej")
        btn_down = QPushButton("▼  Niżej")
        btn_up.clicked.connect(self._move_up)
        btn_down.clicked.connect(self._move_down)
        move_row.addWidget(btn_up)
        move_row.addWidget(btn_down)
        move_row.addStretch()
        btn_all = QPushButton("Zaznacz wszystkie")
        btn_none = QPushButton("Odznacz wszystkie")
        btn_all.clicked.connect(lambda: self._set_all_checked(True))
        btn_none.clicked.connect(lambda: self._set_all_checked(False))
        move_row.addWidget(btn_all)
        move_row.addWidget(btn_none)
        inner_lay.addLayout(move_row)

        btn_apply = QPushButton("Zastosuj kolumny")
        btn_apply.setObjectName("btn_primary")
        btn_apply.setDefault(True)
        btn_apply.clicked.connect(self.accept)
        inner_lay.addWidget(btn_apply)

        bottom_row = QHBoxLayout()
        btn_reset = QPushButton("Domyślne")
        btn_reset.clicked.connect(self._reset_defaults)
        bottom_row.addWidget(btn_reset)
        bottom_row.addStretch()
        btn_cancel = QPushButton("Anuluj")
        btn_cancel.clicked.connect(self.reject)
        bottom_row.addWidget(btn_cancel)
        inner_lay.addLayout(bottom_row)

    def _move_up(self):
        row = self._list.currentRow()
        if row > 0:
            item = self._list.takeItem(row)
            self._list.insertItem(row - 1, item)
            self._list.setCurrentRow(row - 1)

    def _move_down(self):
        row = self._list.currentRow()
        if 0 <= row < self._list.count() - 1:
            item = self._list.takeItem(row)
            self._list.insertItem(row + 1, item)
            self._list.setCurrentRow(row + 1)

    def _set_all_checked(self, state: bool):
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.Checked if state else Qt.Unchecked)

    def _reset_defaults(self):
        self._list.clear()
        label_by_attr = {attr: label for label, attr in _COLUMNS}
        for _, attr in _COLUMNS:
            item = QListWidgetItem(label_by_attr.get(attr, attr))
            item.setData(Qt.UserRole, attr)
            item.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsSelectable
                | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled
            )
            item.setCheckState(Qt.Checked)
            self._list.addItem(item)

    def ccrc_checked(self) -> bool:
        return self._ccrc_check.isChecked()

    def result_order_and_hidden(self) -> tuple:
        order = []
        hidden = set()
        for i in range(self._list.count()):
            item = self._list.item(i)
            attr = item.data(Qt.UserRole)
            order.append(attr)
            if item.checkState() != Qt.Checked:
                hidden.add(attr)
        return order, hidden


class DallasMatchingWidget(QWidget):
    """Import załącznika firmy + eksportu serwera i dopasowanie ich w tabeli."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._db = DatabaseManager.instance()
        self._is_light = self._db.get_setting("theme_mode", "dark") == "light"
        self._attachment_rows: list[dict] | None = None
        self._server_map: dict[str, dict] | None = None
        self._server_fleet: str = ""  # wykryta flota z eksportu serwera (np. "PGE"/"Tauron")
        # Czy _server_fleet pochodzi z domysłu po dopasowanych pojazdach (zwykły panel GPS,
        # gdzie cały eksport miesza wiele firm), a nie z pewnej większości w całym pliku
        # (PGE/Tauron) - steruje tylko treścią etykiety ("wczytano pojazdy z X" zamiast
        # "wczytano X"), żeby było jasne że to tylko domysł, nie oficjalna nazwa floty.
        self._fleet_from_matches: bool = False
        self._server_label_base: str = ""
        self._server_mtime_info: str = ""
        self._rows: list[dict] = []  # aktualnie wyświetlane (po dopasowaniu) wiersze
        self._col_filters: dict[str, set] = {}  # attr -> zbiór dozwolonych wartości
        self._setup_ui()
        self._restore_column_settings()
        self._restore_session_state()

    def _setup_ui(self):
        is_light = self._is_light
        bg = "#f8fafc" if is_light else "#1a1d23"
        panel = "#ffffff" if is_light else "#22262f"
        border = "#cbd5e1" if is_light else "#3a4150"
        text = "#0f172a" if is_light else "#e2e8f0"
        muted = "#64748b" if is_light else "#94a3b8"
        btn_disabled_bg = "#f1f5f9" if is_light else "#1e2229"
        btn_disabled_text = "#94a3b8" if is_light else "#3a4150"
        btn_disabled_border = "#cbd5e1" if is_light else "#252930"
        # Subtelny, "przytłumiony" akcent (tinted, nie solid-fill) dla stanu :checked -
        # ten sam język wizualny co odznaka "Rekordów: N" niżej, żeby rząd przycisków nie
        # był "oczojebny" przy kilku aktywnych filtrach naraz (jak solidne, nasycone tło).
        self._accent_tint_bg = "#dbeafe" if is_light else "#1e3a5f"
        self._accent_tint_fg = "#1d4ed8" if is_light else "#60a5fa"
        self._accent_tint_border = "#93c5fd" if is_light else "#2d5480"
        btn_style = (
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:3px;font-size:8pt;font-weight:600;padding:3px 9px;"
            f"min-height:20px;max-height:20px;}}"
            f"QPushButton:hover{{border-color:#3b82f6;color:#3b82f6;}}"
            f"QPushButton:checked{{background:{self._accent_tint_bg};color:{self._accent_tint_fg};"
            f"border-color:{self._accent_tint_border};}}"
            f"QPushButton:checked:hover{{border-color:#3b82f6;color:#3b82f6;}}"
            f"QPushButton:disabled{{background:{btn_disabled_bg};color:{btn_disabled_text};"
            f"border-color:{btn_disabled_border};}}"
        )
        input_style = (
            f"QLineEdit{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:3px;padding:3px 7px;font-size:8.5pt;}}"
            f"QLineEdit:focus{{border-color:#3b82f6;}}"
        )
        self._ccrc_match_bg = QColor("#bbf7d0" if is_light else "#1f7a44")
        self._fw_status_error_bg = QColor("#fca5a5" if is_light else "#7f1d1d")
        self._dallas_error_bg = QColor("#fca5a5" if is_light else "#7f1d1d")

        self.setStyleSheet(f"QWidget{{background:{bg};}} QLabel{{color:{text};}}")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hint = QLabel(
            "Wczytaj załącznik z listą pojazdów firmy oraz eksport urządzeń z serwera - "
            "dane zostaną dopasowane po ID rejestratora. Można później wczytać nowy "
            "eksport z serwera (np. po zaktualizowaniu CCRC) - dopasowanie odświeży się "
            "automatycznie, bez ponownego wczytywania załącznika."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{muted}; font-size:8pt;")
        root.addWidget(hint)

        small_label_style = f"color:{muted}; font-size:8pt;"
        small_input_style = (
            f"QLineEdit{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:3px;padding:2px 6px;font-size:8.5pt;}}"
            f"QLineEdit:focus{{border-color:#3b82f6;}}"
        )

        top_row = QHBoxLayout()
        top_row.setSpacing(14)

        pick_col = QVBoxLayout()
        pick_col.setSpacing(6)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(10)
        self._btn_pick_attachment = QPushButton("📎  Wybierz załącznik (lista pojazdów)")
        self._btn_pick_attachment.setStyleSheet(btn_style)
        pick_row.addWidget(self._btn_pick_attachment)
        self._lbl_attachment = QLabel("brak pliku")
        self._lbl_attachment.setStyleSheet(f"color:{muted}; font-size:8pt;")
        pick_row.addWidget(self._lbl_attachment, 1)
        pick_col.addLayout(pick_row)

        pick_row2 = QHBoxLayout()
        pick_row2.setSpacing(10)
        self._btn_pick_server = QPushButton("🖥  Wybierz eksport z serwera")
        self._btn_pick_server.setStyleSheet(btn_style)
        pick_row2.addWidget(self._btn_pick_server)
        self._lbl_server = QLabel("brak pliku")
        self._lbl_server.setStyleSheet(f"color:{muted}; font-size:8pt;")
        pick_row2.addWidget(self._lbl_server, 1)
        pick_col.addLayout(pick_row2)

        top_row.addLayout(pick_col, 1)

        # Mały, subtelny panel z "aktualnymi" CCRC - jedno pole pod drugim.
        current_col = QVBoxLayout()
        current_col.setSpacing(3)

        gear_row = QHBoxLayout()
        gear_row.setSpacing(8)
        gear_row.addStretch()
        self._btn_columns = QPushButton("Ustawienia ⚙")
        self._btn_columns.setStyleSheet(btn_style)
        self._btn_columns.setToolTip(
            "Zarządzaj kolumnami tabeli (pokaż/ukryj, zmień kolejność) i kolorowaniem wg CCRC"
        )
        gear_row.addWidget(self._btn_columns)
        current_col.addLayout(gear_row)

        # "Koloruj wg CCRC" nie jest już widoczne w głównym layoucie - przeniesione do
        # popupu zębatki (_ColumnManagerPopup), ale checkbox zostaje jako "źródło prawdy"
        # (isChecked()/toggled), z którego korzysta reszta kodu bez zmian.
        self._ccrc_check = QCheckBox("Koloruj wg CCRC")
        self._ccrc_check.setChecked(True)
        # Domyślny styl aplikacji nie rysuje "ptaszka" (tylko subtelną zmianę tła) -
        # tutaj potrzebny jest jednoznaczny, kontrastowy stan zaznaczenia.
        self._ccrc_check.setStyleSheet(
            f"{small_label_style}"
            "QCheckBox::indicator{width:13px;height:13px;border-radius:3px;"
            f"border:1.5px solid {border};background:{panel};}}"
            "QCheckBox::indicator:checked{background:#22c55e;border-color:#16a34a;}"
        )

        text_tip = "Dotyczy wszystkich firmware, oprócz 4.002 i 4.003"
        binary_tip = "Dotyczy firmware 4.002 i 4.003"

        row_a = QHBoxLayout()
        row_a.setSpacing(6)
        lbl_a = QLabel("Aktualna text:")
        lbl_a.setStyleSheet(small_label_style)
        lbl_a.setFixedWidth(86)
        lbl_a.setToolTip(text_tip)
        row_a.addWidget(lbl_a)
        self._current_text_edit = QLineEdit()
        self._current_text_edit.setPlaceholderText("np. 21112250")
        self._current_text_edit.setStyleSheet(small_input_style)
        self._current_text_edit.setFixedHeight(24)
        self._current_text_edit.setFixedWidth(180)
        self._current_text_edit.setToolTip(text_tip)
        row_a.addWidget(self._current_text_edit)
        current_col.addLayout(row_a)

        row_b = QHBoxLayout()
        row_b.setSpacing(6)
        lbl_b = QLabel("Aktualna binary:")
        lbl_b.setStyleSheet(small_label_style)
        lbl_b.setFixedWidth(86)
        lbl_b.setToolTip(binary_tip)
        row_b.addWidget(lbl_b)
        self._current_binary_edit = QLineEdit()
        self._current_binary_edit.setPlaceholderText("np. 2111225")
        self._current_binary_edit.setStyleSheet(small_input_style)
        self._current_binary_edit.setFixedHeight(24)
        self._current_binary_edit.setFixedWidth(180)
        self._current_binary_edit.setToolTip(binary_tip)
        row_b.addWidget(self._current_binary_edit)
        current_col.addLayout(row_b)

        top_row.addLayout(current_col)
        root.addLayout(top_row)

        # FlowLayout zamiast QHBoxLayout - przy zwężaniu okna przyciski filtrów
        # przechodzą do kolejnej linii zamiast ściskać się do nieczytelnego rozmiaru.
        search_row = FlowLayout(h_spacing=8, v_spacing=6)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("🔍  Szukaj…  (kolumna:tekst, kolumna:!tekst wyklucza)")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setStyleSheet(input_style)
        self._search_edit.setFixedWidth(450)
        search_row.addWidget(self._search_edit)
        self._btn_show_binary = QPushButton("Pokaż same binary")
        self._btn_show_binary.setCheckable(True)
        self._btn_show_binary.setStyleSheet(btn_style)
        self._btn_show_binary.setToolTip("Pokazuje tylko wiersze z firmware 4.002/4.003 (lista w formacie binary)")
        search_row.addWidget(self._btn_show_binary)
        self._btn_show_text = QPushButton("Pokaż same text")
        self._btn_show_text.setCheckable(True)
        self._btn_show_text.setStyleSheet(btn_style)
        self._btn_show_text.setToolTip("Pokazuje tylko wiersze z firmware poza 4.002/4.003 (lista w formacie text)")
        search_row.addWidget(self._btn_show_text)
        self._btn_hide_matched = QPushButton("Ukryj podegrane")
        self._btn_hide_matched.setCheckable(True)
        self._btn_hide_matched.setStyleSheet(btn_style)
        self._btn_hide_matched.setToolTip("Ukrywa wiersze już oznaczone na zielono (podegrane) - zostają tylko te do podegrania")
        search_row.addWidget(self._btn_hide_matched)
        self._btn_hide_no_id = QPushButton("Ukryj bez ID rejestratora")
        self._btn_hide_no_id.setCheckable(True)
        self._btn_hide_no_id.setStyleSheet(btn_style)
        self._btn_hide_no_id.setToolTip(
            "Ukrywa wiersze bez ID rejestratora - puste, 'brak' albo sama kropka '.' liczą się "
            "tak samo jak brak wartości (np. pojazdy bez telemetrii) - i tak nie da się ich dopasować do serwera"
        )
        search_row.addWidget(self._btn_hide_no_id)
        self._btn_deselect_skaut1 = QPushButton("Odznacz Skaut1")
        self._btn_deselect_skaut1.setCheckable(True)
        self._btn_deselect_skaut1.setStyleSheet(btn_style)
        self._btn_deselect_skaut1.setToolTip(
            "Ukrywa w tabeli rejestratory z firmware 1.XXX.XXX (Skaut1) - "
            "10.XXX.XXX to nie Skaut1 i pozostaje widoczne"
        )
        search_row.addWidget(self._btn_deselect_skaut1)
        self._btn_hide_non_setivo = QPushButton("Ukryj nie-Setivo")
        self._btn_hide_non_setivo.setCheckable(True)
        self._btn_hide_non_setivo.setStyleSheet(btn_style)
        self._btn_hide_non_setivo.setToolTip(
            "Ukrywa rejestratory innych producentów niż Setivo (Albatros, Teltonika, Topfly...) - "
            "podgrywanie kart Dallas działa tylko dla Setivo"
        )
        search_row.addWidget(self._btn_hide_non_setivo)
        root.addLayout(search_row)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setItemDelegate(_RowBgDelegate(self._table, is_light))
        self._table.setHorizontalHeader(FilterableHeaderView(is_light, self._table))
        self._table.setHorizontalHeaderLabels([label for label, _ in _COLUMNS])
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        hdr.setSectionsMovable(True)
        hdr.filter_clicked.connect(self._on_column_filter_clicked)
        hdr.clear_all_requested.connect(self._on_clear_col_filters)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.itemSelectionChanged.connect(self._update_action_buttons)
        root.addWidget(self._table, 1)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)
        self._summary_lbl = QLabel(" ")
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setStyleSheet(f"color:{text}; font-size:9pt; font-weight:600;")
        bottom_row.addWidget(self._summary_lbl, 1)
        # "Rekordów: N" jako odznaka (pill) w dolnym rzędzie przycisków - QPushButton ma tu
        # sztywną wysokość (min/max-height w btn_style), więc wyrównanie do reszty przycisków
        # jest gwarantowane bit-w-bit, a nie zależne od natywnego stylu Qt na danym systemie.
        badge_bg = self._accent_tint_bg
        badge_fg = self._accent_tint_fg
        badge_border = self._accent_tint_border
        self._info_lbl = QLabel("")
        self._info_lbl.setAlignment(Qt.AlignCenter)
        self._info_lbl.setStyleSheet(
            f"QLabel{{background:{badge_bg}; color:{badge_fg}; font-size:8pt; font-weight:700;"
            f"border:1px solid {badge_border}; border-radius:10px; padding:3px 9px;"
            f"min-height:20px;max-height:20px;}}"
        )
        # Ukryta dopóki nic nie jest wczytane/dopasowane.
        self._info_lbl.hide()
        bottom_row.addWidget(self._info_lbl, 0, Qt.AlignRight | Qt.AlignBottom)
        # Przyciski "gwiazdek" (sms i komenda) w jednym rzędzie, od lewej do prawej:
        # gwiazda-sms, miotła-sms, gwiazda-komenda, miotła-komenda. Krótkie etykiety
        # (sama ikona + jedno słowo) zajmują mniej miejsca niż wcześniejsze długie opisy.
        self._btn_stars_sms = QPushButton("⭐  SMS")
        self._btn_stars_sms.setStyleSheet(btn_style)
        self._btn_stars_sms.setToolTip(f"Włączenie gwiazdek (SMS) - kopiuje do schowka: {_STARS_SMS_TEXT}")
        bottom_row.addWidget(self._btn_stars_sms, 0, Qt.AlignRight | Qt.AlignBottom)
        self._btn_clear_stars_sms = QPushButton("🧹  SMS")
        self._btn_clear_stars_sms.setStyleSheet(btn_style)
        self._btn_clear_stars_sms.setToolTip(f"Wyłączenie gwiazdek (SMS) - kopiuje do schowka: {_CLEAR_STARS_SMS_TEXT}")
        bottom_row.addWidget(self._btn_clear_stars_sms, 0, Qt.AlignRight | Qt.AlignBottom)
        self._btn_stars_cmd_on = QPushButton("⭐  Komenda")
        self._btn_stars_cmd_on.setStyleSheet(btn_style)
        self._btn_stars_cmd_on.setToolTip(f"Włączenie gwiazdek (komenda) - kopiuje do schowka: {_STARS_CMD_ON_TEXT}")
        bottom_row.addWidget(self._btn_stars_cmd_on, 0, Qt.AlignRight | Qt.AlignBottom)
        self._btn_stars_cmd_off = QPushButton("🧹  Komenda")
        self._btn_stars_cmd_off.setStyleSheet(btn_style)
        self._btn_stars_cmd_off.setToolTip(f"Wyłączenie gwiazdek (komenda) - kopiuje do schowka: {_STARS_CMD_OFF_TEXT}")
        bottom_row.addWidget(self._btn_stars_cmd_off, 0, Qt.AlignRight | Qt.AlignBottom)

        self._btn_export_sim = QPushButton("📄  Generuj SIM do CSV")
        self._btn_export_sim.setStyleSheet(btn_style)
        self._btn_export_sim.setToolTip(
            "Zapisuje numery SIM zaznaczonych wierszy do pliku CSV w formacie Name,Number"
        )
        bottom_row.addWidget(self._btn_export_sim, 0, Qt.AlignRight | Qt.AlignBottom)
        self._btn_send_list = QPushButton("📤  Wyślij listę")
        self._btn_send_list.setStyleSheet(btn_style)
        self._btn_send_list.setToolTip(
            "Wysyła listę dla zaznaczonych wierszy (tak jak 'Wyślij listę' w menu prawego klawisza)"
        )
        bottom_row.addWidget(self._btn_send_list, 0, Qt.AlignRight | Qt.AlignBottom)
        root.addLayout(bottom_row)

        self._btn_pick_attachment.clicked.connect(self._on_pick_attachment)
        self._btn_pick_server.clicked.connect(self._on_pick_server)
        self._search_edit.textChanged.connect(self._apply_filters)
        self._current_text_edit.textChanged.connect(self._on_current_ccrc_changed)
        self._current_binary_edit.textChanged.connect(self._on_current_ccrc_changed)
        self._ccrc_check.toggled.connect(self._refresh_colors)
        self._btn_hide_matched.toggled.connect(self._apply_filters)
        self._btn_hide_no_id.toggled.connect(self._apply_filters)
        self._btn_export_sim.clicked.connect(self._on_export_sim_csv)
        self._btn_send_list.clicked.connect(self._on_send_list_button)
        self._btn_stars_sms.clicked.connect(self._on_copy_stars_sms)
        self._btn_clear_stars_sms.clicked.connect(self._on_copy_clear_stars_sms)
        self._btn_stars_cmd_on.clicked.connect(self._on_copy_stars_cmd_on)
        self._btn_stars_cmd_off.clicked.connect(self._on_copy_stars_cmd_off)
        self._btn_deselect_skaut1.toggled.connect(self._apply_filters)
        self._btn_hide_non_setivo.toggled.connect(self._apply_filters)
        self._btn_columns.clicked.connect(self._on_manage_columns_clicked)
        self._btn_show_binary.toggled.connect(self._on_show_binary_toggled)
        self._btn_show_text.toggled.connect(self._on_show_text_toggled)
        self._update_action_buttons()

    def _on_show_binary_toggled(self, checked: bool):
        if checked and self._btn_show_text.isChecked():
            self._btn_show_text.setChecked(False)
        self._apply_filters()

    def _on_show_text_toggled(self, checked: bool):
        if checked and self._btn_show_binary.isChecked():
            self._btn_show_binary.setChecked(False)
        self._apply_filters()

    # ------------------------------------------------------------ import

    def _format_mtime_info(self, mtime: str) -> str:
        """Data modyfikacji pliku podświetlona kolorem (a nie zlewająca się z resztą
        szarej etykiety) - żeby od razu było widać, z kiedy jest wczytany plik, np.
        gdyby ktoś przez pomyłkę wskazał stary/nieaktualny eksport."""
        if not mtime:
            return ""
        return f'  •  plik z <span style="color:{self._accent_tint_fg};font-weight:700;">{mtime}</span>'

    def _on_pick_attachment(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Wybierz załącznik z listą pojazdów", "",
            "Excel (*.xlsx *.xls)"
        )
        if not path:
            return
        try:
            rows = parse_attachment(path)
        except Exception as exc:
            QMessageBox.warning(self, "Błąd wczytywania załącznika", str(exc))
            return
        if not rows:
            QMessageBox.warning(self, "Brak danych", "Nie znaleziono żadnych pojazdów w tym pliku.")
            return
        self._attachment_rows = rows
        mtime_info = self._format_mtime_info(_file_mtime_label(path))
        fname = _truncate_filename(os.path.basename(path))
        self._lbl_attachment.setText(f"{fname}  •  {len(rows)} pojazdów{mtime_info}")
        self._try_match()
        self._save_session_state()

    def _on_pick_server(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Wybierz eksport urządzeń z serwera", "",
            "CSV (*.csv)"
        )
        if not path:
            return
        try:
            server_map, fleet = parse_server_export(path)
        except Exception as exc:
            QMessageBox.warning(self, "Błąd wczytywania eksportu", str(exc))
            return
        if not server_map:
            QMessageBox.warning(self, "Brak danych", "Nie znaleziono żadnych urządzeń w tym pliku.")
            return
        self._server_map = server_map
        self._server_fleet = fleet
        self._fleet_from_matches = False
        fname = _truncate_filename(os.path.basename(path))
        self._server_label_base = f"{fname}  •  {len(server_map)} urządzeń"
        self._server_mtime_info = self._format_mtime_info(_file_mtime_label(path))
        self._refresh_server_label()
        self._try_match()
        self._save_session_state()

    def _guess_fleet_from_matches(self) -> str:
        """Gdy analiza CAŁEGO eksportu serwera nie dała pewnej większości (typowe dla
        zwykłego panelu GPS, który zwraca urządzenia wielu firm naraz) - spróbuj rozpoznać
        flotę po już DOPASOWANYCH pojazdach z załącznika. To tylko urządzenia tego jednego
        klienta, więc większość wśród NICH jest dużo bardziej wiarygodna niż w całym pliku."""
        if not self._server_map:
            return ""
        counts: dict[str, int] = {}
        for row in self._rows:
            if not row.get("found"):
                continue
            srv = self._server_map.get(row.get("device_id")) or {}
            flota = (srv.get("flota") or "").strip()
            if flota:
                counts[flota] = counts.get(flota, 0) + 1
        if not counts:
            return ""
        best, best_count = max(counts.items(), key=lambda kv: kv[1])
        if best_count / sum(counts.values()) > 0.5:
            return best
        return ""

    def _refresh_server_label(self):
        # _server_label_base (razem z _server_mtime_info/_fleet_from_matches) jest też
        # przywracane w _restore_session_state, więc to działa zarówno po świeżym
        # _on_pick_server, jak i w nowym oknie, które odziedziczyło stan sesji.
        if not self._server_map or not self._server_label_base:
            return
        if self._server_fleet and not self._fleet_from_matches:
            fleet_info = f"  •  wczytano {self._server_fleet}"
        else:
            guessed = self._guess_fleet_from_matches()
            if guessed:
                self._server_fleet = guessed
                self._fleet_from_matches = True
                fleet_info = f"  •  wczytano pojazdy z {guessed}"
            elif self._server_fleet:
                fleet_info = f"  •  wczytano {self._server_fleet}"
            else:
                fleet_info = "  •  flota nierozpoznana"

        # "Brak w eksporcie serwera" - tu, obok informacji o wczytanym eksporcie, a nie
        # w podsumowaniu na dole (żeby tam było czytelniej - patrz _update_summary).
        not_found_info = ""
        if self._rows:
            not_found = sum(1 for r in self._rows if not r["found"])
            warn = "#dc2626" if self._is_light else "#f87171"
            muted = "#64748b" if self._is_light else "#94a3b8"
            color = warn if not_found else muted
            not_found_info = (
                f'  •  brak w eksporcie serwera: '
                f'<span style="color:{color};font-weight:700;">{not_found}</span>'
            )

        self._lbl_server.setText(
            f"{self._server_label_base}{fleet_info}{self._server_mtime_info}{not_found_info}"
        )

    def _save_session_state(self):
        # Pamięć w procesie (patrz _SESSION_STATE) - NIE baza danych. Ma tylko przetrwać
        # do zamknięcia aplikacji, żeby nowo otwarte okno "Podgrywanie Dallas" nie startowało
        # od zera, jeśli w tej samej sesji coś już wczytano/wpisano.
        _SESSION_STATE["attachment_rows"] = self._attachment_rows
        _SESSION_STATE["attachment_label"] = self._lbl_attachment.text()
        _SESSION_STATE["server_map"] = self._server_map
        _SESSION_STATE["server_fleet"] = self._server_fleet
        _SESSION_STATE["server_label"] = self._lbl_server.text()
        _SESSION_STATE["server_label_base"] = self._server_label_base
        _SESSION_STATE["server_mtime_info"] = self._server_mtime_info
        _SESSION_STATE["fleet_from_matches"] = self._fleet_from_matches
        _SESSION_STATE["ccrc_text"] = self._current_text_edit.text()
        _SESSION_STATE["ccrc_binary"] = self._current_binary_edit.text()
        _SESSION_STATE["col_order"] = self._current_column_order()
        _SESSION_STATE["col_hidden"] = self._current_hidden_columns()
        _SESSION_STATE["ccrc_checked"] = self._ccrc_check.isChecked()
        self._db.set_setting(_COL_SETTINGS_KEY, json.dumps({
            "order": _SESSION_STATE["col_order"],
            "hidden": sorted(_SESSION_STATE["col_hidden"]),
            "ccrc_checked": _SESSION_STATE["ccrc_checked"],
        }))

    def _restore_column_settings(self):
        """Wołane niezależnie od _restore_session_state (które przy pierwszym oknie w
        sesji od razu wychodzi, gdy _SESSION_STATE jest jeszcze puste) - żeby układ
        kolumn wczytywał się z bazy nawet dla pierwszego okna po starcie aplikacji."""
        if "col_order" in _SESSION_STATE:
            order = _SESSION_STATE["col_order"]
            hidden = _SESSION_STATE.get("col_hidden") or set()
            ccrc_checked = _SESSION_STATE.get("ccrc_checked", True)
        else:
            raw = self._db.get_setting(_COL_SETTINGS_KEY, "")
            if not raw:
                return
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                return
            order = data.get("order") or []
            hidden = set(data.get("hidden") or [])
            ccrc_checked = data.get("ccrc_checked", True)

        if order:
            self._apply_column_layout(order, hidden)
        self._ccrc_check.setChecked(ccrc_checked)

    def _restore_session_state(self):
        if not _SESSION_STATE:
            return

        self._current_text_edit.blockSignals(True)
        self._current_binary_edit.blockSignals(True)
        self._current_text_edit.setText(_SESSION_STATE.get("ccrc_text", ""))
        self._current_binary_edit.setText(_SESSION_STATE.get("ccrc_binary", ""))
        self._current_text_edit.blockSignals(False)
        self._current_binary_edit.blockSignals(False)

        self._attachment_rows = _SESSION_STATE.get("attachment_rows")
        self._server_map = _SESSION_STATE.get("server_map")
        self._server_fleet = _SESSION_STATE.get("server_fleet", "")
        self._server_label_base = _SESSION_STATE.get("server_label_base", "")
        self._server_mtime_info = _SESSION_STATE.get("server_mtime_info", "")
        self._fleet_from_matches = _SESSION_STATE.get("fleet_from_matches", False)
        if _SESSION_STATE.get("attachment_label"):
            self._lbl_attachment.setText(_SESSION_STATE["attachment_label"])
        if _SESSION_STATE.get("server_label"):
            self._lbl_server.setText(_SESSION_STATE["server_label"])

        if self._attachment_rows is not None and self._server_map is not None:
            self._try_match()
        if self._acceptable_ccrc():
            self._ccrc_check.setChecked(True)
            self._refresh_colors()

    def _try_match(self):
        if self._attachment_rows is None or self._server_map is None:
            return
        # Filtry kolumn, wyszukiwarka i przyciski (Ukryj podegrane/bez ID/Odznacz Skaut1
        # itd.) NIE są resetowane przy ponownym wczytaniu eksportu z serwera (np. po
        # zaktualizowaniu CCRC) - użytkownik zostaje przy tym, co już sobie ustawił.
        # Jeśli po wczytaniu zupełnie innego pliku filtry nie mają sensu, można je
        # wyczyścić prawym klikiem na nagłówku kolumny ("Wyczyść wszystkie filtry").

        rows = build_matched_rows(self._attachment_rows, self._server_map)
        for row in rows:
            if not row["found"]:
                row["status"] = "Brak w eksporcie serwera"
            elif _is_non_setivo(row):
                row["status"] = f"Rejestrator {row['manufacturer']} - obsługa działa tylko dla Setivo"
            else:
                row["status"] = "OK"
        self._rows = rows
        self._populate_table(rows)
        self._update_header_filter_indicators()
        self._update_summary(rows)
        self._update_action_buttons()
        self._refresh_server_label()

    # ------------------------------------------------------------ tabela

    def _acceptable_ccrc(self) -> set:
        return _split_values(self._current_text_edit.text()) | _split_values(self._current_binary_edit.text())

    def _on_current_ccrc_changed(self):
        # Wpisanie wartości ma od razu włączyć kolorowanie - nie trzeba pamiętać o checkboxie.
        if self._acceptable_ccrc() and not self._ccrc_check.isChecked():
            self._ccrc_check.setChecked(True)
        self._refresh_colors()
        _SESSION_STATE["ccrc_text"] = self._current_text_edit.text()
        _SESSION_STATE["ccrc_binary"] = self._current_binary_edit.text()
        self._update_action_buttons()

    def _row_bg(self, row: dict):
        if not self._ccrc_check.isChecked():
            return None
        # Zarejestrowany błąd wysyłania listy Dallas wyklucza zielony wiersz, nawet
        # jeśli CCRC się zgadza - to urządzenie i tak wymaga uwagi/ponownej wysyłki.
        if row.get("dallas_error_date"):
            return None
        acceptable = self._acceptable_ccrc()
        if not acceptable:
            return None
        return self._ccrc_match_bg if row.get("ccrc") in acceptable else None

    def _is_matched(self, row: dict) -> bool:
        return self._row_bg(row) is not None

    def _cell_bg(self, row: dict, key: str, row_bg):
        # Data błędu wysyłania listy Dallas - jeśli jest tam jakakolwiek wartość, wyróżniamy
        # tę komórkę na czerwono niezależnie od tego, czy wiersz jest już pokolorowany.
        if key == "dallas_error_date" and row.get("dallas_error_date"):
            return self._dallas_error_bg
        # Nawet w dopasowanym (zielonym) wierszu, status firmware "255" oznacza błąd -
        # wyróżniamy tylko tę jedną komórkę na czerwono, reszta wiersza zostaje zielona.
        if key == "firmware_status" and row_bg is not None and row.get("firmware_status") == "255":
            return self._fw_status_error_bg
        return row_bg

    def _populate_table(self, rows: list[dict]):
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            bg = self._row_bg(row)
            for c, (_, key) in enumerate(_COLUMNS):
                item = QTableWidgetItem(str(row.get(key, "")))
                if c == 0:
                    item.setData(_ROW_DATA_ROLE, row)
                cell_bg = self._cell_bg(row, key, bg)
                if cell_bg is not None:
                    item.setBackground(cell_bg)
                self._table.setItem(r, c, item)
        # Ważne: setSortingEnabled(True) samo w sobie wywołuje sort po aktualnym
        # sortIndicatorSection() - bez wyczyszczenia go najpierw, wiersze mieszają się
        # od razu po wczytaniu, mimo że użytkownik nigdy nie kliknął w nagłówek.
        self._table.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        self._table.setSortingEnabled(True)
        self._table.resizeColumnsToContents()
        self._apply_filters()

    def _refresh_colors(self):
        for r in range(self._table.rowCount()):
            first_item = self._table.item(r, 0)
            if first_item is None:
                continue
            row = first_item.data(_ROW_DATA_ROLE) or {}
            bg = self._row_bg(row)
            for c, (_, key) in enumerate(_COLUMNS):
                item = self._table.item(r, c)
                if item is None:
                    continue
                cell_bg = self._cell_bg(row, key, bg)
                if cell_bg is not None:
                    item.setBackground(cell_bg)
                else:
                    item.setData(Qt.BackgroundRole, None)
        self._update_summary(self._rows)
        self._apply_filters()

    def _on_cell_clicked(self, row: int, col: int):
        if col >= len(_COLUMNS):
            return
        _, key = _COLUMNS[col]
        if key not in _COPY_ATTRS:
            return
        item = self._table.item(row, col)
        text = item.text().strip() if item else ""
        if not text:
            return
        QApplication.clipboard().setText(text)
        self._flash_cell(item)

    def _flash_cell(self, item: QTableWidgetItem):
        if item.data(_FLASH_ROLE):
            return  # już miga - nie zacinamy animacji ponownym kliknięciem
        item.setData(_FLASH_ROLE, True)

        def restore():
            if item.tableWidget() is not None:
                item.setData(_FLASH_ROLE, None)

        QTimer.singleShot(200, restore)

    def _on_copy_stars_sms(self):
        QApplication.clipboard().setText(_STARS_SMS_TEXT)
        orig = self._btn_stars_sms.text()
        self._btn_stars_sms.setText("Skopiowano ✓")
        QTimer.singleShot(1000, lambda: self._btn_stars_sms.setText(orig))

    def _on_copy_clear_stars_sms(self):
        QApplication.clipboard().setText(_CLEAR_STARS_SMS_TEXT)
        orig = self._btn_clear_stars_sms.text()
        self._btn_clear_stars_sms.setText("Skopiowano ✓")
        QTimer.singleShot(1000, lambda: self._btn_clear_stars_sms.setText(orig))

    def _on_copy_stars_cmd_on(self):
        QApplication.clipboard().setText(_STARS_CMD_ON_TEXT)
        orig = self._btn_stars_cmd_on.text()
        self._btn_stars_cmd_on.setText("Skopiowano ✓")
        QTimer.singleShot(1000, lambda: self._btn_stars_cmd_on.setText(orig))

    def _on_copy_stars_cmd_off(self):
        QApplication.clipboard().setText(_STARS_CMD_OFF_TEXT)
        orig = self._btn_stars_cmd_off.text()
        self._btn_stars_cmd_off.setText("Skopiowano ✓")
        QTimer.singleShot(1000, lambda: self._btn_stars_cmd_off.setText(orig))

    def _update_summary(self, rows: list[dict]):
        total = len(rows)
        matched = sum(1 for r in rows if r["found"])

        # Podświetlone kolorem liczby (zamiast płaskiego szarego tekstu) - żeby te
        # kluczowe statystyki od razu rzucały się w oczy, tak jak odznaka "Rekordów: N".
        # "brak w eksporcie serwera" przeniesione do etykiety eksportu serwera na górze
        # (_refresh_server_label) - tu na dole zostaje tylko to, co najważniejsze.
        accent = self._accent_tint_fg

        text = (
            f'Dopasowano <span style="color:{accent};font-weight:800;">{matched}/{total}</span> '
            f'pojazdów z załącznika'
        )

        acceptable = self._acceptable_ccrc()
        if self._ccrc_check.isChecked() and acceptable:
            # Skauty1 i rejestratory innych producentów niż Setivo (Albatros, Teltonika...)
            # nie liczą się jako podegrane (dla obu realnie nie ma wgrywania listy kart
            # Dallas), więc odejmujemy je od całości - nie mają psuć ani liczyć się do
            # procentu tych, które trzeba podegrać.
            excluded_found = sum(
                1 for r in rows
                if r["found"] and (
                    _is_skaut1_firmware(r.get("firmware"), r.get("device_id")) or _is_non_setivo(r)
                )
            )
            to_program = matched - excluded_found
            programmed = sum(1 for r in rows if self._is_matched(r))
            pct = round(100 * programmed / to_program) if to_program else 0
            pct_color = ("#16a34a" if self._is_light else "#4ade80") if to_program and programmed == to_program else accent
            text += (
                f'  •  podegrane: <span style="color:{pct_color};font-weight:800;">'
                f'{programmed}/{to_program} ({pct}%)</span>'
            )

        self._summary_lbl.setText(text)

    # ------------------------------------------------------------ wyszukiwarka (tekst)

    def _apply_filters(self):
        raw = self._search_edit.text().strip()
        query = raw.lower()
        col_idx = None
        if ":" in raw:
            col_part, _, val_part = raw.partition(":")
            col_key = col_part.strip().lower()
            for i, (label, _) in enumerate(_COLUMNS):
                if col_key in label.lower():
                    col_idx = i
                    query = val_part.strip().lower()
                    break

        negate = query.startswith("!")
        if negate:
            query = query[1:].strip()

        hide_matched = self._btn_hide_matched.isChecked()
        hide_no_id = self._btn_hide_no_id.isChecked()
        fw_filter = "binary" if self._btn_show_binary.isChecked() else (
            "text" if self._btn_show_text.isChecked() else None
        )

        # "Ukryj nie-Setivo" i "Odznacz Skaut1" sterują bezpośrednio filtrami kolumnowymi
        # ("Producent" / "Firmware") zamiast osobnej, niezależnej logiki - dzięki temu
        # przyciski i popupy filtrów kolumn (▼) zawsze pokazują ten sam, spójny stan.
        if self._btn_hide_non_setivo.isChecked():
            setivo_values = {
                str(r.get("manufacturer", "")) for r in self._rows
                if str(r.get("manufacturer", "")).strip().lower() == "setivo"
            }
            if setivo_values:
                self._col_filters["manufacturer"] = setivo_values
        if self._btn_deselect_skaut1.isChecked():
            skaut1_values = {
                str(r.get("firmware", "")) for r in self._rows
                if _is_skaut1_firmware(r.get("firmware"), r.get("device_id"))
            }
            if skaut1_values:
                all_fw_values = {str(r.get("firmware", "")) for r in self._rows}
                self._col_filters["firmware"] = all_fw_values - skaut1_values
        self._update_header_filter_indicators()

        visible = 0
        total = self._table.rowCount()
        for r in range(total):
            first_item = self._table.item(r, 0)
            row = (first_item.data(_ROW_DATA_ROLE) if first_item else None) or {}
            match_filters = all(
                str(row.get(attr, "")) in vals for attr, vals in self._col_filters.items()
            )
            fw_ok = fw_filter is None or _firmware_category(row.get("firmware")) == fw_filter
            no_id = hide_no_id and _is_missing_device_id(row.get("device_id"))
            if (
                not match_filters or not fw_ok or no_id
                or (hide_matched and self._is_matched(row))
            ):
                self._table.setRowHidden(r, True)
                continue

            if not query:
                match_search = True
            elif col_idx is not None:
                cell = self._table.item(r, col_idx)
                match_search = query in (cell.text().lower() if cell else "")
            else:
                match_search = any(
                    query in (self._table.item(r, c).text().lower() if self._table.item(r, c) else "")
                    for c in range(self._table.columnCount())
                )
            if negate:
                match_search = not match_search

            self._table.setRowHidden(r, not match_search)
            if match_search:
                visible += 1

        # Zawsze "Rekordów: X" - liczba widocznych wierszy po WSZYSTKICH filtrach
        # (wyszukiwarka, kolumny, przyciski Pokaż/Ukryj). Łączna liczba wszystkich
        # wczytanych pojazdów jest już pokazana w podsumowaniu na dole.
        self._info_lbl.setText(f"Rekordów: {visible}")
        self._info_lbl.show()

    # ------------------------------------------------------------ filtry kolumnowe (▼)

    def _on_column_filter_clicked(self, col_index: int):
        if col_index < 0 or col_index >= len(_COLUMNS):
            return
        col_name, attr = _COLUMNS[col_index]

        other_cf = {k: v for k, v in self._col_filters.items() if k != attr}
        source = [
            row for row in self._rows
            if all(str(row.get(a, "")) in vs for a, vs in other_cf.items())
        ]
        all_vals = [str(row.get(attr, "")) for row in source]

        active = self._col_filters.get(attr)
        popup = ColumnFilterPopup(col_name, all_vals, active, self._is_light, self)

        hdr = self._table.horizontalHeader()
        sec_x = hdr.sectionViewportPosition(col_index)
        global_pos = self._table.mapToGlobal(QPoint(sec_x, hdr.height()))
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        geom = screen.availableGeometry()
        px = min(global_pos.x(), geom.right() - popup.minimumWidth() - 10)
        py = min(global_pos.y(), geom.bottom() - popup.minimumHeight() - 10)
        popup.move(px, py)

        if popup.exec() == QDialog.Accepted:
            result = popup.selected_values()
            if result is None:
                self._col_filters.pop(attr, None)
            else:
                self._col_filters[attr] = result
            if attr == "manufacturer":
                self._sync_hide_non_setivo_button()
            elif attr == "firmware":
                self._sync_deselect_skaut1_button()
            self._update_header_filter_indicators()
            self._apply_filters()

    def _sync_hide_non_setivo_button(self):
        """Przycisk 'Ukryj nie-Setivo' odzwierciedla ręczną zmianę w popupie filtra
        kolumny 'Producent' - zaznaczony tylko wtedy, gdy wybrano DOKŁADNIE same wartości
        oznaczające Setivo (czyli popup i przycisk reprezentują ten sam stan)."""
        setivo_values = {
            str(r.get("manufacturer", "")) for r in self._rows
            if str(r.get("manufacturer", "")).strip().lower() == "setivo"
        }
        is_setivo_only = bool(setivo_values) and self._col_filters.get("manufacturer") == setivo_values
        self._btn_hide_non_setivo.blockSignals(True)
        self._btn_hide_non_setivo.setChecked(is_setivo_only)
        self._btn_hide_non_setivo.blockSignals(False)

    def _sync_deselect_skaut1_button(self):
        """Przycisk 'Odznacz Skaut1' odzwierciedla ręczną zmianę w popupie filtra
        kolumny 'Firmware' - zaznaczony tylko wtedy, gdy wybrane wartości to DOKŁADNIE
        wszystkie firmware oprócz tych rozpoznanych jako Skaut1."""
        skaut1_values = {
            str(r.get("firmware", "")) for r in self._rows
            if _is_skaut1_firmware(r.get("firmware"), r.get("device_id"))
        }
        all_fw_values = {str(r.get("firmware", "")) for r in self._rows}
        expected = all_fw_values - skaut1_values
        is_skaut1_hidden = bool(skaut1_values) and self._col_filters.get("firmware") == expected
        self._btn_deselect_skaut1.blockSignals(True)
        self._btn_deselect_skaut1.setChecked(is_skaut1_hidden)
        self._btn_deselect_skaut1.blockSignals(False)

    def _on_clear_col_filters(self):
        self._col_filters.clear()
        self._btn_hide_non_setivo.blockSignals(True)
        self._btn_hide_non_setivo.setChecked(False)
        self._btn_hide_non_setivo.blockSignals(False)
        self._btn_deselect_skaut1.blockSignals(True)
        self._btn_deselect_skaut1.setChecked(False)
        self._btn_deselect_skaut1.blockSignals(False)
        self._update_header_filter_indicators()
        self._apply_filters()

    # ------------------------------------------------------------ zarządzanie kolumnami (⚙)

    def _current_column_order(self) -> list:
        hdr = self._table.horizontalHeader()
        pairs = [(hdr.visualIndex(logical), attr) for logical, (_, attr) in enumerate(_COLUMNS)]
        pairs.sort(key=lambda p: p[0])
        return [attr for _, attr in pairs]

    def _current_hidden_columns(self) -> set:
        hdr = self._table.horizontalHeader()
        return {attr for logical, (_, attr) in enumerate(_COLUMNS) if hdr.isSectionHidden(logical)}

    def _apply_column_layout(self, order: list, hidden: set):
        hdr = self._table.horizontalHeader()
        attr_to_logical = {attr: i for i, (_, attr) in enumerate(_COLUMNS)}
        for target_visual, attr in enumerate(order):
            logical = attr_to_logical.get(attr)
            if logical is None:
                continue
            current_visual = hdr.visualIndex(logical)
            if current_visual != target_visual:
                hdr.moveSection(current_visual, target_visual)
        for attr, logical in attr_to_logical.items():
            hdr.setSectionHidden(logical, attr in hidden)

    def _on_manage_columns_clicked(self):
        popup = _ColumnManagerPopup(
            self._current_column_order(), self._current_hidden_columns(),
            self._ccrc_check.isChecked(), self._is_light, self,
        )
        # Wymuś finalny rozmiar PRZED pozycjonowaniem - layout z rzędem przycisków
        # (▲Wyżej/▼Niżej/Zaznacz.../Odznacz...) bywa szerszy niż minimumWidth(), a
        # liczenie pozycji na podstawie samego minimum wypychało popup poza ekran.
        popup.adjustSize()

        btn_top_right = self._btn_columns.mapToGlobal(QPoint(self._btn_columns.width(), self._btn_columns.height()))
        screen = QApplication.screenAt(btn_top_right) or QApplication.primaryScreen()
        geom = screen.availableGeometry()
        # Prawa krawędź popupu wyrównana do prawej krawędzi przycisku (tak jak on jest
        # w prawym górnym rogu) - i dodatkowo przyciśnięta do wnętrza ekranu, gdyby i tak
        # była za szeroka.
        px = btn_top_right.x() - popup.width()
        px = max(geom.left() + 10, min(px, geom.right() - popup.width() - 10))
        py = min(btn_top_right.y(), geom.bottom() - popup.height() - 10)
        popup.move(px, py)

        if popup.exec() != QDialog.Accepted:
            return
        order, hidden = popup.result_order_and_hidden()
        self._apply_column_layout(order, hidden)
        self._ccrc_check.setChecked(popup.ccrc_checked())
        self._save_session_state()

    def _update_header_filter_indicators(self):
        hdr = self._table.horizontalHeader()
        if isinstance(hdr, FilterableHeaderView):
            hdr.set_active({i for i, (_, attr) in enumerate(_COLUMNS) if attr in self._col_filters})

    # ------------------------------------------------------------ menu kontekstowe (prawy klik)

    def _selected_row_data(self) -> list:
        rows = []
        seen_r = set()
        for idx in self._table.selectedIndexes():
            r = idx.row()
            if r in seen_r:
                continue
            seen_r.add(r)
            item = self._table.item(r, 0)
            row = item.data(_ROW_DATA_ROLE) if item else None
            if row:
                rows.append(row)
        return rows

    def _on_export_sim_csv(self):
        rows = self._selected_row_data()
        if not rows:
            QMessageBox.information(self, "Brak zaznaczenia", "Zaznacz przynajmniej jeden wiersz.")
            return
        # Tabela pokazuje surowy numer z załącznika (do wykrywania błędów w systemie) -
        # normalizacja formatu (+48XXXXXXXXX) stosowana jest tylko tutaj, przy eksporcie.
        sims = [_normalize_sim(r.get("sim", "")) for r in rows if r.get("sim", "").strip()]
        if not sims:
            QMessageBox.information(self, "Brak numerów SIM", "Zaznaczone wiersze nie mają numeru SIM.")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Zapisz plik CSV", "import_sim.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Name", "Number"])
                for sim in sims:
                    writer.writerow(["1", sim])
        except Exception as exc:
            QMessageBox.critical(self, "Błąd zapisu", str(exc))
            return

        QMessageBox.information(self, "Zapisano", f"Zapisano {len(sims)} numerów SIM do:\n{path}")

    def _panel_url(self) -> str:
        fleet = self._server_fleet
        url = self._db.get_url_for_fleet(f"Panel - {fleet}") if fleet else ""
        return url or self._db.get_url_for_fleet(_DEFAULT_PANEL_KEY)

    def _jobs_panel_url(self) -> str:
        """Link do sekcji zadań danej floty (słownik Linki flot, klucz
        "Panel - {flota} (zadania)"), z fallbackiem na generyczny "Panel - GPS (zadania)"."""
        fleet = self._server_fleet
        url = self._db.get_url_for_fleet(f"Panel - {fleet} (zadania)") if fleet else ""
        return url or self._db.get_url_for_fleet(f"{_DEFAULT_PANEL_KEY} (zadania)")

    def _needs_per_device(self) -> bool:
        """Czy wysyłać zadania pojedynczo (po jednym urządzeniu), zamiast jednego
        zbiorczego z wieloma IMEI - dotyczy paneli, gdzie filtrowanie rejestratora w
        kroku "Urządzenia" nie działa dla wielu IMEI naraz (patrz _PER_DEVICE_URL_HOSTS)."""
        if (self._server_fleet or "").strip().lower() in _PER_DEVICE_FLEETS:
            return True
        jobs_url = self._jobs_panel_url().lower()
        return any(host in jobs_url for host in _PER_DEVICE_URL_HOSTS)

    def _send_list_status(self, rows: list) -> tuple:
        """Sprawdza czy zaznaczenie kwalifikuje się do wysyłki. Zwraca (ok, tekst_pliku_lub_powod_blokady)."""
        if not self._jobs_panel_url():
            fleet = self._server_fleet or "nieznana"
            return False, (
                f"Brak linku 'Panel - {fleet} (zadania)' (albo 'Panel - GPS (zadania)') "
                "w słowniku Linki flot."
            )
        if not rows:
            return False, "Zaznacz co najmniej jeden wiersz."
        if not all(r.get("found") and r.get("device_id") for r in rows):
            return False, "Wszystkie zaznaczone wiersze muszą być dopasowane do urządzenia z eksportu serwera."

        categories = {_firmware_category(r.get("firmware")) for r in rows}
        if len(categories) > 1:
            return False, "Zaznaczone rejestratory mają pomieszane firmware (text i binary) - wybierz tylko jeden typ."

        category = categories.pop()
        field = self._current_text_edit if category == "text" else self._current_binary_edit
        field_label = "Aktualna text" if category == "text" else "Aktualna binary"
        values = _split_values(field.text())
        if not values:
            return False, f"Wpisz numer listy do wysłania w polu „{field_label}”."
        if len(values) > 1:
            return False, f"W polu „{field_label}” jest więcej niż jedna wartość - wpisz tylko tę, którą chcesz wysłać."

        return True, next(iter(values))

    def _on_send_list_button(self):
        rows = self._selected_row_data()
        ok, info = self._send_list_status(rows)
        if not ok:
            QMessageBox.information(self, "Nie można wysłać listy", info)
            return
        file_name = info
        imeis = [r["device_id"] for r in rows]
        self._send_list(imeis, file_name)

    def _update_action_buttons(self):
        """Przyciski 'Wyślij listę' i 'Generuj SIM do CSV' są aktywne tylko, gdy
        zaznaczenie faktycznie kwalifikuje się do danej akcji - tooltip przy
        wyszarzonym przycisku wyjaśnia dlaczego (widoczny też, gdy jest disabled)."""
        rows = self._selected_row_data()

        self._btn_export_sim.setEnabled(bool(rows))
        self._btn_export_sim.setToolTip(
            "Zapisuje numery SIM zaznaczonych wierszy do pliku CSV w formacie Name,Number"
            if rows else "Zaznacz przynajmniej jeden wiersz."
        )

        ok, info = self._send_list_status(rows)
        base_label = "📤  Wyślij listę"
        if ok:
            file_name = info
            self._btn_send_list.setEnabled(True)
            self._btn_send_list.setText(f"{base_label} „{file_name}”")
            self._btn_send_list.setToolTip(
                f"Wysyła listę „{file_name}” dla {len(rows)} zaznaczonych urządzeń"
            )
        else:
            self._btn_send_list.setEnabled(False)
            self._btn_send_list.setText(base_label)
            self._btn_send_list.setToolTip(info)

    def _on_go_to_panel(self, panel_url: str, rows: list):
        # Przy pojedynczym zaznaczeniu kopiujemy ID rejestratora do schowka - tak samo
        # jak przycisk "Panel" w formularzu głównej tabeli (od razu masz co wkleić
        # w wyszukiwarkę panelu). Przy wielu zaznaczonych wierszach nie ma jednego ID
        # do skopiowania, więc pomijamy.
        if len(rows) == 1 and rows[0].get("device_id"):
            QApplication.clipboard().setText(rows[0]["device_id"])
        QDesktopServices.openUrl(QUrl(panel_url))

    def _on_table_context_menu(self, pos: QPoint):
        if self._table.itemAt(pos) is None and not self._table.selectedItems():
            return
        rows = self._selected_row_data()

        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        # "Przejdź do panelu" ma prowadzić od razu do sekcji zadań (ten sam link co
        # "Wyślij listę") - fallback na ogólny panel, jeśli link "(zadania)" nie jest
        # jeszcze zapisany dla tej floty.
        panel_url = self._jobs_panel_url() or self._panel_url()
        panel_label = f"🌐  Przejdź do panelu {self._server_fleet}" if self._server_fleet else "🌐  Przejdź do panelu"
        act_panel = menu.addAction(panel_label)
        act_panel.setEnabled(bool(panel_url))
        if panel_url:
            act_panel.triggered.connect(lambda: self._on_go_to_panel(panel_url, rows))
        else:
            act_panel.setToolTip("Brak zapisanego linku dla tej floty w słowniku Linki flot.")

        menu.addSeparator()
        ok, info = self._send_list_status(rows)
        if ok:
            file_name = info
            imeis = [r["device_id"] for r in rows]
            per_device = self._needs_per_device()
            suffix = " - osobno per urządzenie (tymczasowo)" if per_device and len(imeis) > 1 else ""
            act_send = menu.addAction(f"📤  Wyślij listę „{file_name}” ({len(imeis)} urządzeń){suffix}")
            act_send.triggered.connect(lambda: self._send_list(imeis, file_name))
        else:
            act_send = menu.addAction("📤  Wyślij listę")
            act_send.setEnabled(False)
            act_send.setToolTip(info)

        menu.addSeparator()
        act_export_sim = menu.addAction("📄  Generuj SIM do CSV")
        act_export_sim.triggered.connect(self._on_export_sim_csv)
        act_copy_cmd = menu.addAction("📋  Kopiuj do komendy")
        act_copy_cmd.triggered.connect(lambda: self._on_copy_devices_command(rows))

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_copy_devices_command(self, rows: list):
        # ID rejestratorów zaznaczonych wierszy, rozdzielone przecinkiem, w formacie
        # gotowym do wklejenia w komendę serwisową (np. X,Y,Z).
        device_ids = [r["device_id"] for r in rows if r.get("device_id")]
        if not device_ids:
            QMessageBox.information(self, "Brak ID", "Zaznaczone wiersze nie mają ID rejestratora.")
            return
        QApplication.clipboard().setText(",".join(device_ids))

    def _send_list(self, imeis: list, file_name: str):
        # org=None - skrypt w przeglądarce sam wybierze jedyną/pierwszą pozycję z listy.
        jobs_url = self._jobs_panel_url()
        per_device = self._needs_per_device()
        url = _build_macro_url(jobs_url, imeis, file_name, org=None, per_device=per_device)
        QDesktopServices.openUrl(QUrl(url))
