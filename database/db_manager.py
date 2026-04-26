"""
database/db_manager.py
-----------------------
Klasa DatabaseManager – singleton zarządzający połączeniem z SQLite.

Odpowiada za:
- inicjalizację schematu (migracje)
- CRUD na wszystkich tabelach
- konwersję między wierszami SQL a dataclassami z models.py
"""

import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DB_PATH
from database.models import ServiceRecord, Technician, DinChannel

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class DatabaseManager:
    """
    Singleton zarządzający połączeniem z bazą SQLite.

    Użycie:
        db = DatabaseManager.instance()
        records = db.get_all_records()
    """

    _instance: Optional["DatabaseManager"] = None

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._initialize()

    # --- Singleton ---

    @classmethod
    def instance(cls) -> "DatabaseManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # --- Inicjalizacja ---

    def _initialize(self) -> None:
        """Otwiera połączenie i uruchamia migracje."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._run_migrations()
        logger.info(f"Baza danych zainicjalizowana: {self._db_path}")

    def _run_migrations(self) -> None:
        """Uruchamia wszystkie pliki SQL z katalogu migrations/."""
        migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
        for sql_file in migration_files:
            logger.debug(f"Uruchamiam migrację: {sql_file.name}")
            sql = sql_file.read_text(encoding="utf-8")
            self._conn.executescript(sql)
        self._conn.commit()
        self._apply_schema_updates()

    def _apply_schema_updates(self) -> None:
        """Bezpieczne addytywne zmiany schematu (ALTER TABLE)."""
        try:
            self._conn.execute("ALTER TABLE companies ADD COLUMN fleet_name TEXT DEFAULT ''")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # Kolumna już istnieje

    def commit(self) -> None:
        if self._conn:
            self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ============================================================
    # SŁOWNIKI – Companies (z flotą)
    # ============================================================

    def get_all_companies_with_fleet(self) -> list[tuple]:
        """Zwraca [(id, name, fleet_name)] posortowane."""
        rows = self._conn.execute(
            "SELECT id, name, COALESCE(fleet_name,'') FROM companies ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def upsert_company_with_fleet(self, name: str, fleet_name: str) -> None:
        self._conn.execute(
            """INSERT INTO companies (name, fleet_name) VALUES (?, ?)
               ON CONFLICT(name) DO UPDATE SET fleet_name = excluded.fleet_name""",
            (name.strip(), fleet_name.strip()),
        )

    def delete_company_by_id(self, company_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ============================================================
    # SŁOWNIKI – Technicians
    # ============================================================

    def get_all_technicians(self, active_only: bool = True) -> list[Technician]:
        sql = "SELECT * FROM technicians"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY full_name COLLATE NOCASE"
        rows = self._conn.execute(sql).fetchall()
        return [Technician(id=r["id"], full_name=r["full_name"],
                           is_active=bool(r["is_active"])) for r in rows]

    def get_technician_names(self) -> list[str]:
        return [t.full_name for t in self.get_all_technicians()]

    def upsert_technician(self, tech: Technician) -> int:
        if tech.id is None:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO technicians (full_name, is_active) VALUES (?, ?)",
                (tech.full_name, int(tech.is_active))
            )
        else:
            cur = self._conn.execute(
                "UPDATE technicians SET full_name=?, is_active=? WHERE id=?",
                (tech.full_name, int(tech.is_active), tech.id)
            )
        self._conn.commit()
        return cur.lastrowid or tech.id

    def delete_technician_by_id(self, tech_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM technicians WHERE id = ?", (tech_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ============================================================
    # SŁOWNIKI – Extra devices
    # ============================================================

    def get_all_extra_devices(self) -> list[tuple]:
        """Zwraca [(id, fleet_name, device_name)] posortowane."""
        rows = self._conn.execute(
            "SELECT id, fleet_name, device_name FROM extra_devices "
            "ORDER BY fleet_name COLLATE NOCASE, device_name COLLATE NOCASE"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def get_extra_devices_for_fleet(self, fleet_name: str) -> list[str]:
        """Zwraca listę nazw urządzeń dodatkowych dla danej floty."""
        rows = self._conn.execute(
            "SELECT device_name FROM extra_devices WHERE fleet_name = ? COLLATE NOCASE "
            "ORDER BY device_name COLLATE NOCASE",
            (fleet_name.strip(),),
        ).fetchall()
        return [r[0] for r in rows]

    def upsert_extra_device(self, fleet_name: str, device_name: str) -> None:
        self._conn.execute(
            """INSERT INTO extra_devices (fleet_name, device_name) VALUES (?, ?)
               ON CONFLICT(fleet_name, device_name) DO NOTHING""",
            (fleet_name.strip(), device_name.strip()),
        )

    def delete_extra_device_by_id(self, device_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM extra_devices WHERE id = ?", (device_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ============================================================
    # SŁOWNIKI – Vehicle models
    # ============================================================

    def get_all_vehicle_models(self) -> list[tuple]:
        """Zwraca [(id, brand_model, vehicle_type)] posortowane."""
        rows = self._conn.execute(
            "SELECT id, brand_model, vehicle_type FROM vehicle_models ORDER BY brand_model COLLATE NOCASE"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def upsert_vehicle_model(self, brand_model: str, vehicle_type: str) -> None:
        self._conn.execute(
            """INSERT INTO vehicle_models (brand_model, vehicle_type) VALUES (?, ?)
               ON CONFLICT(brand_model) DO UPDATE SET vehicle_type = excluded.vehicle_type""",
            (brand_model.strip(), vehicle_type.strip()),
        )

    def delete_vehicle_model_by_id(self, vm_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM vehicle_models WHERE id = ?", (vm_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ============================================================
    # SŁOWNIKI – Recorder locations
    # ============================================================

    def get_all_recorder_locations(self) -> list[tuple]:
        """Zwraca [(id, location)] posortowane."""
        rows = self._conn.execute(
            "SELECT id, location FROM recorder_locations ORDER BY location COLLATE NOCASE"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def upsert_recorder_location(self, location: str) -> None:
        self._conn.execute(
            """INSERT INTO recorder_locations (location) VALUES (?)
               ON CONFLICT(location) DO NOTHING""",
            (location.strip(),),
        )

    def delete_recorder_location_by_id(self, loc_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM recorder_locations WHERE id = ?", (loc_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ============================================================
    # SŁOWNIKI – Device models
    # ============================================================

    def get_all_device_models(self) -> list[tuple]:
        """Zwraca [(id, name)] posortowane alfabetycznie."""
        rows = self._conn.execute(
            "SELECT id, name FROM device_models ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def get_device_model_names(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM device_models ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [r[0] for r in rows]

    def upsert_device_model(self, name: str) -> None:
        self._conn.execute(
            "INSERT INTO device_models (name) VALUES (?) ON CONFLICT(name) DO NOTHING",
            (name.strip(),),
        )

    def delete_device_model_by_id(self, model_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM device_models WHERE id = ?", (model_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # ============================================================
    # SIM CARDS
    # ============================================================

    def get_sim_by_ccid(self, ccid: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT sim FROM sim_cards WHERE ccid = ?", (ccid.strip(),)
        ).fetchone()
        return row["sim"] if row else None

    def bulk_upsert_sim_cards(self, cards: list[tuple[str, str]]) -> int:
        """Wstawia lub zastępuje karty SIM. cards: lista (sim, ccid). Zwraca liczbę wstawionych."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        for sim, ccid in cards:
            if sim and ccid:
                self._conn.execute(
                    "INSERT OR REPLACE INTO sim_cards (sim, ccid, synced_at) VALUES (?, ?, ?)",
                    (sim.strip(), ccid.strip(), now),
                )
                count += 1
        self._conn.commit()
        return count

    def get_sim_cards_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM sim_cards").fetchone()[0]

    def get_existing_import_keys(self) -> dict:
        """Returns dict of (device_id, service_date, service_hour, service_minute) → id for duplicate detection."""
        rows = self._conn.execute(
            "SELECT id, device_id, service_date, service_hour, service_minute FROM service_records"
        ).fetchall()
        return {
            (r["device_id"] or "", r["service_date"] or "", r["service_hour"] or 0, r["service_minute"] or 0): r["id"]
            for r in rows
        }

    def insert_record_no_commit(self, rec: "ServiceRecord") -> int:
        """Like insert_record but without committing – caller must call commit()."""
        sql = """
        INSERT INTO service_records (
            record_type, service_date, service_hour, service_minute,
            company_name, fleet_name, license_plate, side_number,
            vehicle_brand, vehicle_type,
            device_id, sim_number, device_model,
            firmware_tacho, recorder_location, mileage,
            probe1_id, probe1_capacity, probe1_length,
            probe2_id, probe2_capacity, probe2_length,
            right_tank_probe,
            can_active, can_checkboxes, can_vehicle_type,
            din1_function, din1_type, din1_low, din1_high, din1_sn,
            din2_function, din2_type, din2_low, din2_high, din2_sn,
            din3_function, din3_type, din3_low, din3_high, din3_sn,
            has_rfid, has_immo, has_tablet, tablet_sn, has_power,
            config_json,
            technician_name, comment, duty_time_min
        ) VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?
        )
        """
        cur = self._conn.execute(sql, self._record_to_values(rec))
        return cur.lastrowid

    def update_record_no_commit(self, rec: "ServiceRecord") -> bool:
        """Like update_record but without committing – caller must call commit()."""
        if rec.id is None:
            return False
        sql = """
        UPDATE service_records SET
            record_type=?, service_date=?, service_hour=?, service_minute=?,
            company_name=?, fleet_name=?, license_plate=?, side_number=?,
            vehicle_brand=?, vehicle_type=?,
            device_id=?, sim_number=?, device_model=?,
            firmware_tacho=?, recorder_location=?, mileage=?,
            probe1_id=?, probe1_capacity=?, probe1_length=?,
            probe2_id=?, probe2_capacity=?, probe2_length=?,
            right_tank_probe=?,
            can_active=?, can_checkboxes=?, can_vehicle_type=?,
            din1_function=?, din1_type=?, din1_low=?, din1_high=?, din1_sn=?,
            din2_function=?, din2_type=?, din2_low=?, din2_high=?, din2_sn=?,
            din3_function=?, din3_type=?, din3_low=?, din3_high=?, din3_sn=?,
            has_rfid=?, has_immo=?, has_tablet=?, tablet_sn=?, has_power=?,
            config_json=?,
            technician_name=?, comment=?, duty_time_min=?
        WHERE id=?
        """
        cur = self._conn.execute(sql, self._record_to_values(rec) + [rec.id])
        return cur.rowcount > 0

    # ============================================================
    # APP SETTINGS
    # ============================================================

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    # ============================================================
    # GŁÓWNE REKORDY – ServiceRecord
    # ============================================================

    def get_all_records(self, filters: Optional[dict] = None) -> list[ServiceRecord]:
        """
        Pobiera wszystkie rekordy, opcjonalnie z filtrowaniem.

        filters: dict z kluczami: company_name, technician_name, date_from,
                 date_to, license_plate, record_type
        """
        sql = "SELECT * FROM service_records"
        params = []
        conditions = []

        if filters:
            if filters.get("company_name"):
                conditions.append("company_name LIKE ?")
                params.append(f"%{filters['company_name']}%")
            if filters.get("technician_name"):
                conditions.append("technician_name LIKE ?")
                params.append(f"%{filters['technician_name']}%")
            if filters.get("license_plate"):
                conditions.append("license_plate LIKE ?")
                params.append(f"%{filters['license_plate']}%")
            if filters.get("record_type"):
                conditions.append("record_type = ?")
                params.append(filters["record_type"])
            if filters.get("date_from"):
                conditions.append("service_date >= ?")
                params.append(filters["date_from"])
            if filters.get("date_to"):
                conditions.append("service_date <= ?")
                params.append(filters["date_to"])

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        sql += " ORDER BY service_date DESC, id DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_record_by_id(self, record_id: int) -> Optional[ServiceRecord]:
        row = self._conn.execute(
            "SELECT * FROM service_records WHERE id = ?", (record_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def insert_record(self, rec: ServiceRecord) -> int:
        """Dodaje nowy rekord. Zwraca nowe id."""
        sql = """
        INSERT INTO service_records (
            record_type, service_date, service_hour, service_minute,
            company_name, fleet_name, license_plate, side_number,
            vehicle_brand, vehicle_type,
            device_id, sim_number, device_model,
            firmware_tacho, recorder_location, mileage,
            probe1_id, probe1_capacity, probe1_length,
            probe2_id, probe2_capacity, probe2_length,
            right_tank_probe,
            can_active, can_checkboxes, can_vehicle_type,
            din1_function, din1_type, din1_low, din1_high, din1_sn,
            din2_function, din2_type, din2_low, din2_high, din2_sn,
            din3_function, din3_type, din3_low, din3_high, din3_sn,
            has_rfid, has_immo, has_tablet, tablet_sn, has_power,
            config_json,
            technician_name, comment, duty_time_min
        ) VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?
        )
        """
        values = self._record_to_values(rec)
        cur = self._conn.execute(sql, values)
        self._conn.commit()
        return cur.lastrowid

    def update_record(self, rec: ServiceRecord) -> bool:
        """Aktualizuje istniejący rekord. Zwraca True jeśli sukces."""
        if rec.id is None:
            return False
        sql = """
        UPDATE service_records SET
            record_type=?, service_date=?, service_hour=?, service_minute=?,
            company_name=?, fleet_name=?, license_plate=?, side_number=?,
            vehicle_brand=?, vehicle_type=?,
            device_id=?, sim_number=?, device_model=?,
            firmware_tacho=?, recorder_location=?, mileage=?,
            probe1_id=?, probe1_capacity=?, probe1_length=?,
            probe2_id=?, probe2_capacity=?, probe2_length=?,
            right_tank_probe=?,
            can_active=?, can_checkboxes=?, can_vehicle_type=?,
            din1_function=?, din1_type=?, din1_low=?, din1_high=?, din1_sn=?,
            din2_function=?, din2_type=?, din2_low=?, din2_high=?, din2_sn=?,
            din3_function=?, din3_type=?, din3_low=?, din3_high=?, din3_sn=?,
            has_rfid=?, has_immo=?, has_tablet=?, tablet_sn=?, has_power=?,
            config_json=?,
            technician_name=?, comment=?, duty_time_min=?
        WHERE id=?
        """
        values = self._record_to_values(rec) + [rec.id]
        cur = self._conn.execute(sql, values)
        self._conn.commit()
        return cur.rowcount > 0

    def delete_record(self, record_id: int) -> bool:
        cur = self._conn.execute(
            "DELETE FROM service_records WHERE id = ?", (record_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ============================================================
    # KONWERSJE – Row <-> DataClass
    # ============================================================

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ServiceRecord:
        """Konwertuje wiersz SQLite na obiekt ServiceRecord."""
        return ServiceRecord(
            id=row["id"],
            record_type=row["record_type"] or "Montaż",
            service_date=row["service_date"] or "",
            service_hour=row["service_hour"] or 0,
            service_minute=row["service_minute"] or 0,
            company_name=row["company_name"] or "",
            fleet_name=row["fleet_name"] or "",
            license_plate=row["license_plate"] or "",
            side_number=row["side_number"] or "",
            vehicle_brand=row["vehicle_brand"] or "",
            vehicle_type=row["vehicle_type"] or "",
            device_id=row["device_id"] or "",
            sim_number=row["sim_number"] or "",
            device_model=row["device_model"] or "",
            firmware_tacho=row["firmware_tacho"] or "",
            recorder_location=row["recorder_location"] or "",
            mileage=row["mileage"],
            probe1_id=row["probe1_id"] or "",
            probe1_capacity=row["probe1_capacity"],
            probe1_length=row["probe1_length"],
            probe2_id=row["probe2_id"] or "",
            probe2_capacity=row["probe2_capacity"],
            probe2_length=row["probe2_length"],
            right_tank_probe=row["right_tank_probe"] or "",
            can_active=bool(row["can_active"]),
            can_checkboxes=ServiceRecord.can_checkboxes_from_str(
                row["can_checkboxes"] or "00000000"
            ),
            can_vehicle_type=row["can_vehicle_type"] or "",
            din1=DinChannel(
                function=row["din1_function"] or "",
                din_type=row["din1_type"] or "",
                level_low=bool(row["din1_low"]),
                level_high=bool(row["din1_high"]),
                serial_number=row["din1_sn"] or "",
            ),
            din2=DinChannel(
                function=row["din2_function"] or "",
                din_type=row["din2_type"] or "",
                level_low=bool(row["din2_low"]),
                level_high=bool(row["din2_high"]),
                serial_number=row["din2_sn"] or "",
            ),
            din3=DinChannel(
                function=row["din3_function"] or "",
                din_type=row["din3_type"] or "",
                level_low=bool(row["din3_low"]),
                level_high=bool(row["din3_high"]),
                serial_number=row["din3_sn"] or "",
            ),
            has_rfid=bool(row["has_rfid"]),
            has_immo=bool(row["has_immo"]),
            has_tablet=bool(row["has_tablet"]),
            tablet_sn=row["tablet_sn"] or "",
            has_power=bool(row["has_power"]),
            config_json=json.loads(row["config_json"] or "{}"),
            technician_name=row["technician_name"] or "",
            comment=row["comment"] or "",
            duty_time_min=row["duty_time_min"],
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    @staticmethod
    def _record_to_values(rec: ServiceRecord) -> list:
        """Zamienia ServiceRecord na płaską listę wartości do INSERT/UPDATE."""
        return [
            rec.record_type, rec.service_date, rec.service_hour, rec.service_minute,
            rec.company_name, rec.fleet_name, rec.license_plate, rec.side_number,
            rec.vehicle_brand, rec.vehicle_type,
            rec.device_id, rec.sim_number, rec.device_model,
            rec.firmware_tacho, rec.recorder_location, rec.mileage,
            rec.probe1_id, rec.probe1_capacity, rec.probe1_length,
            rec.probe2_id, rec.probe2_capacity, rec.probe2_length,
            rec.right_tank_probe,
            int(rec.can_active), rec.can_checkboxes_to_str(), rec.can_vehicle_type,
            rec.din1.function, rec.din1.din_type, int(rec.din1.level_low),
            int(rec.din1.level_high), rec.din1.serial_number,
            rec.din2.function, rec.din2.din_type, int(rec.din2.level_low),
            int(rec.din2.level_high), rec.din2.serial_number,
            rec.din3.function, rec.din3.din_type, int(rec.din3.level_low),
            int(rec.din3.level_high), rec.din3.serial_number,
            int(rec.has_rfid), int(rec.has_immo), int(rec.has_tablet),
            rec.tablet_sn, int(rec.has_power),
            rec.config_json_to_str(),
            rec.technician_name, rec.comment, rec.duty_time_min,
        ]
