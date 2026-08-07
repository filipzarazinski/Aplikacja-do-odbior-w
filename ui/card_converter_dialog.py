"""
ui/card_converter_dialog.py
----------------------------
Przelicznik numerów kart Dallas/iButton (np. 01 <-> AD).
Logika 1:1 odtworzona z arkusza Excel "Przelicznik_kart_dallas.xlsm"
(CRC-8 Dallas/Maxim, poly 0x31 odbity, init=0, brak finalnego XOR).
"""
import re

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPlainTextEdit,
    QPushButton, QApplication,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from database.db_manager import DatabaseManager

_CRC8_MAXIM_TABLE = [
    0,94,188,226,97,63,221,131,194,156,126,32,163,253,31,65,157,195,33,127,
    252,162,64,30,95,1,227,189,62,96,130,220,35,125,159,193,66,28,254,160,
    225,191,93,3,128,222,60,98,190,224,2,92,223,129,99,61,124,34,192,158,
    29,67,161,255,70,24,250,164,39,121,155,197,132,218,56,102,229,187,89,7,
    219,133,103,57,186,228,6,88,25,71,165,251,120,38,196,154,101,59,217,135,
    4,90,184,230,167,249,27,69,198,152,122,36,248,166,68,26,153,199,37,123,
    58,100,134,216,91,5,231,185,140,210,48,110,237,179,81,15,78,16,242,172,
    47,113,147,205,17,79,173,243,112,46,204,146,211,141,111,49,178,236,14,80,
    175,241,19,77,206,144,114,44,109,51,209,143,12,82,176,238,50,108,142,208,
    83,13,239,177,240,174,76,18,145,207,45,115,202,148,118,40,171,245,23,73,
    8,86,180,234,105,55,213,139,87,9,235,181,54,104,138,212,149,203,41,119,
    244,170,72,22,233,183,85,11,136,214,52,106,43,117,151,201,74,20,246,168,
    116,42,200,150,21,75,169,247,182,232,10,84,215,137,107,53,
]


def dallas_maxim_crc8(data: bytes) -> int:
    """CRC-8/MAXIM (Dallas 1-Wire), init=0, brak finalnego XOR."""
    crc = 0
    for b in data:
        crc = _CRC8_MAXIM_TABLE[crc ^ b]
    return crc


def convert_card(card_hex: str, new_suffix: str) -> str:
    """Konwertuje 16-znakowy hex numer karty na nowe zakończenie (np. '01' <-> 'AD')."""
    card_hex = card_hex.strip().upper()
    if not re.fullmatch(r"[0-9A-F]{16}", card_hex):
        raise ValueError("musi mieć 16 znaków hex (0-9, A-F)")

    serial = card_hex[4:14]  # S1..S5, 5 bajtów, bez zmian
    reversed_serial = serial[8:10] + serial[6:8] + serial[4:6] + serial[2:4] + serial[0:2]

    new_suffix = new_suffix.strip().upper()
    buf_hex = new_suffix + reversed_serial + "00"  # 7 bajtów
    crc = dallas_maxim_crc8(bytes.fromhex(buf_hex))

    return f"{crc:02X}00{serial}{new_suffix}"


def _wrap_lines(text: str, tag: str) -> str:
    """Owija każdą niepustą linię w <tag>...</tag> (np. karta -> <binary>karta</binary>).
    Zdejmuje istniejące tagi <text>/<binary>, żeby kolejne kliknięcie nie zagnieżdżało ich ponownie."""
    result = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            result.append(line)
            continue
        inner = re.sub(r"^<(?:text|binary)>|</(?:text|binary)>$", "", stripped, flags=re.IGNORECASE)
        result.append(f"<{tag}>{inner}</{tag}>")
    return "\n".join(result)


class CardConverterWidget(QWidget):
    """Widget przelicznika numerów kart Dallas (np. 01 <-> AD). Bez stanu w bazie."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_light = DatabaseManager.instance().get_setting("theme_mode", "dark") == "light"
        self._setup_ui()

    def _setup_ui(self):
        is_light = self._is_light
        bg = "#f8fafc" if is_light else "#1a1d23"
        panel = "#ffffff" if is_light else "#22262f"
        border = "#cbd5e1" if is_light else "#3a4150"
        text = "#0f172a" if is_light else "#e2e8f0"
        muted = "#64748b" if is_light else "#94a3b8"
        input_style = (
            f"QPlainTextEdit{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:4px;padding:6px 8px;font-size:10pt;}}"
            f"QPlainTextEdit:focus{{border-color:#3b82f6;}}"
        )
        btn_style = (
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:3px;font-size:8.5pt;font-weight:600;padding:4px 10px;}}"
            f"QPushButton:hover{{border-color:#3b82f6;color:#3b82f6;}}"
        )
        link_btn_style = (
            f"QPushButton{{background:transparent;color:{muted};border:none;"
            f"font-size:8pt;text-decoration:underline;}}"
            f"QPushButton:hover{{color:#3b82f6;}}"
        )

        self.setStyleSheet(f"QWidget{{background:{bg};}} QLabel{{color:{text};}}")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hint = QLabel(
            "Wklej jeden lub wiele numerów kart (16 znaków hex), po jednym w wierszu - "
            "tak jak kolumna w Excelu. Serial zostaje bez zmian, zmienia się tylko "
            "końcówka karty i suma kontrolna CRC."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{muted}; font-size:8pt;")
        root.addWidget(hint)

        # Grid: kolumny wejście/wynik mają identyczną szerokość (setColumnStretch),
        # a wiersz z polami tekstowymi jako jedyny ma rowStretch - dzięki temu przy
        # rozciąganiu okna rosną tylko pola tekstowe, nie przyciski/etykiety.
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)

        in_head = QHBoxLayout()
        in_head.setSpacing(8)
        in_head.addWidget(QLabel("Karty wejściowe"))
        in_head.addStretch()
        self._btn_copy_input = QPushButton("Kopiuj")
        self._btn_copy_input.setStyleSheet(link_btn_style)
        self._btn_copy_input.setCursor(Qt.PointingHandCursor)
        in_head.addWidget(self._btn_copy_input)
        grid.addLayout(in_head, 0, 0)

        out_head = QHBoxLayout()
        out_head.setSpacing(8)
        out_head.addWidget(QLabel("Wynik"))
        out_head.addStretch()
        self._btn_copy_output = QPushButton("Kopiuj")
        self._btn_copy_output.setStyleSheet(link_btn_style)
        self._btn_copy_output.setCursor(Qt.PointingHandCursor)
        out_head.addWidget(self._btn_copy_output)
        grid.addLayout(out_head, 0, 1)

        self._input_edit = QPlainTextEdit()
        self._input_edit.setPlaceholderText("BF003100CCFC4AAD\n3500660044355EAD\n...")
        self._input_edit.setStyleSheet(input_style)
        self._input_edit.setFont(QFont("Consolas", 10))
        self._input_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        grid.addWidget(self._input_edit, 1, 0)

        self._output_edit = QPlainTextEdit()
        self._output_edit.setReadOnly(True)
        self._output_edit.setStyleSheet(input_style)
        self._output_edit.setFont(QFont("Consolas", 10))
        self._output_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        grid.addWidget(self._output_edit, 1, 1)

        root.addLayout(grid, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_to_01 = QPushButton("→ 01")
        self._btn_to_01.setStyleSheet(btn_style)
        self._btn_to_ad = QPushButton("→ AD")
        self._btn_to_ad.setStyleSheet(btn_style)
        btn_row.addWidget(self._btn_to_01)
        btn_row.addWidget(self._btn_to_ad)
        self._btn_format_text = QPushButton("Zapisz w formacie <text>")
        self._btn_format_text.setStyleSheet(btn_style)
        self._btn_format_binary = QPushButton("Zapisz w formacie <binary>")
        self._btn_format_binary.setStyleSheet(btn_style)
        btn_row.addWidget(self._btn_format_text)
        btn_row.addWidget(self._btn_format_binary)
        btn_row.addStretch()
        self._summary_lbl = QLabel(" ")
        self._summary_lbl.setStyleSheet(f"color:{muted}; font-size:8pt;")
        btn_row.addWidget(self._summary_lbl)
        root.addLayout(btn_row)

        self._btn_to_01.clicked.connect(lambda: self._convert_all("01"))
        self._btn_to_ad.clicked.connect(lambda: self._convert_all("AD"))
        self._btn_copy_input.clicked.connect(lambda: self._copy_edit(self._input_edit, self._btn_copy_input))
        self._btn_copy_output.clicked.connect(lambda: self._copy_edit(self._output_edit, self._btn_copy_output))
        self._btn_format_text.clicked.connect(lambda: self._apply_format("text"))
        self._btn_format_binary.clicked.connect(lambda: self._apply_format("binary"))

    def _apply_format(self, tag: str):
        for edit in (self._input_edit, self._output_edit):
            was_read_only = edit.isReadOnly()
            edit.setReadOnly(False)
            edit.setPlainText(_wrap_lines(edit.toPlainText(), tag))
            edit.setReadOnly(was_read_only)

    def _convert_all(self, new_suffix: str):
        lines = self._input_edit.toPlainText().splitlines()
        results = []
        ok_count = 0
        err_count = 0
        for line in lines:
            card = line.strip()
            if not card:
                results.append("")
                continue
            try:
                results.append(convert_card(card, new_suffix))
                ok_count += 1
            except ValueError as exc:
                results.append(f"❌ {card} — {exc}")
                err_count += 1

        self._output_edit.setPlainText("\n".join(results))
        if err_count:
            self._summary_lbl.setText(f"Przeliczono {ok_count}, błędów: {err_count}")
        else:
            self._summary_lbl.setText(f"Przeliczono {ok_count}" if ok_count else " ")

    def _copy_edit(self, edit: QPlainTextEdit, btn: QPushButton):
        text = edit.toPlainText().strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        orig = btn.text()
        btn.setText("Skopiowano ✓")
        QTimer.singleShot(1000, lambda: btn.setText(orig))
