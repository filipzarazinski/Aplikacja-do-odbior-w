"""
ui/kompendium_dialog.py
------------------------
Okno "Baza wiedzy" - dwie zakładki:
- Baza wiedzy: artykuły z prostym formatowaniem (pogrubienie/lista/nagłówek),
  przeszukiwane "inteligentnie" (FTS5 - dopasowanie słów/prefiksów + ranking trafności).
- Komendy: płaska baza komend do rejestratorów (marka + komenda + opis).

Redagowane lokalnie w aplikacji, dystrybuowane do innych użytkowników przez ręczny
eksport/import pliku JSON (świadomie NIE przez automatyczny link/URL - patrz historia
problemów z synchronizacją "Baza SIM").
"""
import json
import base64
import re

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QTabWidget, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QComboBox, QMessageBox,
    QFileDialog, QSplitter, QApplication, QTextEdit, QMenu, QSpinBox, QDialogButtonBox,
    QFormLayout,
)
from PySide6.QtCore import Qt, QTimer, QUrl, QBuffer, QIODevice
from PySide6.QtGui import (
    QFont, QTextListFormat, QTextCursor, QTextCharFormat, QColor, QBrush, QDesktopServices,
    QShortcut, QKeySequence, QImage, QTextImageFormat, QTextTableFormat, QTextFrameFormat,
    QTextLength,
)

from config import KOMPENDIUM_EDIT_ENABLED
from database.db_manager import DatabaseManager
from ui.widgets.montaz_tab import PlainPasteTextEdit

_BRANDS = ["Setivo", "Teltonika", "Albatros"]
_COMMAND_TYPES = ["SMS", "Logi"]
_ARTICLE_ROLE = Qt.UserRole + 1
_COMMAND_ROLE = Qt.UserRole + 1


def _safe_filename(title: str) -> str:
    """Tytuł artykułu -> nazwa pliku bez znaków niedozwolonych w Windows."""
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", title).strip(" ._")
    return cleaned or "artykul"


def _format_at(document, pos: int):
    """Format DOKŁADNIE jednego znaku na pozycji `pos` (nie 'znaku przed kursorem',
    jak niejednoznaczny QTextCursor.charFormat() bez zaznaczenia) - potrzebne, żeby
    wykrywanie 'czy to już jest oznaczone jako klikalne' było jednoznaczne niezależnie
    od kierunku zaznaczenia myszą."""
    total = document.characterCount() - 1
    if pos < 0 or pos >= total:
        return None
    c = QTextCursor(document)
    c.setPosition(pos)
    c.setPosition(pos + 1, QTextCursor.KeepAnchor)
    return c.charFormat()


class ArticleBodyTextEdit(PlainPasteTextEdit):
    """PlainPasteTextEdit + klik w oznaczony ('klikalny') fragment tekstu kopiuje go
    do schowka - z podświetleniem po najechaniu (kursor zmienia się w łapkę) i krótkim
    'błyskiem' po kliknięciu, żeby było wyraźnie czuć, że coś się kliknęło. Działa
    niezależnie od trybu odczytu/edycji - to funkcja dla wszystkich użytkowników,
    nie tylko dla redagującego."""

    _FLASH_MS = 260

    def __init__(self, parent=None, is_light: bool = False):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._press_pos = None
        self._hover_range = None
        self._is_light = is_light
        self.on_copy = None  # callable(str), ustawiane z zewnątrz

    # --- wykrywanie granic oznaczonego fragmentu ---

    def _anchor_range_at(self, pos: int):
        fmt = _format_at(self.document(), pos)
        if not fmt or not fmt.isAnchor():
            return None
        start = pos
        while True:
            f = _format_at(self.document(), start - 1)
            if not f or not f.isAnchor():
                break
            start -= 1
        end = pos
        while True:
            f = _format_at(self.document(), end + 1)
            if not f or not f.isAnchor():
                break
            end += 1
        return (start, end + 1)

    def _span_cursor(self, span):
        c = QTextCursor(self.document())
        c.setPosition(span[0])
        c.setPosition(span[1], QTextCursor.KeepAnchor)
        return c

    # --- hover / flash (wizualne, nie modyfikują treści artykułu) ---

    def _set_hover(self, span):
        if span == self._hover_range:
            return
        self._hover_range = span
        self._refresh_extra_selections()

    def _refresh_extra_selections(self, flash_span=None):
        selections = []
        if self._hover_range and not flash_span:
            sel = QTextEdit.ExtraSelection()
            sel.cursor = self._span_cursor(self._hover_range)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#ddd6fe" if self._is_light else "#3b2f6b"))
            fmt.setForeground(QColor("#5b21b6" if self._is_light else "#ddd6fe"))
            sel.format = fmt
            selections.append(sel)
        if flash_span:
            sel = QTextEdit.ExtraSelection()
            sel.cursor = self._span_cursor(flash_span)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#4ade80"))
            fmt.setForeground(QColor("#052e16"))
            sel.format = fmt
            selections.append(sel)
        self.setExtraSelections(selections)

    def _flash(self, span):
        self._refresh_extra_selections(flash_span=span)
        QTimer.singleShot(self._FLASH_MS, self._refresh_extra_selections)

    # --- eventy myszy ---

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        pos = self.cursorForPosition(event.pos()).position()
        span = self._anchor_range_at(pos)
        self._set_hover(span)
        self.viewport().setCursor(Qt.PointingHandCursor if span else Qt.IBeamCursor)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._set_hover(None)

    def mousePressEvent(self, event):
        self._press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        pos = event.pos()
        moved = self._press_pos is not None and (pos - self._press_pos).manhattanLength() > 3
        self._press_pos = None
        if moved or self.textCursor().hasSelection():
            return
        doc_pos = self.cursorForPosition(pos).position()
        span = self._anchor_range_at(doc_pos)
        if not span:
            return
        fmt = _format_at(self.document(), span[0])
        text = (fmt.anchorHref() if fmt else "") or self._span_cursor(span).selectedText()
        if not text:
            return
        QApplication.clipboard().setText(text)
        if self.on_copy:
            self.on_copy(text)
        self._flash(span)

    # --- wklejanie zrzutów ekranu (obrazków) ---

    _MAX_IMAGE_WIDTH = 900  # px - zrzuty ekranu bywają dużo szersze niż edytor

    def canInsertFromMimeData(self, source):
        if source.hasImage():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                self._insert_image(image)
                return
        super().insertFromMimeData(source)

    def _insert_image(self, image: QImage):
        # Skalujemy duże zrzuty ekranu w dół (i tak są nieczytelne w wąskim edytorze),
        # żeby nie rozdymać body_html/eksportu JSON - obraz trzymany jest jako data:
        # URI wprost w tekście, więc rozmiar obrazka = rozmiar zapisanego artykułu.
        if image.width() > self._MAX_IMAGE_WIDTH:
            image = image.scaledToWidth(self._MAX_IMAGE_WIDTH, Qt.SmoothTransformation)
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        image.save(buf, "PNG")
        b64 = bytes(buf.data().toBase64()).decode("ascii")
        self.textCursor().insertImage(f"data:image/png;base64,{b64}")


class _InsertTableDialog(QDialog):
    """Mały popup pytający o liczbę wierszy/kolumn nowej tabeli."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wstaw tabelę")
        self.setModal(True)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._rows = QSpinBox()
        self._rows.setRange(1, 100)
        self._rows.setValue(3)
        form.addRow("Wiersze:", self._rows)
        self._cols = QSpinBox()
        self._cols.setRange(1, 20)
        self._cols.setValue(3)
        form.addRow("Kolumny:", self._cols)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def rows(self) -> int:
        return self._rows.value()

    def cols(self) -> int:
        return self._cols.value()


class _CommandEditDialog(QDialog):
    """Popup do dodawania/edycji pojedynczej komendy - osobne okienko zamiast stale
    widocznego formularza w zakładce Komendy, żeby dodanie/edycja było świadomą,
    osobną akcją (a nie czymś co zawsze zajmuje miejsce na ekranie)."""

    def __init__(self, parent, btn_style: str, input_style: str, is_light: bool,
                 brand: str = "", typ: str = "", command: str = "", description: str = "",
                 title: str = "Nowa komenda"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        bg = "#f8fafc" if is_light else "#1a1d23"
        text = "#0f172a" if is_light else "#e2e8f0"
        self.setStyleSheet(f"QDialog{{background:{bg};}} QLabel{{color:{text};}}")
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._on_save)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        def _row(label_text, widget):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(70)
            row.addWidget(lbl)
            row.addWidget(widget, 1)
            lay.addLayout(row)

        self._brand_edit = QComboBox()
        self._brand_edit.setEditable(True)
        self._brand_edit.addItems(_BRANDS)
        self._brand_edit.setCurrentText(brand)
        self._brand_edit.setStyleSheet(input_style)
        _row("Marka", self._brand_edit)

        self._type_edit = QComboBox()
        self._type_edit.setEditable(True)
        self._type_edit.addItems(_COMMAND_TYPES)
        self._type_edit.setCurrentText(typ)
        self._type_edit.setStyleSheet(input_style)
        _row("Typ", self._type_edit)

        self._command_edit = QLineEdit(command)
        self._command_edit.setStyleSheet(input_style)
        _row("Komenda", self._command_edit)

        self._description_edit = QLineEdit(description)
        self._description_edit.setStyleSheet(input_style)
        _row("Opis", self._description_edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("Anuluj")
        btn_cancel.setStyleSheet(btn_style)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_save = QPushButton("💾  Zapisz")
        btn_save.setStyleSheet(btn_style)
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)
        lay.addLayout(btn_row)

        self._command_edit.setFocus()

    def _on_save(self):
        if not self._brand_edit.currentText().strip() or not self._command_edit.text().strip():
            QMessageBox.information(self, "Brak danych", "Podaj markę i komendę.")
            return
        self.accept()

    def values(self):
        return (
            self._brand_edit.currentText().strip(),
            self._type_edit.currentText().strip(),
            self._command_edit.text().strip(),
            self._description_edit.text().strip(),
        )


class KompendiumDialog(QDialog):
    """Okno Baza wiedzy + Komendy. Można otworzyć z góry ustawione na zakładkę
    Komendy (`initial_tab`/`initial_brand`) albo z podpowiedziami kontekstu dla
    artykułów (`initial_context_fields`) - pole szukania artykułów ZAWSZE startuje
    puste (patrz search_kompendium_articles: sugestia z formularza działa tylko dopóki
    użytkownik sam czegoś nie wpisze - wtedy jest w całości porzucana)."""

    def __init__(self, parent=None, initial_tab: str = "articles",
                 initial_brand: str = "", initial_context_fields: dict | None = None,
                 initial_absent_hardware: set | None = None):
        super().__init__(parent)
        self._db = DatabaseManager.instance()
        self._is_light = self._db.get_setting("theme_mode", "dark") == "light"
        # Edycja jest teraz odblokowywana WYŁĄCZNIE hasłem, przez ukryty skrót Ctrl+Alt+
        # Shift+E w głównym oknie (patrz main_window._on_toggle_kompendium_edit) - nie
        # samą flagą KOMPENDIUM_EDIT_ENABLED w config.py, która teraz jest tylko grubym,
        # globalnym wyłącznikiem (musi być True ORAZ hasło musi być wcześniej wpisane
        # poprawnie na TEJ instalacji, inaczej edycja jest wyłączona nawet gdy flaga w
        # kodzie jest True - inaczej KAŻDY egzemplarz aplikacji miałby pełną edycję).
        self._edit_enabled = (
            KOMPENDIUM_EDIT_ENABLED and self._db.get_setting("kompendium_edit_unlocked", "0") == "1"
        )
        self._current_article_id: int | None = None
        self._current_command_id: int | None = None
        # Podgląd "tak jak zobaczy zwykły użytkownik" - dostępny tylko gdy edycja jest
        # w ogóle włączona (czyli tylko u mnie, z poziomu kodu) - przełącznik w UI,
        # nie osobna flaga w config.py, żeby dało się to przeklikać bez zmiany kodu.
        self._article_preview_mode = False
        self._article_edit_only_widgets: list = []
        # Dodatkowe podpowiedzi spoza pola wyszukiwania - wiele pól formularza montażu
        # naraz (Firma, model urządzenia, typ pojazdu, marka tachografu, D8, typ
        # montażu...) - każde ważone osobno przy liczeniu trafności w search_kompendium_
        # articles, żeby np. przy firmie Robano artykuł dla Robano wygrywał z artykułem
        # "NIE dotyczy Robano", mimo że oba tekstowo wspominają to słowo.
        self._article_context_fields = initial_context_fields or {}
        # Sprzęt potwierdzony jako NIEobecny (checkbox jawnie odznaczony w formularzu) -
        # patrz search_kompendium_articles: tłumi artykuły o tym sprzęcie (np. RFID),
        # nawet jeśli inne pole formularza przypadkiem koresponduje jakimś słowem z ich
        # tagami. Puste, gdy okno otwarte spoza formularza montażu (np. z toolbara).
        self._absent_hardware = initial_absent_hardware or set()

        self.setWindowTitle("Baza wiedzy")
        self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint | Qt.WindowMinMaxButtonsHint)
        self.resize(1100, 650)
        self.setMinimumSize(700, 450)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self.close)
        if self._edit_enabled:
            QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._on_ctrl_s)

        self._setup_ui()
        if self._edit_enabled:
            self._set_preview_mode(False)

        if initial_brand:
            idx = self._cmd_brand_filter.findText(initial_brand)
            if idx >= 0:
                self._cmd_brand_filter.setCurrentIndex(idx)
        if initial_tab == "commands":
            self._tabs.setCurrentIndex(1)

        self._reload_articles()
        self._reload_commands()

    # ------------------------------------------------------------ UI setup

    def _body_stylesheet(self, font_pt: float) -> str:
        return (
            f"QTextEdit{{background:{self._panel};color:{self._text_color};"
            f"border:1px solid {self._border};border-radius:4px;padding:6px;"
            f"font-size:{font_pt}pt;}}"
        )

    def _setup_ui(self):
        is_light = self._is_light
        bg = "#f8fafc" if is_light else "#1a1d23"
        panel = "#ffffff" if is_light else "#22262f"
        border = "#cbd5e1" if is_light else "#3a4150"
        text = "#0f172a" if is_light else "#e2e8f0"
        muted = "#64748b" if is_light else "#94a3b8"

        self.setStyleSheet(f"QDialog{{background:{bg};}} QLabel{{color:{text};}}")

        self._btn_style = (
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:3px;font-size:8.5pt;font-weight:600;padding:4px 10px;}}"
            f"QPushButton:hover{{border-color:#3b82f6;color:#3b82f6;}}"
            f"QPushButton:checked{{background:#1e3a5f;color:#60a5fa;border-color:#2d5480;}}"
            # Wcześniej nie było w ogóle stanu :pressed - kliknięcie (np. Zapisz/Usuń)
            # nie dawało żadnego wizualnego efektu wciśnięcia.
            f"QPushButton:pressed{{background:#1e3a5f;color:#60a5fa;border-color:#3b82f6;}}"
        )
        self._input_style = (
            f"QLineEdit,QComboBox{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:3px;padding:4px 7px;font-size:8.5pt;}}"
            f"QLineEdit:focus,QComboBox:focus{{border-color:#3b82f6;}}"
        )
        self._list_style = (
            f"QListWidget,QTableWidget{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:4px;font-size:8.5pt;}}"
            f"QListWidget::item{{padding:5px 6px;}}"
            f"QListWidget::item:selected{{background:#1e3a5f;color:#60a5fa;}}"
            f"QTableWidget::item:selected{{background:#1e3a5f;color:#60a5fa;}}"
        )
        self._panel = panel
        self._border = border
        self._text_color = text
        self._article_font_pt = 9.5
        self._body_style = self._body_stylesheet(self._article_font_pt)
        self._muted_color = muted
        # Tytuł artykułu dostaje wyraźnie inny (większy, pogrubiony) styl niż reszta
        # metadanych (kategoria/tagi/źródło) - inaczej wszystkie cztery pola wyglądały
        # identycznie i tytuł zupełnie ginął w rzędzie.
        self._title_input_style = (
            f"QLineEdit{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:3px;padding:6px 8px;font-size:12.5pt;font-weight:600;}}"
            f"QLineEdit:focus{{border-color:#3b82f6;}}"
        )
        self._meta_label_style = f"color:{muted};font-size:7.5pt;font-weight:600;"
        self._meta_input_style = (
            f"QLineEdit{{background:{panel};color:{muted};border:1px solid {border};"
            f"border-radius:3px;padding:3px 7px;font-size:8pt;}}"
            f"QLineEdit:focus{{border-color:#3b82f6;color:{text};}}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_articles_tab(), "  Baza wiedzy  ")
        self._tabs.addTab(self._build_commands_tab(), "  Komendy  ")
        root.addWidget(self._tabs, 1)

        io_row = QHBoxLayout()
        io_row.setSpacing(8)
        if self._edit_enabled:
            # Nowy/Usuń/Zapisz artykułu żyją w tym wspólnym dolnym pasku (nie w samej
            # zakładce) - zwalnia to sporo miejsca w pionie na treść artykułu. Widoczne
            # tylko na zakładce "Baza wiedzy" (przełączane przez _on_tab_changed).
            self._btn_article_new = QPushButton("＋ Nowy artykuł")
            self._btn_article_new.setStyleSheet(self._btn_style)
            self._btn_article_new.clicked.connect(self._on_new_article)
            io_row.addWidget(self._btn_article_new)
            self._btn_article_delete = QPushButton("🗑  Usuń")
            self._btn_article_delete.setStyleSheet(self._btn_style)
            self._btn_article_delete.clicked.connect(self._on_delete_article)
            io_row.addWidget(self._btn_article_delete)
            self._btn_article_save = QPushButton("💾  Zapisz")
            self._btn_article_save.setObjectName("btn_primary")
            self._btn_article_save.setStyleSheet(self._btn_style)
            self._btn_article_save.clicked.connect(self._on_save_article)
            io_row.addWidget(self._btn_article_save)
            # Eksport/wczytanie POJEDYNCZEGO artykułu - osobno od zbiorczego eksportu/
            # importu całej bazy. Zbiorczy import NADPISUJE całą lokalną kopię (clear +
            # dodanie wszystkiego z pliku) - świetne do dystrybucji "z góry" do zwykłych
            # użytkowników, ale fatalne między dwoma osobami, które same tworzą artykuły
            # (import zbiorczy skasowałby lokalne artykuły drugiej osoby). Pojedynczy
            # import tylko DODAJE jeden artykuł, nie rusza reszty lokalnej bazy.
            self._btn_article_export_one = QPushButton("⬆  Eksportuj artykuł")
            self._btn_article_export_one.setStyleSheet(self._btn_style)
            self._btn_article_export_one.setToolTip(
                "Zapisuje TYLKO ten jeden (otwarty) artykuł do pliku JSON - do przesłania komuś"
            )
            self._btn_article_export_one.clicked.connect(self._on_export_single_article)
            io_row.addWidget(self._btn_article_export_one)
            self._btn_article_import_one = QPushButton("⬇  Wczytaj artykuł")
            self._btn_article_import_one.setStyleSheet(self._btn_style)
            self._btn_article_import_one.setToolTip(
                "Dodaje jeden artykuł z pliku JSON jako nowy - NIE rusza pozostałych artykułów"
            )
            self._btn_article_import_one.clicked.connect(self._on_import_single_article)
            io_row.addWidget(self._btn_article_import_one)
            self._article_edit_only_widgets += [
                self._btn_article_new, self._btn_article_delete, self._btn_article_save,
                self._btn_article_export_one, self._btn_article_import_one,
            ]
            self._tabs.currentChanged.connect(self._on_tab_changed)
        io_row.addStretch()
        # Eksport (jak i cała reszta edycji) tylko gdy edycja odblokowana hasłem - to Ty
        # przygotowujesz plik do udostępnienia, reszta użytkowników tylko go wczytuje.
        if self._edit_enabled:
            btn_export = QPushButton("⬆  Eksportuj Bazę wiedzy")
            btn_export.setStyleSheet(self._btn_style)
            btn_export.setToolTip(
                "Zapisuje wszystkie artykuły i komendy do jednego pliku JSON - do udostępnienia innym"
            )
            btn_export.clicked.connect(self._on_export)
            io_row.addWidget(btn_export)
        btn_import = QPushButton("⬇  Wczytaj Bazę wiedzy z pliku")
        btn_import.setStyleSheet(self._btn_style)
        btn_import.setToolTip(
            "Wczytuje plik JSON z Bazy wiedzy (nadpisuje TWOJĄ lokalną kopię artykułów i komend)"
        )
        btn_import.clicked.connect(self._on_import)
        io_row.addWidget(btn_import)
        root.addLayout(io_row)

    def _on_tab_changed(self, index: int):
        # Nowy/Usuń/Zapisz artykułu (w dolnym pasku) mają sens tylko na zakładce
        # "Baza wiedzy" (index 0) - na Komendach są zbędne.
        is_articles_tab = index == 0
        for btn in (
            self._btn_article_new, self._btn_article_delete, self._btn_article_save,
            self._btn_article_export_one, self._btn_article_import_one,
        ):
            btn.setVisible(is_articles_tab and not self._article_preview_mode)

    # ------------------------------------------------------------ zakładka Artykuły

    def _build_articles_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        # Przycisk chowania/pokazywania listy artykułów - stoi ZAWSZE w tym samym,
        # przewidywalnym miejscu (lewy górny róg, przed wyszukiwarką), niezależnie od
        # tego, czy lista jest akurat zwinięta czy nie. Wcześniej jedynym sposobem na
        # schowanie listy było przeciągnięcie uchwytu splittera do zera - a wtedy sam
        # uchwyt (jedyny sposób na przywrócenie) też znikał, więc nie dało się już
        # łatwo wrócić. Ten przycisk to jedyne miejsce, które to robi świadomie i w
        # obie strony.
        self._btn_toggle_list = QPushButton("◀")
        self._btn_toggle_list.setFixedWidth(28)
        self._btn_toggle_list.setStyleSheet(self._btn_style)
        self._btn_toggle_list.setToolTip("Pokaż/ukryj listę artykułów")
        self._btn_toggle_list.clicked.connect(self._toggle_article_list)
        search_row.addWidget(self._btn_toggle_list)
        self._article_search = QLineEdit()
        self._article_search.setPlaceholderText("🔍  Szukaj w artykułach (tytuł, treść, tagi)…")
        self._article_search.setClearButtonEnabled(True)
        self._article_search.setStyleSheet(self._input_style)
        self._article_search.textChanged.connect(self._reload_articles)
        search_row.addWidget(self._article_search, 1)
        if self._edit_enabled:
            self._btn_article_mode = QPushButton("")
            self._btn_article_mode.setStyleSheet(self._btn_style)
            self._btn_article_mode.clicked.connect(self._on_toggle_preview_mode)
            search_row.addWidget(self._btn_article_mode)
        outer.addLayout(search_row)

        splitter = QSplitter(Qt.Horizontal)
        self._article_splitter = splitter
        # UWAGA: setChildrenCollapsible(False) NIE nadaje się tu do niczego - blokuje
        # też programowe setSizes([0, ...]) z _set_article_list_collapsed, więc sam
        # przycisk przestałby działać. Zamiast tego zostawiamy domyślne (True) - problem
        # "zgubionej" listy rozwiązuje wyłącznie _btn_toggle_list: to jedyne miejsce,
        # które jest ZAWSZE widoczne (nie znika razem z listą jak uchwyt splittera przy
        # ręcznym przeciągnięciu do zera), więc zawsze da się nim wrócić.
        # Zapamiętana szerokość listy sprzed ostatniego zwinięcia - żeby rozwinięcie
        # przywracało tę samą szerokość, a nie zawsze ten sam domyślny rozmiar.
        self._article_list_last_width = 280

        self._article_list = QListWidget()
        self._article_list.setStyleSheet(self._list_style)
        self._article_list.setMinimumWidth(240)
        self._article_list.itemSelectionChanged.connect(self._on_article_selected)
        splitter.addWidget(self._article_list)

        editor_w = QWidget()
        editor = QVBoxLayout(editor_w)
        editor.setContentsMargins(0, 0, 0, 0)
        editor.setSpacing(6)

        # Tytuł dostaje WŁASNY, pełnoszerokościowy wiersz z wyraźnie większym/grubszym
        # stylem - wcześniej siedział w jednym rzędzie z kategorią/tagami/źródłem,
        # wszystkie w identycznym stylu pola, więc tytuł zupełnie nie był wyeksponowany.
        self._article_title = QLineEdit()
        self._article_title.setPlaceholderText("Tytuł artykułu")
        self._article_title.setStyleSheet(self._title_input_style)
        editor.addWidget(self._article_title)

        # Kategoria/tagi/źródło to metadane, nie treść - osobny, wyraźnie "cichszy"
        # (mniejsza czcionka, przygaszony kolor) wiersz, każde pole z podpisem NAD nim
        # (nie tylko placeholder, który znika po wpisaniu wartości) - inaczej rząd
        # samych wartości bez etykiet (np. "Konfiguracja / d8", ":10,bus,konfiguracja")
        # wyglądał jak przypadkowy, nieopisany ciąg tekstu.
        def _meta_field(label_text: str, placeholder: str, stretch: int):
            container = QWidget()
            col = QVBoxLayout(container)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(1)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(self._meta_label_style)
            col.addWidget(lbl)
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setStyleSheet(self._meta_input_style)
            col.addWidget(edit)
            meta_row.addWidget(container, stretch)
            return edit, container

        meta_row = QHBoxLayout()
        meta_row.setSpacing(10)
        self._article_category, _ = _meta_field("KATEGORIA", "np. Tachograf", 1)
        self._article_tags, _ = _meta_field("TAGI (po przecinku)", "np. d8, ddd, skaut8", 1)
        # Pole URL źródła wjeżdża jako 3. pole w tym samym wierszu (nie osobny wiersz) -
        # osobny wiersz z samym polem+przyciskiem wyglądał na pustą, nic-nie-robiącą
        # przestrzeń, zwłaszcza w trybie podglądu. W podglądzie CAŁA kolumna (etykieta +
        # pole) się chowa - inaczej zostawałaby osierocona etykieta "ŹRÓDŁO" bez pola.
        self._article_source, source_container = _meta_field("ŹRÓDŁO (URL)", "https://…", 2)
        editor.addLayout(meta_row)
        self._article_edit_only_widgets.append(source_container)

        # Edycja (pisanie/formatowanie/zapis/usuwanie) jest za osobną flagą od samej
        # widoczności modułu - reszta użytkowników tylko przegląda i wyszukuje.
        if not self._edit_enabled:
            self._article_title.setReadOnly(True)
            self._article_category.setReadOnly(True)
            self._article_tags.setReadOnly(True)
            self._article_source.setReadOnly(True)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        if self._edit_enabled:
            btn_bold = QPushButton("B")
            btn_bold.setCheckable(True)
            btn_bold.setFixedWidth(30)
            btn_bold.setStyleSheet(self._btn_style)
            btn_bold.setToolTip("Pogrubienie")
            btn_bold.clicked.connect(self._toggle_bold)
            toolbar.addWidget(btn_bold)
            btn_list = QPushButton("•  Lista")
            btn_list.setStyleSheet(self._btn_style)
            btn_list.setToolTip("Lista punktowana")
            btn_list.clicked.connect(self._insert_bullet_list)
            toolbar.addWidget(btn_list)
            btn_heading = QPushButton("H  Nagłówek")
            btn_heading.setStyleSheet(self._btn_style)
            btn_heading.setToolTip("Nagłówek (kliknij ponownie na tym samym fragmencie, żeby cofnąć)")
            btn_heading.clicked.connect(self._insert_heading)
            toolbar.addWidget(btn_heading)
            btn_click = QPushButton("🔗  Kopiowalny")
            btn_click.setStyleSheet(self._btn_style)
            btn_click.setToolTip(
                "Zaznacz fragment tekstu i kliknij - stanie się klikalny (klik = kopiowanie do schowka)"
            )
            btn_click.clicked.connect(self._toggle_clickable)
            toolbar.addWidget(btn_click)
            btn_img_smaller = QPushButton("🖼−")
            btn_img_smaller.setStyleSheet(self._btn_style)
            btn_img_smaller.setToolTip("Kliknij w obrazek w treści, potem tutaj - zmniejsza go o 15%")
            btn_img_smaller.clicked.connect(lambda: self._resize_image(0.85))
            toolbar.addWidget(btn_img_smaller)
            btn_img_bigger = QPushButton("🖼+")
            btn_img_bigger.setStyleSheet(self._btn_style)
            btn_img_bigger.setToolTip("Kliknij w obrazek w treści, potem tutaj - powiększa go o 15%")
            btn_img_bigger.clicked.connect(lambda: self._resize_image(1.15))
            toolbar.addWidget(btn_img_bigger)
            btn_table = QPushButton("▦  Tabela")
            btn_table.setStyleSheet(self._btn_style)
            btn_table.setToolTip("Wstawia nową tabelę (podajesz liczbę wierszy i kolumn)")
            btn_table.clicked.connect(self._insert_table)
            toolbar.addWidget(btn_table)
            btn_table_row = QPushButton("+Wiersz")
            btn_table_row.setStyleSheet(self._btn_style)
            btn_table_row.setToolTip("Kliknij w komórkę tabeli, potem tutaj - dodaje wiersz pod nią")
            btn_table_row.clicked.connect(self._table_add_row)
            toolbar.addWidget(btn_table_row)
            btn_table_col = QPushButton("+Kolumna")
            btn_table_col.setStyleSheet(self._btn_style)
            btn_table_col.setToolTip("Kliknij w komórkę tabeli, potem tutaj - dodaje kolumnę po jej prawej")
            btn_table_col.clicked.connect(self._table_add_column)
            toolbar.addWidget(btn_table_col)
            self._article_edit_only_widgets += [
                btn_bold, btn_list, btn_heading, btn_click, btn_img_smaller, btn_img_bigger,
                btn_table, btn_table_row, btn_table_col,
            ]
        # Stretch idzie PO wszystkich przyciskach (nie przed A-/A+) - inaczej gdy
        # przyciski edycji chowają się w trybie podglądu, stretch i tak zajmował tę
        # samą przestrzeń i wypychał A-/A+ daleko w prawo, zostawiając pustą dziurę
        # zamiast po prostu skompresować widoczne przyciski razem po lewej.
        # Powiększanie/zmniejszanie tekstu to preferencja widoku, nie edycja danych -
        # dostępne zawsze, niezależnie od KOMPENDIUM_EDIT_ENABLED. Robione ręczną zmianą
        # font-size w stylesheet (a nie QTextEdit.zoomIn/zoomOut), bo stylesheet i tak
        # nadpisuje font widgetu przy każdym odświeżeniu - zoomIn/zoomOut są przez to
        # niewidoczne.
        zoom_btn_style = (
            self._btn_style.replace("padding:4px 10px;", "padding:4px 2px;")
        )
        btn_zoom_out = QPushButton("A−")
        btn_zoom_out.setFixedWidth(36)
        btn_zoom_out.setStyleSheet(zoom_btn_style)
        btn_zoom_out.setToolTip("Zmniejsz czcionkę")
        btn_zoom_out.clicked.connect(self._zoom_out)
        toolbar.addWidget(btn_zoom_out)
        btn_zoom_in = QPushButton("A+")
        btn_zoom_in.setFixedWidth(36)
        btn_zoom_in.setStyleSheet(zoom_btn_style)
        btn_zoom_in.setToolTip("Powiększ czcionkę")
        btn_zoom_in.clicked.connect(self._zoom_in)
        toolbar.addWidget(btn_zoom_in)
        btn_source = QPushButton("🔗  Źródło")
        btn_source.setStyleSheet(self._btn_style)
        btn_source.setToolTip("Otwiera link do źródła artykułu w przeglądarce")
        btn_source.clicked.connect(self._on_open_article_source)
        toolbar.addWidget(btn_source)
        toolbar.addStretch()
        editor.addLayout(toolbar)

        # Ukryty domyślnie (nie tylko pusty tekst) - inaczej pusta etykieta i tak
        # rezerwuje wysokość linii, zostawiając stałą, nic-nie-znaczącą przerwę nad
        # treścią artykułu, w obu trybach (edycji i podglądu).
        self._article_status_lbl = QLabel("")
        self._article_status_lbl.setStyleSheet("color:#4ade80; font-size:8.5pt; font-weight:600;")
        self._article_status_lbl.setVisible(False)
        editor.addWidget(self._article_status_lbl)

        self._article_body = ArticleBodyTextEdit(is_light=self._is_light)
        self._article_body.on_copy = self._on_article_text_copied
        self._article_body.setStyleSheet(self._body_style)
        self._article_body.setReadOnly(not self._edit_enabled)
        editor.addWidget(self._article_body, 1)

        # Przyciski Nowy/Usuń/Zapisz NIE są tu - są w dolnym pasku (razem z
        # Eksportuj/Wczytaj), żeby zwolnić więcej miejsca w pionie dla treści
        # artykułu. Budowane w _setup_ui, po utworzeniu obu zakładek.

        splitter.addWidget(editor_w)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, 1)

        # Stan zwinięcia listy jest trwały między sesjami (jak reszta preferencji UI w
        # tej aplikacji) - kto raz świadomie schował listę, żeby mieć więcej miejsca na
        # treść, dostaje ten sam widok przy następnym otwarciu Bazy wiedzy.
        if self._db.get_setting("kompendium_list_collapsed", "0") == "1":
            self._set_article_list_collapsed(True, persist=False)
        else:
            self._btn_toggle_list.setText("◀")

        return w

    def _toggle_article_list(self):
        collapsed = self._article_splitter.sizes()[0] <= 1
        self._set_article_list_collapsed(not collapsed, persist=True)

    def _set_article_list_collapsed(self, collapsed: bool, persist: bool):
        total = sum(self._article_splitter.sizes()) or (self.width() - 20)
        if collapsed:
            # Zapamiętujemy AKTUALNĄ szerokość TYLKO jeśli lista faktycznie jest teraz
            # widoczna (>1px) - inaczej (np. przy starcie, zanim splitter dostał swój
            # pierwszy realny rozmiar) zapisalibyśmy 0 jako "szerokość do przywrócenia".
            current = self._article_splitter.sizes()[0]
            if current > 1:
                self._article_list_last_width = current
            self._article_splitter.setSizes([0, total])
            self._btn_toggle_list.setText("▶")
        else:
            left = min(self._article_list_last_width, max(total - 200, 240))
            self._article_splitter.setSizes([left, max(total - left, 200)])
            self._btn_toggle_list.setText("◀")
        if persist:
            self._db.set_setting("kompendium_list_collapsed", "1" if collapsed else "0")

    # ------------------------------------------------------------ zakładka Komendy

    def _build_commands_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self._cmd_search = QLineEdit()
        self._cmd_search.setPlaceholderText("🔍  Szukaj w komendach…")
        self._cmd_search.setClearButtonEnabled(True)
        self._cmd_search.setStyleSheet(self._input_style)
        self._cmd_search.textChanged.connect(self._reload_commands)
        search_row.addWidget(self._cmd_search, 1)
        self._cmd_brand_filter = QComboBox()
        self._cmd_brand_filter.addItem("Wszystkie marki", "")
        for b in _BRANDS:
            self._cmd_brand_filter.addItem(b, b)
        self._cmd_brand_filter.setStyleSheet(self._input_style)
        self._cmd_brand_filter.currentIndexChanged.connect(self._reload_commands)
        search_row.addWidget(self._cmd_brand_filter)
        self._cmd_type_filter = QComboBox()
        self._cmd_type_filter.addItem("Wszystkie typy", "")
        for t in _COMMAND_TYPES:
            self._cmd_type_filter.addItem(t, t)
        self._cmd_type_filter.setStyleSheet(self._input_style)
        self._cmd_type_filter.currentIndexChanged.connect(self._reload_commands)
        search_row.addWidget(self._cmd_type_filter)
        outer.addLayout(search_row)

        self._cmd_table = QTableWidget(0, 4)
        self._cmd_table.setHorizontalHeaderLabels(["Marka", "Typ", "Komenda", "Opis"])
        self._cmd_table.setStyleSheet(self._list_style)
        self._cmd_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # SelectItems (nie SelectRows) - klik podświetla i "flashuje" tylko klikniętą
        # komórkę, a nie cały wiersz. Zaznaczanie całego wiersza (przy edycji z menu
        # kontekstowego) i tak robimy programowo przez selectRow().
        self._cmd_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self._cmd_table.verticalHeader().setVisible(False)
        self._cmd_table.setToolTip("Kliknij wiersz, żeby skopiować komendę do schowka")
        hdr = self._cmd_table.horizontalHeader()
        # Interactive zamiast ResizeToContents/Stretch - użytkownik może ręcznie
        # przeciągać granice kolumn i zmieniać ich kolejność (drag nagłówka).
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionsMovable(True)
        hdr.setStretchLastSection(True)
        self._cmd_table.setColumnWidth(0, 110)
        self._cmd_table.setColumnWidth(1, 80)
        self._cmd_table.setColumnWidth(2, 260)
        self._cmd_table.itemSelectionChanged.connect(self._on_command_selected)
        self._cmd_table.cellClicked.connect(self._on_command_row_clicked)
        if self._edit_enabled:
            # Zwykły klik kopiuje komendę do schowka (najczęstsza akcja) - edycja
            # wchodzi przez osobne menu kontekstowe (PPM), żeby nie mylić dwóch
            # różnych akcji pod tym samym kliknięciem.
            self._cmd_table.setContextMenuPolicy(Qt.CustomContextMenu)
            self._cmd_table.customContextMenuRequested.connect(self._on_command_context_menu)
        outer.addWidget(self._cmd_table, 1)

        if self._edit_enabled:
            btn_row = QHBoxLayout()
            btn_row.setSpacing(8)
            btn_new = QPushButton("＋ Dodaj komendę")
            btn_new.setStyleSheet(self._btn_style)
            btn_new.setToolTip("Otwiera okienko do wpisania nowej komendy")
            btn_new.clicked.connect(self._on_add_command_dialog)
            btn_row.addWidget(btn_new)
            btn_row.addStretch()
            btn_delete = QPushButton("🗑  Usuń")
            btn_delete.setStyleSheet(self._btn_style)
            btn_delete.setToolTip("Usuwa zaznaczoną komendę")
            btn_delete.clicked.connect(self._on_delete_command)
            btn_row.addWidget(btn_delete)
            outer.addLayout(btn_row)

        return w

    # ------------------------------------------------------------ artykuły - logika

    def _reload_articles(self):
        query = self._article_search.text().strip()
        rows = self._db.search_kompendium_articles(
            query, self._article_context_fields, absent_hardware=self._absent_hardware
        )
        # Wyniki są już posortowane po ważonej trafności (tekst + wszystkie pola
        # kontekstu) - nie ukrywamy słabiej dopasowanych artykułów, tylko wyróżniamy
        # i od razu otwieramy najlepszy.
        self._article_list.blockSignals(True)
        self._article_list.clear()
        # Gwiazdka/auto-wybór "najlepszego dopasowania" pokazuje się zawsze, gdy silnik
        # w ogóle coś ranked - albo wpisane zapytanie, albo niepuste pole kontekstu.
        # search_kompendium_articles zawsze liczy wagę dla każdego artykułu (tag/tytuł/
        # treść/fuzzy/IDF/ważność pola) i zawsze zwraca listę posortowaną wynikiem tego
        # liczenia - skoro więc jakiś wynik i tak został obliczony, to na górze zawsze
        # jest realny "najlepszy" wybór silnika, nawet gdy trafienie jest słabe.
        # UWAGA: self._article_context_fields to zawsze pełny słownik z formularza
        # (każde pole obecne jako klucz, nawet puste) - bool(dict) byłby więc zawsze
        # True. Liczy się, czy KTÓREKOLWIEK pole ma realną (niepustą) wartość.
        have_hint = bool(query) or any(v.strip() for v in self._article_context_fields.values())
        # "_match_score" jest teraz liczony w OBU trybach (ręczne wpisywanie i sugestia
        # z formularza - patrz search_kompendium_articles) tym samym ważonym silnikiem,
        # nie tylko surowym bm25 - pokazujemy go jako %, ale w skali BEZWZGLĘDNEJ, nie
        # względem najlepszego wyniku w tej (krótkiej) liście. Względna skala potrafiła
        # pokazać 100% przy jednym, słabym, przypadkowym trafieniu (np. firma "BUS
        # Krzysztof Łyziński" trafiająca w tag "bus" - typ pojazdu, kompletnie inny
        # temat) tylko dlatego, że akurat nic lepszego nie było w bazie. CONFIDENT_SCORE
        # to punkt odniesienia "solidne dopasowanie" (mniej więcej: dwa niezależne
        # trafienia w tag o typowej rzadkości) - kalibrowany względem wag w db_manager.
        CONFIDENT_SCORE = 20.0
        show_scores = have_hint and any(
            (row.get("_match_score", 0) or 0) > 0 for row in rows
        )
        for i, row in enumerate(rows):
            label = f"[{row['category']}] {row['title']}" if row["category"] else row["title"]
            is_best = have_hint and i == 0
            pct = None
            if show_scores:
                pct = min(100, round(100 * (row.get("_match_score", 0) or 0) / CONFIDENT_SCORE))
                label = f"{label}  ·  {pct}%"
            item = QListWidgetItem(("★ " if is_best else "") + label)
            item.setData(_ARTICLE_ROLE, row["id"])
            if is_best:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            # Lekkie podświetlenie tłem wg siły dopasowania - widoczne głównie na
            # pozostałych (niezaznaczonych) wierszach, bo zaznaczenie (★, najlepszy)
            # i tak nadpisuje tło swoim własnym kolorem z _list_style. Przydaje się,
            # gdy dwa artykuły mogą być pomocne naraz - widać po kolorze, który silniej.
            if pct is not None:
                if pct >= 70:
                    item.setBackground(QColor("#dcfce7" if self._is_light else "#1a3a2a"))
                elif pct >= 30:
                    item.setBackground(QColor("#fef3c7" if self._is_light else "#3a3410"))
            if show_scores:
                item.setToolTip(
                    "Dopasowanie do formularza: 100% = solidne, pewne dopasowanie "
                    "(niezależnie od tego, co jeszcze jest na liście). Niski % = "
                    "trafienie słabe/przypadkowe - potraktuj z rezerwą."
                )
            elif is_best:
                item.setToolTip("Najlepsze dopasowanie do wyszukiwania")
            self._article_list.addItem(item)
        self._article_list.blockSignals(False)
        if have_hint and rows:
            self._select_article_by_id(rows[0]["id"])

    def _on_article_selected(self):
        items = self._article_list.selectedItems()
        if not items:
            return
        article_id = items[0].data(_ARTICLE_ROLE)
        row = self._db.get_kompendium_article(article_id)
        if not row:
            return
        self._current_article_id = article_id
        self._article_title.setText(row["title"])
        self._article_category.setText(row["category"])
        self._article_tags.setText(row["tags"])
        self._article_source.setText(row.get("source_url", ""))
        self._article_body.setHtml(row["body_html"])

    def _on_new_article(self):
        self._current_article_id = None
        self._article_list.clearSelection()
        self._article_title.clear()
        self._article_category.clear()
        self._article_tags.clear()
        self._article_source.clear()
        self._article_body.clear()
        self._article_title.setFocus()

    def _on_open_article_source(self):
        url = self._article_source.text().strip()
        if not url:
            QMessageBox.information(self, "Brak źródła", "Ten artykuł nie ma podanego linku do źródła.")
            return
        QDesktopServices.openUrl(QUrl(url))

    def _on_toggle_preview_mode(self):
        self._set_preview_mode(not self._article_preview_mode)

    def _set_preview_mode(self, preview: bool):
        """Podgląd = artykuł wygląda tak, jak zobaczy go zwykły użytkownik (bez
        narzędzi edycji), bez przełączania KOMPENDIUM_EDIT_ENABLED w kodzie - to
        przełącznik tylko w tym oknie, dostępny wyłącznie gdy edycja jest w ogóle
        włączona (czyli tylko u mnie)."""
        self._article_preview_mode = preview
        self._article_title.setReadOnly(preview)
        self._article_category.setReadOnly(preview)
        self._article_tags.setReadOnly(preview)
        self._article_source.setReadOnly(preview)
        self._article_body.setReadOnly(preview)
        for w in self._article_edit_only_widgets:
            w.setVisible(not preview)
        if preview:
            self._btn_article_mode.setText("✎  Tryb edycji")
            self._btn_article_mode.setToolTip("Wróć do edycji artykułu")
        else:
            self._btn_article_mode.setText("👁  Podgląd")
            self._btn_article_mode.setToolTip(
                "Pokazuje artykuł tak, jak zobaczy go zwykły użytkownik (bez narzędzi edycji)"
            )

    def _on_save_article(self):
        title = self._article_title.text().strip()
        if not title:
            QMessageBox.information(self, "Brak tytułu", "Wpisz tytuł artykułu.")
            return
        category = self._article_category.text().strip()
        tags = self._article_tags.text().strip()
        source_url = self._article_source.text().strip()
        body_html = self._article_body.toHtml()
        body_text = self._article_body.toPlainText()

        if self._current_article_id is None:
            self._current_article_id = self._db.add_kompendium_article(
                title, category, tags, body_html, body_text, source_url
            )
        else:
            self._db.update_kompendium_article(
                self._current_article_id, title, category, tags, body_html, body_text, source_url
            )
        # _reload_articles() samo w sobie auto-zaznacza "najlepsze dopasowanie" wg
        # kontekstu (może być INNYM artykułem niż ten właśnie zapisany!) - zapamiętujemy
        # więc zapisane id z góry i wymuszamy je z powrotem PO reloadzie, inaczej zapis
        # potrafił po cichu przełączyć widok na zupełnie inny artykuł.
        saved_id = self._current_article_id
        self._reload_articles()
        self._select_article_by_id(saved_id)
        self._current_article_id = saved_id
        self._article_status_lbl.setText("✓ Zapisano")
        self._article_status_lbl.setVisible(True)
        QTimer.singleShot(2000, lambda: self._article_status_lbl.setVisible(False))

    def _on_ctrl_s(self):
        # Ctrl+S zapisuje artykuł tylko gdy jesteśmy na zakładce "Baza wiedzy"
        # (nie Komendy) i nie w trybie podglądu (tam i tak wszystko jest read-only).
        if self._tabs.currentIndex() == 0 and not self._article_preview_mode:
            self._on_save_article()

    def _on_delete_article(self):
        if self._current_article_id is None:
            return
        if QMessageBox.question(
            self, "Usuń artykuł", "Na pewno usunąć ten artykuł?"
        ) != QMessageBox.Yes:
            return
        self._db.delete_kompendium_article(self._current_article_id)
        self._on_new_article()
        self._reload_articles()

    def _select_article_by_id(self, article_id: int):
        for i in range(self._article_list.count()):
            item = self._article_list.item(i)
            if item.data(_ARTICLE_ROLE) == article_id:
                self._article_list.setCurrentItem(item)
                return

    # --- toolbar formatowania ---

    def _toggle_bold(self):
        cursor = self._article_body.textCursor()
        fmt = cursor.charFormat()
        new_weight = QFont.Normal if fmt.fontWeight() == QFont.Bold else QFont.Bold
        fmt.setFontWeight(new_weight)
        if cursor.hasSelection():
            cursor.mergeCharFormat(fmt)
        self._article_body.setCurrentCharFormat(fmt)
        self._article_body.setFocus()

    def _insert_bullet_list(self):
        cursor = self._article_body.textCursor()
        cursor.insertList(QTextListFormat.ListDisc)
        self._article_body.setFocus()

    # Nagłówek jest o tyle większy od aktualnego (zoomowanego) rozmiaru tekstu -
    # relatywnie, a nie na sztywno w punktach, żeby A+/A- dalej działało na
    # nagłówkach po ich ustawieniu (patrz _rescale_explicit_fonts).
    _HEADING_BUMP = 3.5

    def _insert_heading(self):
        # Toggle: klik na tekście, który już jest nagłówkiem, cofa go do normalnego
        # rozmiaru/wagi - bez tego każdy kolejny klik tylko odtwarzał to samo, większe
        # 13pt, ale nie było sposobu, żeby to cofnąć inaczej niż Ctrl+Z.
        cursor = self._article_body.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.LineUnderCursor)
        start_fmt = _format_at(self._article_body.document(), cursor.selectionStart())
        is_heading = bool(start_fmt) and start_fmt.fontWeight() == QFont.Bold and (
            start_fmt.fontPointSize() >= self._article_font_pt + self._HEADING_BUMP - 0.5
        )
        fmt = QTextCharFormat()
        if is_heading:
            fmt.setFontWeight(QFont.Normal)
            fmt.setFontPointSize(self._article_font_pt)
        else:
            fmt.setFontWeight(QFont.Bold)
            fmt.setFontPointSize(self._article_font_pt + self._HEADING_BUMP)
        cursor.mergeCharFormat(fmt)
        self._article_body.setFocus()

    def _rescale_explicit_fonts(self, delta: float):
        """A+/A- zmienia domyślny rozmiar czcionki edytora przez stylesheet, ale to nie
        rusza fragmentów z jawnie ustawionym rozmiarem (nagłówki, wcześniej odznaczone
        nagłówki) - bez tego zoom przestawał działać na takich fragmentach. Przesuwamy
        więc każdy fragment z jawnym rozmiarem o tę samą deltę, żeby zoom obejmował
        cały dokument, nie tylko zwykły tekst."""
        doc = self._article_body.document()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    old_size = frag.charFormat().fontPointSize()
                    if old_size > 0:
                        new_size = max(6.0, old_size + delta)
                        c = QTextCursor(doc)
                        c.setPosition(frag.position())
                        c.setPosition(frag.position() + frag.length(), QTextCursor.KeepAnchor)
                        new_fmt = QTextCharFormat()
                        new_fmt.setFontPointSize(new_size)
                        c.mergeCharFormat(new_fmt)
                it += 1
            block = block.next()

    def _toggle_clickable(self):
        cursor = self._article_body.textCursor()
        if not cursor.hasSelection():
            QMessageBox.information(self, "Baza wiedzy", "Najpierw zaznacz fragment tekstu do oznaczenia.")
            return
        text = cursor.selectedText().replace(" ", "\n")
        # Format PIERWSZEGO znaku zaznaczenia, jednoznacznie (patrz _format_at) -
        # cursor.charFormat() na zaznaczeniu zależy od kierunku przeciągnięcia myszą,
        # więc potrafił dawać różne wyniki dla tego samego fragmentu w drugą stronę
        # (przez co "odznacz" czasem nie działało).
        start_fmt = _format_at(self._article_body.document(), cursor.selectionStart())
        already = bool(start_fmt) and start_fmt.isAnchor()
        fmt = QTextCharFormat()
        if already:
            fmt.setAnchor(False)
            fmt.setAnchorHref("")
            fmt.setFontUnderline(False)
            fmt.setFontFamily(self._article_body.font().family())
            fmt.setFontWeight(QFont.Normal)
            fmt.setForeground(QBrush(QColor(self._text_color)))
            fmt.setBackground(QBrush(Qt.transparent))
        else:
            # "Chip" w stylu inline-code zamiast płaskiego niebieskiego podświetlenia -
            # monospace + stonowany fiolet, bez podkreślenia (podkreślenie kojarzy się
            # z linkiem, a to ma wyglądać jak klikalny fragment kodu/komendy).
            fmt.setAnchor(True)
            fmt.setAnchorHref(text)
            fmt.setFontUnderline(False)
            fmt.setFontFamily("Consolas")
            fmt.setFontWeight(QFont.DemiBold)
            fmt.setForeground(QColor("#6d28d9" if self._is_light else "#c4b5fd"))
            fmt.setBackground(QColor("#ede9fe" if self._is_light else "#1e1b34"))
        cursor.mergeCharFormat(fmt)
        self._article_body.setFocus()

    def _on_article_text_copied(self, text: str):
        shown = text if len(text) <= 60 else text[:57] + "…"
        self._article_status_lbl.setText(f"✓ Skopiowano: {shown}")
        self._article_status_lbl.setVisible(True)
        QTimer.singleShot(2000, lambda: self._article_status_lbl.setVisible(False))

    def _zoom_in(self):
        old = self._article_font_pt
        self._article_font_pt = min(self._article_font_pt + 1, 24)
        delta = self._article_font_pt - old
        if delta:
            self._article_body.setStyleSheet(self._body_stylesheet(self._article_font_pt))
            self._rescale_explicit_fonts(delta)

    def _zoom_out(self):
        old = self._article_font_pt
        self._article_font_pt = max(self._article_font_pt - 1, 6)
        delta = self._article_font_pt - old
        if delta:
            self._article_body.setStyleSheet(self._body_stylesheet(self._article_font_pt))
            self._rescale_explicit_fonts(delta)

    # --- zmiana rozmiaru wklejonego obrazka ---

    def _image_at_cursor(self):
        """Zwraca (pozycja, QTextImageFormat) obrazka pod/tuż przed kursorem - klik w
        obrazek zwykle stawia kursor zaraz za nim, więc sprawdzamy obie pozycje.

        Iterujemy po fragmentach dokumentu (jak w _rescale_explicit_fonts), a NIE przez
        _format_at (zaznaczenie 1 znaku) - ta druga metoda potrafiła mylnie zgłosić
        "format obrazka" dla znaku podziału akapitu leżącego TUŻ ZA obrazkiem (Qt
        zwraca dla takiej granicy niejednoznaczny/odziedziczony format), więc łapała
        złą pozycję i "zmiana rozmiaru" nic nie robiła, gdy obrazek stał na własnym
        akapicie (czyli w praktyce prawie zawsze po wklejeniu)."""
        click_pos = self._article_body.textCursor().position()
        doc = self._article_body.document()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    fmt = frag.charFormat()
                    if fmt.isImageFormat():
                        start = frag.position()
                        end = start + frag.length()
                        if start <= click_pos <= end:
                            return start, fmt.toImageFormat()
                it += 1
            block = block.next()
        return None

    @staticmethod
    def _image_native_size(img_fmt: QTextImageFormat):
        """Rozmiar obrazka wprost z zakodowanych danych (data: URI) - niezależny od
        tego, czy dokument ma go akurat zarejestrowanego jako resource."""
        name = img_fmt.name()
        if not name.startswith("data:") or "base64," not in name:
            return None
        try:
            raw = base64.b64decode(name.split("base64,", 1)[1])
            img = QImage.fromData(raw)
        except Exception:
            return None
        return (img.width(), img.height()) if not img.isNull() else None

    def _resize_image(self, factor: float):
        found = self._image_at_cursor()
        if not found:
            QMessageBox.information(
                self, "Baza wiedzy", "Kliknij najpierw w obrazek, który chcesz zmienić."
            )
            return
        pos, img_fmt = found
        width, height = img_fmt.width(), img_fmt.height()
        if width <= 0 or height <= 0:
            native = self._image_native_size(img_fmt)
            if not native:
                return
            width, height = native
        new_fmt = QTextImageFormat(img_fmt)
        new_fmt.setWidth(max(40.0, width * factor))
        new_fmt.setHeight(max(24.0, height * factor))
        doc = self._article_body.document()
        c = QTextCursor(doc)
        c.setPosition(pos)
        c.setPosition(pos + 1, QTextCursor.KeepAnchor)
        c.setCharFormat(new_fmt)

    def _insert_table(self):
        dlg = _InsertTableDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        rows, cols = dlg.rows(), dlg.cols()
        fmt = QTextTableFormat()
        fmt.setBorder(1)
        fmt.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
        fmt.setCellPadding(4)
        fmt.setCellSpacing(0)
        fmt.setWidth(QTextLength(QTextLength.PercentageLength, 100))
        self._article_body.textCursor().insertTable(rows, cols, fmt)

    def _table_add_row(self):
        table = self._article_body.textCursor().currentTable()
        if table is None:
            QMessageBox.information(self, "Baza wiedzy", "Kliknij najpierw w komórkę tabeli.")
            return
        cell = table.cellAt(self._article_body.textCursor())
        table.insertRows(cell.row() + 1, 1)

    def _table_add_column(self):
        table = self._article_body.textCursor().currentTable()
        if table is None:
            QMessageBox.information(self, "Baza wiedzy", "Kliknij najpierw w komórkę tabeli.")
            return
        cell = table.cellAt(self._article_body.textCursor())
        table.insertColumns(cell.column() + 1, 1)

    # ------------------------------------------------------------ komendy - logika

    def _reload_commands(self):
        query = self._cmd_search.text().strip()
        brand = self._cmd_brand_filter.currentData() or ""
        typ = self._cmd_type_filter.currentData() or ""
        rows = self._db.search_kompendium_commands(query, brand, typ)
        self._cmd_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, key in enumerate(("brand", "typ", "command", "description")):
                item = QTableWidgetItem(row[key])
                if c == 0:
                    item.setData(_COMMAND_ROLE, row["id"])
                if query and r == 0:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                self._cmd_table.setItem(r, c, item)
        if query and rows:
            self._cmd_table.selectRow(0)

    def _on_command_row_clicked(self, row: int, col: int):
        # Kopiowanie tylko po kliknięciu w samą kolumnę "Komenda" (2) - klik w Markę,
        # Typ czy Opis nic nie kopiuje (te kolumny są tylko do odczytu/kontekstu).
        if col != 2:
            return
        item = self._cmd_table.item(row, 2)
        if not item or not item.text():
            return
        QApplication.clipboard().setText(item.text())
        self._flash_table_cell(self._cmd_table, row, col)

    def _flash_table_cell(self, table: QTableWidget, row: int, col: int):
        """Krótki 'błysk' klikniętego wiersza. Klik od razu zaznacza cały wiersz
        (SelectRows), a Qt maluje podświetlenie zaznaczenia NAD tłem ustawionym przez
        item.setBackground() - więc samo ustawianie tła na itemie było niewidoczne.
        Zamiast tego na chwilę podmieniamy kolor stylu ':selected' na zielony."""
        base_style = table.styleSheet()
        flash_style = base_style + "QTableWidget::item:selected{background:#4ade80;color:#052e16;}"
        table.setStyleSheet(flash_style)
        QTimer.singleShot(220, lambda: table.setStyleSheet(base_style))

    def _on_command_selected(self):
        if not self._edit_enabled:
            return
        items = self._cmd_table.selectedItems()
        if not items:
            return
        row = items[0].row()
        self._current_command_id = self._cmd_table.item(row, 0).data(_COMMAND_ROLE)

    def _on_new_command(self):
        # Resetuje bieżąco wybraną komendę - używane po imporcie z pliku, gdzie nie ma
        # sensu zostawiać zaznaczenia sprzed nadpisania danych.
        if not self._edit_enabled:
            return
        self._current_command_id = None
        self._cmd_table.clearSelection()

    def _on_add_command_dialog(self):
        dlg = _CommandEditDialog(
            self, self._btn_style, self._input_style, self._is_light, title="Nowa komenda"
        )
        if dlg.exec() == QDialog.Accepted:
            brand, typ, command, description = dlg.values()
            self._current_command_id = self._db.add_kompendium_command(brand, command, description, typ)
            self._reload_commands()

    def _on_edit_command_dialog(self, row: int):
        cmd_id = self._cmd_table.item(row, 0).data(_COMMAND_ROLE)
        dlg = _CommandEditDialog(
            self, self._btn_style, self._input_style, self._is_light,
            brand=self._cmd_table.item(row, 0).text(),
            typ=self._cmd_table.item(row, 1).text(),
            command=self._cmd_table.item(row, 2).text(),
            description=self._cmd_table.item(row, 3).text(),
            title="Edytuj komendę",
        )
        if dlg.exec() == QDialog.Accepted:
            brand, typ, command, description = dlg.values()
            self._db.update_kompendium_command(cmd_id, brand, command, description, typ)
            self._current_command_id = cmd_id
            self._reload_commands()

    def _on_command_context_menu(self, pos):
        item = self._cmd_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        self._cmd_table.selectRow(row)
        menu = QMenu(self)
        edit_action = menu.addAction("✎  Edytuj")
        chosen = menu.exec(self._cmd_table.viewport().mapToGlobal(pos))
        if chosen == edit_action:
            self._on_edit_command_dialog(row)

    def _on_delete_command(self):
        if self._current_command_id is None:
            return
        if QMessageBox.question(
            self, "Usuń komendę", "Na pewno usunąć tę komendę?"
        ) != QMessageBox.Yes:
            return
        self._db.delete_kompendium_command(self._current_command_id)
        self._on_new_command()
        self._reload_commands()

    # ------------------------------------------------------------ eksport / import pojedynczego artykułu

    def _on_export_single_article(self):
        title = self._article_title.text().strip()
        if not title:
            QMessageBox.information(self, "Brak tytułu", "Wpisz tytuł artykułu przed eksportem.")
            return
        data = {
            "article": {
                "title": title,
                "category": self._article_category.text().strip(),
                "tags": self._article_tags.text().strip(),
                "source_url": self._article_source.text().strip(),
                "body_html": self._article_body.toHtml(),
                "body_text": self._article_body.toPlainText(),
            }
        }
        default_name = f"{_safe_filename(title)}.json"
        path, _ = QFileDialog.getSaveFileName(self, "Eksportuj artykuł", default_name, "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            QMessageBox.critical(self, "Błąd zapisu", str(exc))
            return
        QMessageBox.information(self, "Wyeksportowano", f"Zapisano artykuł do:\n{path}")

    def _on_import_single_article(self):
        path, _ = QFileDialog.getOpenFileName(self, "Wczytaj artykuł", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            QMessageBox.critical(self, "Błąd wczytywania", str(exc))
            return
        article = data.get("article") if isinstance(data, dict) else None
        if article is None:
            QMessageBox.critical(
                self, "Błąd wczytywania",
                "To nie jest plik pojedynczego artykułu (brak klucza \"article\") - "
                "użyj \"Wczytaj Bazę wiedzy z pliku\" dla zbiorczego eksportu."
            )
            return
        title = (article.get("title") or "").strip()
        if not title:
            QMessageBox.critical(self, "Błąd wczytywania", "Artykuł w pliku nie ma tytułu.")
            return
        if QMessageBox.question(
            self, "Wczytaj artykuł",
            f"Dodać nowy artykuł \"{title}\" do Twojej lokalnej bazy?\n"
            "(dodaje jako nowy wpis - NIE nadpisuje ani nie usuwa istniejących artykułów)"
        ) != QMessageBox.Yes:
            return
        new_id = self._db.add_kompendium_article(
            title, article.get("category", ""), article.get("tags", ""),
            article.get("body_html", ""), article.get("body_text", ""), article.get("source_url", ""),
        )
        self._reload_articles()
        self._select_article_by_id(new_id)
        self._current_article_id = new_id
        QMessageBox.information(self, "Wczytano", f"Dodano artykuł \"{title}\".")

    # ------------------------------------------------------------ eksport / import

    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Eksportuj Bazę wiedzy", "baza_wiedzy_export.json", "JSON (*.json)"
        )
        if not path:
            return
        data = self._db.export_kompendium_data()
        # "content_version" rośnie o 1 przy KAŻDYM eksporcie (niezależnie od tego, czy
        # ten konkretny plik trafi potem na gista czy zostanie tylko lokalną kopią
        # zapasową) - to ten sam licznik, który cicha synchronizacja w tle (patrz
        # main.py, KompendiumSyncChecker) porównuje, żeby wiedzieć, czy pobrać nowszą
        # wersję. Wysyłanie pliku "dalej" (na gista) jest w pełni ręczne - eksport sam
        # w sobie niczego automatycznie nie publikuje.
        next_version = int(self._db.get_setting("kompendium_content_version", "0")) + 1
        self._db.set_setting("kompendium_content_version", str(next_version))
        data["content_version"] = next_version
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            QMessageBox.critical(self, "Błąd zapisu", str(exc))
            return
        QMessageBox.information(
            self, "Wyeksportowano",
            f"Zapisano {len(data['articles'])} artykułów i {len(data['commands'])} komend "
            f"(wersja {next_version}) do:\n{path}"
        )

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Wczytaj Bazę wiedzy", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            QMessageBox.critical(self, "Błąd wczytywania", str(exc))
            return

        articles = data.get("articles", [])
        commands = data.get("commands", [])
        if QMessageBox.question(
            self, "Wczytaj Bazę wiedzy",
            f"To NADPISZE Twoją lokalną kopię ({len(articles)} artykułów, {len(commands)} komend "
            "z pliku). Kontynuować?"
        ) != QMessageBox.Yes:
            return

        self._db.import_kompendium_data(data)
        # Plik ma własny "content_version" (patrz _on_export) - jeśli jest wyższy niż to,
        # co lokalnie mamy zanotowane, podbijamy licznik, żeby cicha synchronizacja w tle
        # (main.py) nie próbowała zaraz PONOWNIE nadpisać tego, co właśnie ręcznie
        # wczytaliśmy, jakąś starszą wersją z gista.
        file_version = data.get("content_version")
        if isinstance(file_version, int):
            local_version = int(self._db.get_setting("kompendium_content_version", "0"))
            if file_version > local_version:
                self._db.set_setting("kompendium_content_version", str(file_version))

        self._on_new_article()
        self._on_new_command()
        self._reload_articles()
        self._reload_commands()
        QMessageBox.information(self, "Wczytano", "Baza wiedzy zaktualizowana.")
