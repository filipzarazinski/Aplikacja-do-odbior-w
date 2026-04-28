"""
ui/whats_new_dialog.py
Dialog "Co nowego" wyswietlany przy pierwszym uruchomieniu po aktualizacji.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QScrollArea, QWidget
)
from PySide6.QtCore import Qt

from changelog import CHANGELOG


class WhatsNewDialog(QDialog):

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Co nowego w wersji {current_version}")
        self.setMinimumSize(480, 400)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(6)

        entry = next((e for e in CHANGELOG if e["version"] == current_version), None)
        if entry:
            _render_release(lay, entry, is_current=True)
        else:
            lbl = QLabel("Brak opisu dla tej wersji.")
            lbl.setStyleSheet("color: #94a3b8;")
            lay.addWidget(lbl)

        lay.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 10, 16, 12)
        btn_row.addStretch()
        btn_ok = QPushButton("OK")
        btn_ok.setObjectName("btn_primary")
        btn_ok.setFixedSize(90, 28)
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)


def _render_release(lay: QVBoxLayout, entry: dict, is_current: bool = False):
    version = entry["version"]
    date = entry.get("date", "")

    header_text = f"Wersja {version}"
    if date:
        header_text += f"   <span style='font-size:9pt; color:#64748b;'>{date}</span>"

    lbl_ver = QLabel(header_text)
    lbl_ver.setTextFormat(Qt.RichText)
    size = "13pt" if is_current else "11pt"
    lbl_ver.setStyleSheet(f"font-size:{size}; font-weight:600; margin-bottom:6px;")
    lay.addWidget(lbl_ver)

    for section, items in entry.get("entries", []):
        lbl_sec = QLabel(section)
        lbl_sec.setStyleSheet("font-size:9.5pt; font-weight:600; color:#64748b; margin-top:8px;")
        lay.addWidget(lbl_sec)
        for item in items:
            lbl_item = QLabel(f"  •  {item}")
            lbl_item.setWordWrap(True)
            lbl_item.setStyleSheet("font-size:9pt; padding: 1px 0;")
            lay.addWidget(lbl_item)
