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
from PySide6.QtCore import Qt, Slot, QTimer, QSettings, QUrl, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QDesktopServices

from config import FORM_WIDTH, FORM_HEIGHT
from database.db_manager import DatabaseManager
from database.models import ServiceRecord
from ui.widgets.montaz_tab import MontazTab

logger = logging.getLogger(__name__)


def _clear_din_sns(cfg: dict) -> None:
    """Czyści numery seryjne z dinConfig dla wpisów z zabezpieczeniem/wlewem."""
    din_cfg = cfg.get("dinConfig", {})
    for key in ("din2", "din3", "din4", "din5"):
        entry = din_cfg.get(key, {})
        nazwa = (entry.get("nazwa") or "").lower()
        if "zabezpieczenie" in nazwa or "wlew" in nazwa:
            entry.pop("sn", None)


class ServiceForm(QDialog):

    record_duplicated = Signal()

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
            self._btn_duplicate.setToolTip("Duplikuj wpis (czyści: ID, SIM, CCID, nr boczny, model urządzenia, nr tabletu, sondy, numery seryjne zabezpieczeń)")
            f_lay.addWidget(self._btn_duplicate)

        self._btn_fleet = QPushButton("🌐  Flota")
        self._btn_fleet.setFixedHeight(_BTN_H)
        self._btn_fleet.setMinimumWidth(110)
        self._btn_fleet.setStyleSheet(_BTN_STYLE_NEUTRAL)
        f_lay.addWidget(self._btn_fleet)

        f_lay.addStretch()

        if not self._edit_mode:
            self._btn_paste_json = QPushButton("📥  Wklej z JSON")
            self._btn_paste_json.setFixedHeight(_BTN_H)
            self._btn_paste_json.setMinimumWidth(120)
            self._btn_paste_json.setStyleSheet(_BTN_STYLE_NEUTRAL)
            self._btn_paste_json.setToolTip("Wczytaj dane formularza z JSON (tylko w pustym formularzu)")
            f_lay.addWidget(self._btn_paste_json)

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
        self._btn_fleet.clicked.connect(self._on_open_fleet)
        self._tab_montaz._fleet_name_edit.textChanged.connect(self._on_fleet_changed)
        if self._edit_mode:
            self._btn_duplicate.clicked.connect(self._on_duplicate)
        else:
            self._btn_paste_json.clicked.connect(self._on_paste_json)
        self.finished.connect(self._save_form_size)

    def _save_form_size(self):
        s = QSettings("TwojaFirma", "SystemOdbiory")
        s.setValue("form/width", self.width())
        s.setValue("form/height", self.height())

    def _on_fleet_changed(self, fleet: str):
        f = fleet.strip()
        if f:
            self._btn_fleet.setText(f"🌐  Flota {f}")
            self._btn_fleet.setToolTip(f"Otwórz instalacje w FleetVision {f}")
            self._btn_fleet.setEnabled(True)
        else:
            self._btn_fleet.setText("🌐  Flota")
            self._btn_fleet.setToolTip("Brak przypisanej floty")
            self._btn_fleet.setEnabled(False)

    def _on_open_fleet(self):
        self._tab_montaz.collect_to_record(self._record)
        fleet = (self._record.fleet_name or "").strip()
        if not fleet:
            return
        url = self._db.get_url_for_fleet(fleet)
        if url:
            QDesktopServices.openUrl(QUrl(url))
        else:
            QMessageBox.information(
                self, "Brak linku",
                f"Nie znaleziono linku dla floty \"{fleet}\".\n"
                "Dodaj go w Ustawienia → Slownniki → Linki flot."
            )

    def _on_paste_json(self):
        if self._is_dirty():
            QMessageBox.warning(
                self, "Formularz nie jest pusty",
                "Wyczysc formularz przed wczytaniem JSON."
            )
            return

        from PySide6.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Wklej JSON")
        dlg.setMinimumSize(480, 300)
        lay = QVBoxLayout(dlg)
        txt = QPlainTextEdit()
        txt.setPlaceholderText("Wklej tutaj JSON skopiowany z formularza...")
        lay.addWidget(txt)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() != QDialog.Accepted:
            return

        raw = txt.toPlainText().strip()
        if not raw:
            return
        try:
            data = __import__("json").loads(raw)
        except Exception:
            QMessageBox.warning(self, "Blad", "Nieprawidlowy JSON.")
            return

        rec = self._json_to_record(data)
        self._tab_montaz.load_from_record(rec)
        self._record = rec
        self._original_record = __import__("copy").deepcopy(rec)
        self._tab_montaz.collect_to_record(self._original_record)

    @staticmethod
    def _json_to_record(j: dict) -> "ServiceRecord":
        from database.models import ServiceRecord
        rec = ServiceRecord()

        raw_date = j.get("data", "")
        if raw_date and "." in raw_date:
            parts = raw_date.split(".")
            if len(parts) == 3:
                raw_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
        rec.service_date = raw_date

        rec.record_type    = j.get("typ", "")
        rec.company_name   = j.get("firma", "")
        rec.fleet_name     = j.get("flota", "")
        rec.license_plate  = j.get("nrRejestracyjny", "")
        rec.side_number    = j.get("nrBoczny", "")
        rec.device_id      = j.get("id", "")
        rec.sim_number     = j.get("sim", "")
        rec.vehicle_type   = j.get("typPojazdu", "")
        rec.device_model   = j.get("modelUrzadzenia", "")
        rec.technician_name = j.get("monter", "")
        rec.recorder_location = j.get("gdzieRejestrator", "")
        rec.comment        = j.get("komentarzDoProtokolu", "")
        rec.right_tank_probe = j.get("prawyZbiornik", False)
        rec.has_tablet     = bool(j.get("tablet", False))
        rec.tablet_sn      = j.get("tabletNr", "")
        rec.has_power      = bool(j.get("zasilanie", False))
        rec.has_rfid       = bool(j.get("rfid", False))
        rec.has_immo       = bool(j.get("immo", False))
        rec.probe1_id      = j.get("an0Numer", "")
        rec.probe2_id      = j.get("an1Numer", "")

        try:
            rec.service_hour = int(j.get("godzina", 0))
        except (ValueError, TypeError):
            rec.service_hour = 0
        try:
            rec.service_minute = int(j.get("minuta", 0))
        except (ValueError, TypeError):
            rec.service_minute = 0
        try:
            mileage = j.get("przebieg", "")
            rec.mileage = int(mileage) if mileage not in ("", None) else None
        except (ValueError, TypeError):
            rec.mileage = None

        marka = j.get("marka", "")
        model = j.get("model", "")
        rec.vehicle_brand = f"{marka} {model}".strip()

        model_tacho = j.get("modelTacho", "")
        wersja_tacho = j.get("wersjaTacho", "")
        rec.firmware_tacho = f"{model_tacho} {wersja_tacho}".strip()

        try:
            rec.duty_time_min = int(j.get("czasDyzuru")) if j.get("czasDyzuru") not in ("", None) else None
        except (ValueError, TypeError):
            rec.duty_time_min = None

        sondy = {}
        for key in ("an0Pojemnosc", "an0Skalowanie", "an1Pojemnosc", "an1Skalowanie"):
            if j.get(key) not in (None, ""):
                sondy[key] = j[key]

        add_cfg = {}
        if j.get("ccid"):
            add_cfg["ccid"] = j["ccid"]
        if j.get("przekladkaZ"):
            add_cfg["przekladkaRej"] = j["przekladkaZ"]

        rec.config_json = {
            "canConfig":       j.get("canConfig", {}),
            "dinConfig":       j.get("dinConfig", {}),
            "additionalConfig": add_cfg,
            "odebrane":        j.get("odebrane", False),
            "dyzurZaznaczony": j.get("dyzur", False),
            "komentarzPrywatny": j.get("komentarzPrywatny", ""),
            "sondyRaw":        sondy,
        }
        return rec

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
            record_type=src.record_type,
            service_date=now.strftime("%Y-%m-%d"),
            service_hour=h_mod,
            service_minute=m_mod,
            company_name=src.company_name,
            fleet_name=src.fleet_name,
            license_plate="",
            side_number="",
            vehicle_brand=src.vehicle_brand,
            vehicle_type=src.vehicle_type,
            device_id="",
            sim_number="",
            device_model="",
            firmware_tacho=src.firmware_tacho,
            recorder_location=src.recorder_location,
            mileage=None,
            probe1_id="", probe1_capacity=None, probe1_length=None,
            probe2_id="", probe2_capacity=None, probe2_length=None,
            right_tank_probe=src.right_tank_probe,
            can_active=src.can_active,
            can_checkboxes=list(src.can_checkboxes),
            can_vehicle_type=src.can_vehicle_type,
            has_rfid=src.has_rfid,
            has_immo=src.has_immo,
            has_tablet=src.has_tablet,
            tablet_sn="",
            has_power=src.has_power,
            technician_name=src.technician_name,
            comment=src.comment,
            duty_time_min=src.duty_time_min,
        )

        cfg = copy.deepcopy(src.config_json)
        cfg.get("additionalConfig", {}).pop("ccid", None)
        cfg["odebrane"] = False
        is_weekend = now.weekday() >= 5
        is_duty_time = h_mod >= 15 or h_mod < 6 or (h_mod == 6 and m_mod <= 55)
        cfg["dyzurZaznaczony"] = is_weekend or is_duty_time
        _clear_din_sns(cfg)
        new_rec.config_json = cfg

        try:
            self._db.insert_record(new_rec)
            self.record_duplicated.emit()
        except Exception as exc:
            logger.error(f"Błąd duplikowania: {exc}", exc_info=True)
            QMessageBox.critical(self, "Błąd", f"Nie udało się zduplikować:\n{exc}")