"""
database/models.py
------------------
Klasy modeli danych (dataclasses) reprezentujące rekordy w bazie SQLite.
Są to proste kontenery danych – bez logiki biznesowej.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class Technician:
    """Monter / technik."""
    id: Optional[int] = None
    full_name: str = ""
    is_active: bool = True


@dataclass
class DinChannel:
    """Pojedynczy kanał DIN (1 z 3 dostępnych w formularzu)."""
    function: str = ""
    din_type: str = ""
    level_low: bool = False
    level_high: bool = False
    serial_number: str = ""

    @property
    def needs_sn(self) -> bool:
        lower = self.function.lower()
        return "zabezpieczenie" in lower or "wlew" in lower


@dataclass
class ServiceRecord:
    """Główny rekord serwisowy."""
    id: Optional[int] = None

    # --- Identyfikacja ---
    record_type: str = ""
    service_date: str = ""
    service_hour: int = 0
    service_minute: int = 0

    # --- Firma i pojazd ---
    company_name: str = ""
    fleet_name: str = ""
    license_plate: str = ""
    side_number: str = ""
    vehicle_brand: str = ""
    vehicle_type: str = ""

    # --- Urządzenie ---
    device_id: str = ""
    sim_number: str = ""
    device_model: str = ""

    # --- Tacho ---
    firmware_tacho: str = ""
    recorder_location: str = ""
    mileage: Optional[int] = None

    # --- Sondy paliwa ---
    probe1_id: str = ""
    probe1_capacity: Optional[float] = None
    probe1_length: Optional[float] = None
    probe2_id: str = ""
    probe2_capacity: Optional[float] = None
    probe2_length: Optional[float] = None
    right_tank_probe: str = ""

    # --- CAN ---
    can_active: bool = False
    can_checkboxes: list[bool] = field(default_factory=lambda: [False] * 8)
    can_vehicle_type: str = ""

    # --- DIN (3 kanały) ---
    din1: DinChannel = field(default_factory=DinChannel)
    din2: DinChannel = field(default_factory=DinChannel)
    din3: DinChannel = field(default_factory=DinChannel)

    # --- Dodatkowe funkcje ---
    has_rfid: bool = False
    has_immo: bool = False
    has_tablet: bool = False
    tablet_sn: str = ""
    has_power: bool = False

    # --- JSON konfiguracyjny ---
    config_json: dict = field(default_factory=dict)

    # --- Technician & Komentarz ---
    technician_name: str = ""
    comment: str = ""
    duty_time_min: Optional[int] = None

    # --- Metadane ---
    created_at: str = ""
    updated_at: str = ""

    # --- Serialization helpers ---

    def can_checkboxes_to_str(self) -> str:
        return "".join("1" if v else "0" for v in self.can_checkboxes)

    @staticmethod
    def can_checkboxes_from_str(s: str) -> list[bool]:
        return [c == "1" for c in s.ljust(8, "0")[:8]]

    def config_json_to_str(self) -> str:
        return json.dumps(self.config_json, ensure_ascii=False)
