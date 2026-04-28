"""
ui/service_form.py – Formularz montażu/serwisu (dark mode).
Zabezpieczony przed przypadkowym zamknięciem i naciśnięciem Enter.
"""
import copy
import logging
from datetime import date, datetime
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QWidget, QPushButton, QMessageBox,
    QApplication
)
from PySide6.QtCore import Qt, Slot, QTimer, QSettings
from PySide6.QtGui import QKeySequence, QShortcut

from config import FORM_WIDTH, FORM_HEIGHT
from database.db_manager import DatabaseManager
from database.models import ServiceRecord
from ui.widgets.montaz_tab import MontazTab

logger = logging.getLogger(__name__)


class ServiceForm(QDialog):

    def __init__(self, parent=None, record: Optional[ServiceRecord] = None):
        super().__init__(parent)
        self._db = DatabaseManager.instance()
        self._record = record
        self._edit_mode = record is not None
        if not self._edit_mode:
            self._record = ServiceRecord(service_date=date.today().isoformat())
            self._record.record_type = " "
        self._setup_ui()
        self._connect_signals()
        if self._edit_mode:
            self._tab_montaz.load_from_record(self._record)
        self._original_record = copy.deepcopy(self._record)
        self._tab_montaz.collect_to_record(self._original_record)

    def _setup_ui(self):
        if self._edit_mode:
            identifier = self._record.license_plate if self._record.license_plate else f"ID {self._record.id}"
            title = f"Edycja – {identifier}"
        else:
            title = "Nowy montaż"
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint | Qt.WindowMinMaxButtonsHint)
        self.setMinimumSize(FORM_WIDTH, FORM_HEIGHT)
        settings = QSettings("TwojaFirma", "SystemOdbiory")
        w = settings.value("form/width", FORM_WIDTH, type=int)
        h = settings.value("form/height", FORM_HEIGHT, type=int)
        self.resize(w, h)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Zakładki
        self._tabs = QTabWidget()
        root.addWidget(self._tabs, 1)

        self._tab_montaz = MontazTab(record=self._record, edit_mode=self._edit_mode)
        self._tabs.addTab(self._tab_montaz, f"  Montaże   |   {title}  ")

        # Stopka
        footer = QWidget()
        footer.setFixedHeight(54)
        
        is_light = self._db.get_setting("theme_mode", "dark") == "light"
        bg_footer = "#f1f5f9" if is_light else "#15181e"
        border_footer = "#cbd5e1" if is_light else "#2e3340"
        btn_bg = "#ffffff" if is_light else "#2a2f3a"
        btn_fg = "#0f172a" if is_light else "#cbd5e1"
        btn_border = "#cbd5e1" if is_light else "#3a4150"
        btn_hover = "#f8fafc" if is_light else "#333847"
        btn_hover_fg = "#2563eb" if is_light else "#e2e8f0"
        btn_pressed = "#e2e8f0" if is_light else "#3a4150"

        footer.setStyleSheet(
            f"background-color: {bg_footer}; border-top: 1px solid {border_footer};"
        )
        _BTN_H = 30
        _BTN_STYLE_NEUTRAL = (
            f"QPushButton{{background:{btn_bg};color:{btn_fg};border:1px solid {btn_border};"
            f"border-radius:4px;font-size:9pt;font-weight:500;}}"
            f"QPushButton:hover{{background:{btn_hover};color:{btn_hover_fg};border-color:#64748b;}}"
            f"QPushButton:pressed{{background:{btn_pressed};}}"
        )

        f_lay = QHBoxLayout(footer)
        f_lay.setContentsMargins(16, 0, 16, 0)
        f_lay.setSpacing(8)
        f_lay.setAlignment(Qt.AlignVCenter)

        if self._edit_mode:
            self._btn_duplicate = QPushButton("⧉  Duplikuj")
            self._btn_duplicate.setFixedHeight(_BTN_H)
            self._btn_duplicate.setMinimumWidth(100)
            self._btn_duplicate.setStyleSheet(_BTN_STYLE_NEUTRAL)
            self._btn_duplicate.setToolTip("Duplikuj wpis (bez ID, SIM, CCID, sond i przebiegu)")
            f_lay.addWidget(self._btn_duplicate)

        f_lay.addStretch()

        self._btn_json = QPushButton("📋  Kopiuj JSON")
        self._btn_json.setFixedHeight(_BTN_H)
        self._btn_json.setMinimumWidth(120)
        self._btn_json.setStyleSheet(_BTN_STYLE_NEUTRAL)
        f_lay.addWidget(self._btn_json)

        self._btn_cancel = QPushButton("Anuluj")
        self._btn_cancel.setFixedHeight(_BTN_H)
        self._btn_cancel.setMinimumWidth(80)
        self._btn_cancel.setStyleSheet(_BTN_STYLE_NEUTRAL)
        f_lay.addWidget(self._btn_cancel)

        self._btn_save = QPushButton("  Zapisz")
        self._btn_save.setObjectName("btn_primary")
        self._btn_save.setFixedHeight(_BTN_H)
        self._btn_save.setMinimumWidth(100)
        f_lay.addWidget(self._btn_save)

        root.addWidget(footer)

        # Obsługa klawiszy (Skrót na Zapisz)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._on_save)

    def _connect_signals(self):
        self._btn_save.clicked.connect(self._on_save)
        self._btn_cancel.clicked.connect(self.reject)
        self._btn_json.clicked.connect(self._on_copy_json)
        if self._edit_mode:
            self._btn_duplicate.clicked.connect(self._on_duplicate)
        self.finished.connect(self._save_form_size)

    def _save_form_size(self):
        s = QSettings("TwojaFirma", "SystemOdbiory")
        s.setValue("form/width", self.width())
        s.setValue("form/height", self.height())

    def keyPressEvent(self, event):
        """Nadpisanie eventu aby ignorować puste wciśnięcia Enter (zapobiega niechcianym akcjom)."""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            return # Całkowicie "zjadamy" Enter
        super().keyPressEvent(event)

    def _is_dirty(self) -> bool:
        try:
            temp = copy.deepcopy(self._record)
            self._tab_montaz.collect_to_record(temp)
            temp.id = self._original_record.id
            temp.created_at = self._original_record.created_at
            temp.updated_at = self._original_record.updated_at
            return temp != self._original_record
        except Exception:
            return True

    def reject(self):
        """Wywoływane przy kliknięciu przycisku X w prawym rogu, ESC lub 'Anuluj'."""
        if not self._is_dirty():
            super().reject()
            return
        reply = QMessageBox.question(
            self,
            "Niezapisane zmiany",
            "Czy chcesz zapisać zmiany przed zamknięciem?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            self._on_save()
        elif reply == QMessageBox.No:
            super().reject()
        # Jeśli Cancel -> nie rób nic (zostań w formularzu)

    @Slot()
    def _on_save(self):
        ok, msg = self._tab_montaz.collect_to_record(self._record)
        if not ok:
            QMessageBox.warning(self, "Błąd walidacji", msg)
            return
        try:
            if self._edit_mode and self._record.id is not None:
                self._db.update_record(self._record)
            else:
                new_id = self._db.insert_record(self._record)
                self._record.id = new_id
            self.accept()
        except Exception as exc:
            logger.error(f"Błąd zapisu: {exc}", exc_info=True)
            QMessageBox.critical(self, "Błąd zapisu", f"Nie udało się zapisać:\n{exc}")

    @Slot()
    def _on_copy_json(self):
        json_str = self._tab_montaz.get_json_string()
        QApplication.clipboard().setText(json_str)

        orig_text = self._btn_json.text()
        orig_style = self._btn_json.styleSheet()
        self._btn_json.setText("✓  Skopiowano!")
        
        is_light = self._db.get_setting("theme_mode", "dark") == "light"
        bg = "#dcfce7" if is_light else "#1a3a2a"
        fg = "#166534" if is_light else "#4ade80"
        border = "#22c55e" if is_light else "#166534"
        
        self._btn_json.setStyleSheet(
            f"QPushButton{{background:{bg};color:{fg};border:1px solid {border};"
            f"border-radius:4px;font-size:9pt;font-weight:bold;}}"
        )
        QTimer.singleShot(2000, lambda: (
            self._btn_json.setText(orig_text),
            self._btn_json.setStyleSheet(orig_style)
        ))

    @Slot()
    def _on_duplicate(self):
        if not self._edit_mode:
            return

        # Zbierz aktualny stan formularza do źródłowego rekordu
        src = copy.deepcopy(self._record)
        self._tab_montaz.collect_to_record(src)

        now = datetime.now()
        _m = int(round(now.minute / 5.0) * 5)
        _h = now.hour + (1 if _m == 60 else 0)
        h_mod = _h % 24
        m_mod = _m % 60
        new_rec = ServiceRecord(
            # Identyfikacja — data/godzina aktualne (zaokrąglone do 5 min)
            record_type=src.record_type,
            service_date=now.strftime("%Y-%m-%d"),
            service_hour=h_mod,
            service_minute=m_mod,
            # Firma i pojazd — kopiowane
            company_name=src.company_name,
            fleet_name=src.fleet_name,
            license_plate="",
            side_number=src.side_number,
            vehicle_brand=src.vehicle_brand,
            vehicle_type=src.vehicle_type,
            # Urządzenie — ID, SIM czyszczone
            device_id="",
            sim_number="",
            device_model=src.device_model,
            # Tacho
            firmware_tacho=src.firmware_tacho,
            recorder_location=src.recorder_location,
            mileage=None,
            # Sondy — czyszczone
            probe1_id="", probe1_capacity=None, probe1_length=None,
            probe2_id="", probe2_capacity=None, probe2_length=None,
            right_tank_probe=src.right_tank_probe,
            # CAN
            can_active=src.can_active,
            can_checkboxes=list(src.can_checkboxes),
            can_vehicle_type=src.can_vehicle_type,
            # Dodatkowe
            has_rfid=src.has_rfid,
            has_immo=src.has_immo,
            has_tablet=src.has_tablet,
            tablet_sn=src.tablet_sn,
            has_power=src.has_power,
            # Technician & komentarz
            technician_name=src.technician_name,
            comment=src.comment,
            duty_time_min=src.duty_time_min,
        )

        # Kopiuj config_json bez CCID i bez odebrane
        cfg = copy.deepcopy(src.config_json)
        cfg.get("additionalConfig", {}).pop("ccid", None)
        cfg["odebrane"] = False
        is_weekend = now.weekday() >= 5
        is_duty_time = h_mod >= 15 or h_mod < 6 or (h_mod == 6 and m_mod <= 55)
        cfg["dyzurZaznaczony"] = is_weekend or is_duty_time
        new_rec.config_json = cfg

        try:
            self._db.insert_record(new_rec)
            QMessageBox.information(
                self, "Duplikat dodany",
                f"Zduplikowano rekord.\nNowy wpis pojawi się w tabeli po odświeżeniu.",
            )
        except Exception as exc:
            logger.error(f"Błąd duplikowania: {exc}", exc_info=True)
            QMessageBox.critical(self, "Błąd", f"Nie udało się zduplikować:\n{exc}")