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
)
from PySide6.QtCore import Qt, QPoint, QUrl, QTimer
from PySide6.QtGui import QColor, QBrush, QDesktopServices, QPen

from database.db_manager import DatabaseManager
from ui.dallas_import import parse_attachment, parse_server_export, build_matched_rows, _normalize_sim
from ui.main_window import FilterableHeaderView, ColumnFilterPopup

_DEFAULT_PANEL_KEY = "Panel - GPS"
_STARS_SMS_TEXT = "PIN= DALLS=**************** RESTART"
_CLEAR_STARS_SMS_TEXT = "PIN= DALLAS= RESTART"


def _file_mtime_label(path: str) -> str:
    """Data modyfikacji pliku (kiedy został zapisany/wyeksportowany) - żeby było
    widać w aplikacji, czy wczytany eksport nie jest przypadkiem nieaktualny."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return ""
    return datetime.fromtimestamp(mtime).strftime("%d.%m.%Y %H:%M")


_FLASH_ROLE = Qt.UserRole + 3  # ta sama konwencja co RowColorDelegate w tabeli głównej


class _RowBgDelegate(QStyledItemDelegate):
    """Wymusza uwzględnienie Qt.BackgroundRole - globalny styl aplikacji definiuje
    QTableWidget::item, co w Qt powoduje całkowite zignorowanie item.setBackground()
    ustawianego programowo, dopóki delegat nie namaluje tła ręcznie (jak w tabeli głównej).

    Miganie (kopiowanie do schowka) NIE nadpisuje Qt.BackgroundRole - to osobna,
    tymczasowa flaga (_FLASH_ROLE) sprawdzana tutaj przy malowaniu. Dzięki temu
    "prawdziwy" kolor wiersza (dopasowanie CCRC itp.) nigdy nie jest nadpisywany
    i nie gubi się przy odświeżaniu kolorów w trakcie migania."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.data(Qt.BackgroundRole) is not None or index.data(_FLASH_ROLE):
            option.backgroundBrush = QBrush(Qt.NoBrush)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        if index.data(_FLASH_ROLE):
            # MAGIA: tak jak w tabeli głównej - okłamujemy system, że komórka nie jest
            # zaznaczona/najechana, żeby czysty kolor migania zawsze przebił się na wierch.
            opt.state &= ~QStyle.State_Selected
            opt.state &= ~QStyle.State_MouseOver
            painter.save()
            painter.fillRect(opt.rect, QColor("#4ade80"))
            painter.restore()
            super().paint(painter, opt, index)
            return

        bg = index.data(Qt.BackgroundRole)
        if bg is None:
            super().paint(painter, option, index)
            return

        is_selected = bool(opt.state & QStyle.State_Selected)
        # bg jest QBrush (tak zapisuje je item.setBackground()) - QColor(QBrush) rzuca
        # wyjątkiem ("QVariant must be holding a QColor"), trzeba wyciągnąć kolor z brusha.
        color = QBrush(bg).color()
        if is_selected:
            # Samo tło koloru (zielony/czerwony/żółty) nie wystarcza, żeby odróżnić
            # zaznaczenie - przyciemniamy je i dorysowujemy ramkę, żeby było widać.
            color = color.darker(120)

        painter.save()
        painter.fillRect(opt.rect, color)
        if is_selected:
            pen = QPen(QColor("#3b82f6"), 1.5)
            painter.setPen(pen)
            painter.drawRect(opt.rect.adjusted(0, 0, -1, -1))
        painter.restore()

        opt.state &= ~QStyle.State_Selected
        opt.state &= ~QStyle.State_MouseOver
        super().paint(painter, opt, index)

_COLUMNS = [
    ("Nr rejestracyjny", "plate"),
    ("ID rejestratora", "device_id"),
    ("SIM (załącznik)", "sim"),
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


_SKAUT1_MAJOR_RE = re.compile(r"^\D*0*(\d+)(?:\.|$)")


def _is_skaut1_firmware(firmware: str) -> bool:
    """Firmware 1.XXX.XXX = Skaut1. UWAGA: 10.XXX.XXX (i 11., 12. itd.) to NIE Skaut1 -
    dlatego sprawdzamy dokładnie pierwszy segment wersji (liczbowo, ignorując ewentualny
    tekstowy prefiks i wiodące zera), nie samo startswith('1')."""
    fw = (firmware or "").strip()
    if not fw:
        return False
    m = _SKAUT1_MAJOR_RE.match(fw)
    return bool(m) and m.group(1) == "1"


# TYMCZASOWE: na panelu Tauron filtrowanie rejestratora w kroku "Urządzenia" nie działa,
# więc dla tej floty wysyłamy jedno zadanie na urządzenie po kolei, zamiast jednego
# zadania z wieloma IMEI. Usunąć, gdy filtrowanie zostanie naprawione po stronie panelu.
_PER_DEVICE_FLEETS = frozenset({"tauron"})


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


class DallasMatchingWidget(QWidget):
    """Import załącznika firmy + eksportu serwera i dopasowanie ich w tabeli."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._db = DatabaseManager.instance()
        self._is_light = self._db.get_setting("theme_mode", "dark") == "light"
        self._attachment_rows: list[dict] | None = None
        self._server_map: dict[str, dict] | None = None
        self._server_fleet: str = ""  # wykryta flota z eksportu serwera (np. "PGE"/"Tauron")
        self._rows: list[dict] = []  # aktualnie wyświetlane (po dopasowaniu) wiersze
        self._col_filters: dict[str, set] = {}  # attr -> zbiór dozwolonych wartości
        self._setup_ui()
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
        btn_style = (
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:3px;font-size:8pt;font-weight:600;padding:3px 9px;}}"
            f"QPushButton:hover{{border-color:#3b82f6;color:#3b82f6;}}"
            f"QPushButton:checked{{background:#3b82f6;color:#ffffff;border-color:#2563eb;}}"
            f"QPushButton:checked:hover{{background:#2563eb;color:#ffffff;}}"
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

        # Mały, subtelny panel z "aktualnymi" CCRC - jedno pole pod drugim + checkbox włącz/wyłącz.
        current_col = QVBoxLayout()
        current_col.setSpacing(3)

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
        current_col.addWidget(self._ccrc_check)

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

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("🔍  Szukaj…  (kolumna:tekst, kolumna:!tekst wyklucza)")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setStyleSheet(input_style)
        search_row.addWidget(self._search_edit, 1)
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
        self._btn_hide_no_id.setToolTip("Ukrywa wiersze bez ID rejestratora (np. pojazdy bez telemetrii) - i tak nie da się ich dopasować do serwera")
        search_row.addWidget(self._btn_hide_no_id)
        self._btn_deselect_skaut1 = QPushButton("Odznacz Skaut1")
        self._btn_deselect_skaut1.setCheckable(True)
        self._btn_deselect_skaut1.setStyleSheet(btn_style)
        self._btn_deselect_skaut1.setToolTip(
            "Ukrywa w tabeli rejestratory z firmware 1.XXX.XXX (Skaut1) - "
            "10.XXX.XXX to nie Skaut1 i pozostaje widoczne"
        )
        search_row.addWidget(self._btn_deselect_skaut1)
        self._info_lbl = QLabel("")
        self._info_lbl.setStyleSheet(f"color:{muted}; font-size:8pt;")
        search_row.addWidget(self._info_lbl)
        root.addLayout(search_row)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setItemDelegate(_RowBgDelegate(self._table))
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
        self._btn_stars_sms = QPushButton("⭐  Gwiazdki sms")
        self._btn_stars_sms.setStyleSheet(btn_style)
        self._btn_stars_sms.setToolTip(f"Kopiuje do schowka: {_STARS_SMS_TEXT}")
        bottom_row.addWidget(self._btn_stars_sms, 0, Qt.AlignRight | Qt.AlignBottom)
        self._btn_clear_stars_sms = QPushButton("🧹  Usuń gwiazdki sms")
        self._btn_clear_stars_sms.setStyleSheet(btn_style)
        self._btn_clear_stars_sms.setToolTip(f"Kopiuje do schowka: {_CLEAR_STARS_SMS_TEXT}")
        bottom_row.addWidget(self._btn_clear_stars_sms, 0, Qt.AlignRight | Qt.AlignBottom)
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
        self._btn_deselect_skaut1.toggled.connect(self._apply_filters)
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
        mtime_info = f"  •  plik z {mtime}" if (mtime := _file_mtime_label(path)) else ""
        self._lbl_attachment.setText(f"{os.path.basename(path)}  •  {len(rows)} pojazdów{mtime_info}")
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
        fleet_info = f"  •  wczytano {fleet}" if fleet else "  •  flota nierozpoznana"
        mtime_info = f"  •  plik z {mtime}" if (mtime := _file_mtime_label(path)) else ""
        self._lbl_server.setText(f"{os.path.basename(path)}  •  {len(server_map)} urządzeń{fleet_info}{mtime_info}")
        self._try_match()
        self._save_session_state()

    def _save_session_state(self):
        # Pamięć w procesie (patrz _SESSION_STATE) - NIE baza danych. Ma tylko przetrwać
        # do zamknięcia aplikacji, żeby nowo otwarte okno "Podgrywanie Dallas" nie startowało
        # od zera, jeśli w tej samej sesji coś już wczytano/wpisano.
        _SESSION_STATE["attachment_rows"] = self._attachment_rows
        _SESSION_STATE["attachment_label"] = self._lbl_attachment.text()
        _SESSION_STATE["server_map"] = self._server_map
        _SESSION_STATE["server_fleet"] = self._server_fleet
        _SESSION_STATE["server_label"] = self._lbl_server.text()
        _SESSION_STATE["ccrc_text"] = self._current_text_edit.text()
        _SESSION_STATE["ccrc_binary"] = self._current_binary_edit.text()

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
        self._col_filters.clear()
        self._search_edit.blockSignals(True)
        self._search_edit.clear()
        self._search_edit.blockSignals(False)

        rows = build_matched_rows(self._attachment_rows, self._server_map)
        for row in rows:
            row["status"] = "OK" if row["found"] else "Brak w eksporcie serwera"
        self._rows = rows
        self._populate_table(rows)
        self._update_header_filter_indicators()
        self._update_summary(rows)
        self._update_action_buttons()

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

    def _update_summary(self, rows: list[dict]):
        total = len(rows)
        matched = sum(1 for r in rows if r["found"])
        not_found = total - matched
        text = (
            f"Dopasowano {matched}/{total} pojazdów z załącznika  •  "
            f"brak w eksporcie serwera: {not_found}"
        )

        acceptable = self._acceptable_ccrc()
        if self._ccrc_check.isChecked() and acceptable:
            # Skauty1 nie liczą się jako podegrane (dla nich realnie nie ma wgrywania listy
            # kart Dallas), więc odejmujemy je od całości - nie mają psuć ani liczyć się
            # do procentu tych, które trzeba podegrać.
            skaut1_found = sum(1 for r in rows if r["found"] and _is_skaut1_firmware(r.get("firmware")))
            to_program = matched - skaut1_found
            programmed = sum(1 for r in rows if self._is_matched(r))
            pct = round(100 * programmed / to_program) if to_program else 0
            text += f"  •  podegrane: {programmed}/{to_program} ({pct}%)"

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
        hide_skaut1 = self._btn_deselect_skaut1.isChecked()
        fw_filter = "binary" if self._btn_show_binary.isChecked() else (
            "text" if self._btn_show_text.isChecked() else None
        )

        visible = 0
        total = self._table.rowCount()
        for r in range(total):
            first_item = self._table.item(r, 0)
            row = (first_item.data(_ROW_DATA_ROLE) if first_item else None) or {}
            match_filters = all(
                str(row.get(attr, "")) in vals for attr, vals in self._col_filters.items()
            )
            fw_ok = fw_filter is None or _firmware_category(row.get("firmware")) == fw_filter
            no_id = hide_no_id and not row.get("device_id")
            is_skaut1 = hide_skaut1 and _is_skaut1_firmware(row.get("firmware"))
            if not match_filters or not fw_ok or no_id or is_skaut1 or (hide_matched and self._is_matched(row)):
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
            self._update_header_filter_indicators()
            self._apply_filters()

    def _on_clear_col_filters(self):
        self._col_filters.clear()
        self._update_header_filter_indicators()
        self._apply_filters()

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
            act_panel.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(panel_url)))
        else:
            act_panel.setToolTip("Brak zapisanego linku dla tej floty w słowniku Linki flot.")

        menu.addSeparator()
        ok, info = self._send_list_status(rows)
        if ok:
            file_name = info
            imeis = [r["device_id"] for r in rows]
            per_device = (self._server_fleet or "").strip().lower() in _PER_DEVICE_FLEETS
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

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _send_list(self, imeis: list, file_name: str):
        # org=None - skrypt w przeglądarce sam wybierze jedyną/pierwszą pozycję z listy.
        jobs_url = self._jobs_panel_url()
        per_device = (self._server_fleet or "").strip().lower() in _PER_DEVICE_FLEETS
        url = _build_macro_url(jobs_url, imeis, file_name, org=None, per_device=per_device)
        QDesktopServices.openUrl(QUrl(url))
