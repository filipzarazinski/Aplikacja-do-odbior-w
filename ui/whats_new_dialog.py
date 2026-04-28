"""
ui/whats_new_dialog.py
Dialog wyswietlany przy pierwszym uruchomieniu po aktualizacji.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QScrollArea, QWidget, QToolButton, QSizePolicy
)
from PySide6.QtCore import Qt

from changelog import CHANGELOG


def _ver_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def _make_collapsible_section(parent_lay: QVBoxLayout, entry: dict, expanded: bool) -> None:
    """Dodaje do parent_lay zwijana sekcje dla jednej wersji."""
    version = entry["version"]
    date = entry.get("date", "")

    header_text = f"Wersja {version}"
    if date:
        header_text += f"    {date}"

    btn = QToolButton()
    btn.setText(header_text)
    btn.setCheckable(True)
    btn.setChecked(expanded)
    btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
    btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    btn.setStyleSheet(
        "QToolButton { font-size: 10pt; font-weight: 600; border: none; "
        "text-align: left; padding: 6px 4px; background: transparent; }"
        "QToolButton:hover { color: #94a3b8; }"
    )
    parent_lay.addWidget(btn)

    body = QWidget()
    body_lay = QVBoxLayout(body)
    body_lay.setContentsMargins(16, 0, 0, 8)
    body_lay.setSpacing(2)

    for section, items in entry.get("entries", []):
        lbl_sec = QLabel(section)
        lbl_sec.setStyleSheet("font-size:9pt; font-weight:600; color:#64748b; margin-top:6px;")
        body_lay.addWidget(lbl_sec)
        for item in items:
            lbl = QLabel(f"  •  {item}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size:9pt; padding: 1px 0;")
            body_lay.addWidget(lbl)

    body.setVisible(expanded)
    parent_lay.addWidget(body)

    def _toggle(checked: bool):
        btn.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        body.setVisible(checked)

    btn.toggled.connect(_toggle)


class WhatsNewDialog(QDialog):

    def __init__(self, current_version: str, last_seen_version: str = "", parent=None):
        super().__init__(parent)
        self.setMinimumSize(500, 420)
        self.setModal(True)

        last = _ver_tuple(last_seen_version) if last_seen_version else (0,)

        new_entries = [e for e in CHANGELOG if _ver_tuple(e["version"]) > last]

        if len(new_entries) == 1:
            self.setWindowTitle(f"Co nowego w wersji {current_version}")
        elif last_seen_version:
            self.setWindowTitle(f"Co nowego od wersji {last_seen_version}")
        else:
            self.setWindowTitle(f"Co nowego w aplikacji")

        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(0)

        if new_entries:
            for i, entry in enumerate(new_entries):
                if i > 0:
                    sep = QWidget()
                    sep.setFixedHeight(1)
                    sep.setStyleSheet("background: #2e3340; margin: 4px 0;")
                    lay.addWidget(sep)
                _make_collapsible_section(lay, entry, expanded=(i == 0))
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
