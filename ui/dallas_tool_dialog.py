"""
ui/dallas_tool_dialog.py
--------------------------
Okno "Podgrywanie Dallas" - zakładka Kalkulator (przelicznik 01<->AD)
i zakładka Dopasowanie (import załącznika + eksportu serwera). Nie-modalne,
bez zapisu żadnego stanu do bazy danych.
"""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QTabWidget
from PySide6.QtCore import Qt

from database.db_manager import DatabaseManager
from ui.card_converter_dialog import CardConverterWidget
from ui.dallas_matching_widget import DallasMatchingWidget


class DallasToolDialog(QDialog):
    """Okno narzędzi do podgrywania kart Dallas do rejestratorów."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_light = DatabaseManager.instance().get_setting("theme_mode", "dark") == "light"
        self.setWindowTitle("Podgrywanie Dallas")
        self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint | Qt.WindowMinMaxButtonsHint)
        self.resize(1200, 620)
        # Rząd przycisków filtrów w zakładce "Podgrywka kart" używa FlowLayout - przy
        # zwężaniu okna przyciski przechodzą do kolejnej linii zamiast się ściskać, więc
        # minimum może zostać rozsądnie małe (Qt samo pilnuje, żeby nie zejść poniżej
        # naturalnego minimum pozostałych, nie-zawijanych elementów).
        self.setMinimumSize(640, 460)
        self._apply_dwm_titlebar()
        self._setup_ui()

    def _apply_dwm_titlebar(self) -> None:
        try:
            import ctypes
            hwnd = int(self.winId())
            mode = ctypes.c_int(0 if self._is_light else 1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(mode), ctypes.sizeof(mode))
        except Exception:
            pass

    def _setup_ui(self):
        bg = "#f8fafc" if self._is_light else "#1a1d23"
        self.setStyleSheet(f"QDialog{{background:{bg};}}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        tabs = QTabWidget(self)
        tabs.setDocumentMode(True)
        tabs.addTab(DallasMatchingWidget(), "  Podgrywka kart  ")
        tabs.addTab(CardConverterWidget(), "  Kalkulator 01 / AD  ")
        root.addWidget(tabs, 1)
