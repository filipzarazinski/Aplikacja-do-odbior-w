"""
ui/dict_tab.py
Generyczny widget zakładki słownika: tabela + CRUD + import z Excela.
"""
import io
import logging
import shutil
import tempfile
import os
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QDialog, QFormLayout,
    QLineEdit, QDialogButtonBox, QFileDialog,
    QLabel, QMessageBox,
)
from PySide6.QtCore import Qt, Signal

logger = logging.getLogger(__name__)


class _RowDialog(QDialog):
    """Prosty dialog do dodawania/edytowania wiersza słownika."""

    def __init__(self, labels: list[str], values: list[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edycja rekordu")
        self.setModal(True)
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)
        self._edits: list[QLineEdit] = []

        for i, label in enumerate(labels):
            edit = QLineEdit()
            edit.setText(values[i] if values and i < len(values) else "")
            form.addRow(label + ":", edit)
            self._edits.append(edit)

        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_values(self) -> list[str]:
        return [e.text().strip() for e in self._edits]


class DictTab(QWidget):
    """
    Generyczny widget do zarządzania słownikiem.

    Parameters
    ----------
    headers : list[str]
        Nagłówki kolumn widocznych w tabeli.
    loader : () -> list[tuple]
        Pobiera rekordy; każdy tuple: (id, val1, val2, ...).
    saver : (values: list[str]) -> None
        Zapisuje nowy lub edytowany rekord; values = [val1, val2, ...] (bez id).
    deleter : (id: int) -> bool
        Usuwa rekord o podanym id.
    excel_sheet : str
        Nazwa arkusza do importu.
    excel_parser : (worksheet) -> list[list[str]]
        Parsuje arkusz i zwraca listę wierszy (bez id).
    add_labels : list[str]
        Etykiety pól w dialogu Dodaj/Edytuj. Domyślnie = headers.
    commit_fn : callable
        Funkcja do zatwierdzania zmian w DB (np. db.commit()).
    """

    data_changed = Signal()

    def __init__(
        self,
        headers: list[str],
        loader: Callable,
        saver: Callable,
        deleter: Callable,
        excel_sheet: str,
        excel_parser: Callable,
        add_labels: list[str] = None,
        commit_fn: Callable = None,
        parent=None,
    ):
        super().__init__(parent)
        self._headers = headers
        self._loader = loader
        self._saver = saver
        self._deleter = deleter
        self._excel_sheet = excel_sheet
        self._excel_parser = excel_parser
        self._add_labels = add_labels or headers
        self._commit = commit_fn or (lambda: None)
        self._row_ids: list[int] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Info label
        self._info_lbl = QLabel()
        self._info_lbl.setStyleSheet("color: #64748b; font-size: 8.5pt;")
        layout.addWidget(self._info_lbl)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Stretch)
        self._table.doubleClicked.connect(self._on_edit)
        layout.addWidget(self._table, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_add = QPushButton("＋  Dodaj")
        btn_edit = QPushButton("✎  Edytuj")
        btn_del = QPushButton("✕  Usuń")
        btn_imp = QPushButton(f"📥  Importuj z Excel  ({excel_sheet})")
        for b in (btn_add, btn_edit, btn_del):
            b.setFixedHeight(26)
        btn_imp.setFixedHeight(26)
        btn_imp.setObjectName("btn_primary")
        btn_clear = QPushButton("⊘  Usuń wszystkie")
        btn_clear.setFixedHeight(26)
        btn_add.clicked.connect(self._on_add)
        btn_edit.clicked.connect(self._on_edit)
        btn_del.clicked.connect(self._on_delete)
        btn_imp.clicked.connect(self._on_import)
        btn_clear.clicked.connect(self._on_clear_all)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_edit)
        btn_row.addWidget(btn_del)
        btn_row.addWidget(btn_clear)
        btn_row.addStretch()
        btn_row.addWidget(btn_imp)
        layout.addLayout(btn_row)

        self.refresh()

    # ---------------------------------------------------------------- Public

    def refresh(self):
        records = self._loader()
        self._row_ids = []
        self._table.setRowCount(0)
        for rec in records:
            rid = rec[0]
            vals = [str(v) if v is not None else "" for v in rec[1:]]
            row = self._table.rowCount()
            self._table.insertRow(row)
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self._table.setItem(row, col, item)
            self._row_ids.append(rid)
        self._info_lbl.setText(f"Rekordów: {len(records)}")

    # ---------------------------------------------------------------- Slots

    def _on_add(self):
        dlg = _RowDialog(self._add_labels, parent=self)
        if dlg.exec():
            vals = dlg.get_values()
            if any(vals):
                self._saver(vals)
                self._commit()
                self.refresh()
                self.data_changed.emit()

    def _on_edit(self):
        row = self._table.currentRow()
        if row < 0:
            return
        current = [
            self._table.item(row, c).text()
            for c in range(self._table.columnCount())
        ]
        dlg = _RowDialog(self._add_labels, current, parent=self)
        if dlg.exec():
            vals = dlg.get_values()
            if any(vals):
                # Update: delete old + insert new
                self._deleter(self._row_ids[row])
                self._saver(vals)
                self._commit()
                self.refresh()
                self.data_changed.emit()

    def _on_delete(self):
        row = self._table.currentRow()
        if row < 0:
            return
        name = self._table.item(row, 0).text()
        if QMessageBox.question(
            self, "Usuń rekord",
            f"Usunac '{name}'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            self._deleter(self._row_ids[row])
            self._commit()
            self.refresh()
            self.data_changed.emit()

    def _on_clear_all(self):
        if not self._row_ids:
            return
        count = len(self._row_ids)
        if QMessageBox.question(
            self, "Usuń wszystkie rekordy",
            f"Na pewno usunąć wszystkie {count} rekord(ów)?\nTej operacji nie można cofnąć.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            for rid in list(self._row_ids):
                self._deleter(rid)
            self._commit()
            self.refresh()
            self.data_changed.emit()

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Wybierz plik Excel (arkusz: {self._excel_sheet})",
            "", "Pliki Excel (*.xlsx *.xlsm *.xls)"
        )
        if not path:
            return
        try:
            import openpyxl
        except ImportError:
            QMessageBox.warning(self, "Brak biblioteki",
                                "Uruchom: pip install openpyxl")
            return
        try:
            tmp = tempfile.mktemp(suffix=os.path.splitext(path)[1])
            shutil.copy2(path, tmp)
            wb = openpyxl.load_workbook(tmp, read_only=True, data_only=True)
            if self._excel_sheet not in wb.sheetnames:
                QMessageBox.warning(
                    self, "Brak arkusza",
                    f"Nie znaleziono arkusza '{self._excel_sheet}'.\n"
                    f"Dostepne: {', '.join(wb.sheetnames)}"
                )
                wb.close()
                os.remove(tmp)
                return
            ws = wb[self._excel_sheet]
            rows = self._excel_parser(ws)
            wb.close()
            os.remove(tmp)
        except Exception as e:
            QMessageBox.critical(self, "Błąd odczytu", str(e))
            return

        count = 0
        for vals in rows:
            if any(v for v in vals if v):
                self._saver(vals)
                count += 1
        self._commit()
        self.refresh()
        self.data_changed.emit()
        QMessageBox.information(self, "Import zakonczony",
                                f"Zaimportowano {count} rekordow z arkusza '{self._excel_sheet}'.")
