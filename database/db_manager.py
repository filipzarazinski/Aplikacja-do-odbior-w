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
import math
import difflib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DB_PATH
from database.models import ServiceRecord, Technician, DinChannel

logger = logging.getLogger(__name__)

import sys as _sys
if getattr(_sys, "frozen", False):
    _MIGRATIONS_DIR = Path(getattr(_sys, "_MEIPASS", Path(_sys.executable).parent)) / "database" / "migrations"
else:
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
        self._conn.execute("PRAGMA synchronous = NORMAL")
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

        try:
            # Komendy w Kompendium dzielą się na wysyłane SMS-em i wysyłane przez logi
            # (inny kanał komunikacji z rejestratorem) - filtr w tej samej zakładce
            # Komendy, zamiast osobnej zakładki.
            self._conn.execute("ALTER TABLE kompendium_commands ADD COLUMN typ TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # Kolumna już istnieje

        try:
            # Link do źródła (np. strony Confluence), z którego artykuł został
            # streszczony - przycisk "Źródło" w oknie Kompendium otwiera ten link.
            self._conn.execute("ALTER TABLE kompendium_articles ADD COLUMN source_url TEXT NOT NULL DEFAULT ''")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # Kolumna już istnieje

        # Migracja: zmiana UNIQUE(name) → UNIQUE(name, fleet_name)
        # żeby jedna firma mogła figurować na wielu flotach jako osobne wiersze.
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='companies'"
        ).fetchone()
        if row and "UNIQUE(name, fleet_name)" not in (row[0] or ""):
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS companies_new (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT    NOT NULL COLLATE NOCASE,
                    device_protocol TEXT    DEFAULT 'FM3',
                    fleet_type      TEXT    DEFAULT 'Zwykłe',
                    is_active       INTEGER DEFAULT 1,
                    fleet_name      TEXT    NOT NULL DEFAULT '' COLLATE NOCASE,
                    UNIQUE(name, fleet_name)
                );
                INSERT OR IGNORE INTO companies_new
                    (id, name, device_protocol, fleet_type, is_active, fleet_name)
                SELECT
                    id,
                    name,
                    COALESCE(device_protocol, 'FM3'),
                    COALESCE(fleet_type, 'Zwykłe'),
                    COALESCE(is_active, 1),
                    COALESCE(fleet_name, '')
                FROM companies;
                DROP TABLE companies;
                ALTER TABLE companies_new RENAME TO companies;
            """)
            self._conn.commit()
            logger.info("Migracja companies: UNIQUE(name) → UNIQUE(name, fleet_name)")

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
               ON CONFLICT(name, fleet_name) DO NOTHING""",
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
    # SŁOWNIKI – Fleet links
    # ============================================================

    def get_all_fleet_links(self) -> list[tuple]:
        """Zwraca [(id, fleet_name, url)] posortowane po fleet_name."""
        rows = self._conn.execute(
            "SELECT id, fleet_name, url FROM fleet_links ORDER BY fleet_name COLLATE NOCASE"
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def get_url_for_fleet(self, fleet_name: str) -> str:
        """Zwraca URL dla podanej floty lub pusty string jeśli nie znaleziono."""
        row = self._conn.execute(
            "SELECT url FROM fleet_links WHERE fleet_name = ? COLLATE NOCASE",
            (fleet_name.strip(),),
        ).fetchone()
        return row[0] if row else ""

    def upsert_fleet_link(self, fleet_name: str, url: str) -> None:
        self._conn.execute(
            """INSERT INTO fleet_links (fleet_name, url) VALUES (?, ?)
               ON CONFLICT(fleet_name) DO UPDATE SET url=excluded.url""",
            (fleet_name.strip(), url.strip()),
        )

    def delete_fleet_link_by_id(self, link_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM fleet_links WHERE id = ?", (link_id,))
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

    def clear_sim_cards(self) -> None:
        self._conn.execute("DELETE FROM sim_cards")
        self._conn.commit()

    def get_sim_cards_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM sim_cards").fetchone()[0]

    def get_all_sim_cards(self) -> list[tuple]:
        """Zwraca [(id, sim, ccid, synced_at)] posortowane po numerze SIM."""
        rows = self._conn.execute(
            "SELECT id, sim, ccid, synced_at FROM sim_cards ORDER BY sim COLLATE NOCASE"
        ).fetchall()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    def delete_sim_card_by_id(self, card_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM sim_cards WHERE id = ?", (card_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def upsert_sim_card(self, sim: str, ccid: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            "INSERT OR REPLACE INTO sim_cards (sim, ccid, synced_at) VALUES (?, ?, ?)",
            (sim.strip(), ccid.strip(), now),
        )

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
    # KOMPENDIUM WIEDZY – artykuły i komendy do rejestratorów
    # ============================================================

    _PL_FOLD = str.maketrans(
        "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ",
        "acelnoszzACELNOSZZ",
    )

    @staticmethod
    def _fold_pl(text: str) -> str:
        """Usuwa polskie znaki diakrytyczne (ą->a, ł->l, ż/ź->z, itd.). FTS5's wbudowana
        opcja remove_diacritics nie ogarnia polskiego 'ł' (to nie jest znak ze zwykłym
        znakiem diakrytycznym w Unicode) - bez tego wpisanie zapytania bez polskich
        znaków (częste przy szybkim pisaniu) nie trafiało w treści zapisane z ogonkami,
        i odwrotnie. Foldujemy więc ręcznie i tekst indeksowany w FTS, i zapytania -
        obie strony spotykają się w tej samej, uproszczonej formie."""
        return (text or "").translate(DatabaseManager._PL_FOLD)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Dzieli tekst na słowa po DOWOLNYM znaku niealfanumerycznym (spacja, "/",
        "-", ",", ";"...), nie tylko po spacji. Bez tego np. "FMB640/FMC650" albo
        "209:0" zlepiały się w jeden bezużyteczny token zamiast dwóch osobnych,
        dopasowywalnych słów."""
        return re.findall(r"[a-z0-9]+", (text or "").lower())

    @staticmethod
    def _fts_escape(query: str) -> str:
        """Zamienia dowolny tekst użytkownika (np. cały komentarz prywatny wklejony
        1:1) na "inteligentne" zapytanie FTS5: każde słowo jako osobny prefiks
        (dopasowanie częściowe), połączone przez OR - żeby artykuł pasujący tylko
        częściowo (np. 2 z 4 słów) nadal się pojawił, zamiast być całkowicie ukryty
        (jak przy AND). Trafność i tak porządkuje bm25() w zapytaniu SQL - najlepiej
        dopasowany wynik i tak wypłynie na górę, więc nie trzeba wymagać pełnej zgody."""
        query = DatabaseManager._fold_pl(query)
        words = DatabaseManager._tokenize(query)
        # Bardzo krótkie słowa (spójniki typu "z", "do", "dla") jako osobne prefiksy
        # dopasowują się do zbyt wielu przypadkowych tokenów i zaśmiecają wynik OR-em -
        # pomijamy je, chyba że to jedyne, co użytkownik wpisał.
        significant = [w for w in words if len(w) >= 3]
        words = significant or words
        if not words:
            return ""
        # "Ubogi" polski stemming: tniemy dłuższe słowa do wspólnego rdzenia (5 znaków),
        # zanim opakujemy w prefiks FTS. Polska odmiana potrafi zmieniać końcówkę już od
        # 5.-6. znaku (np. "kamery"/"kamerze", "montażu"/"montaże") - dopasowanie CAŁEGO
        # wpisanego słowa jako prefiksu było zbyt sztywne i takich par w ogóle nie łączyło,
        # przez co np. artykuł, który używa innej odmiany tego samego słowa w tytule, wcale
        # nie dostawał punktów za to trafienie i przegrywał z krótszym, mniej trafnym
        # artykułem (bm25 faworyzuje krótsze dokumenty przy tej samej gęstości słów).
        STEM_LEN = 5
        stems = [w[:STEM_LEN] if len(w) > STEM_LEN else w for w in words]
        return " OR ".join(f'"{w}"*' for w in stems)

    # --- Artykuły ---

    def add_kompendium_article(self, title: str, category: str, tags: str,
                                body_html: str, body_text: str, source_url: str = "") -> int:
        cur = self._conn.execute(
            """INSERT INTO kompendium_articles (title, category, tags, body_html, body_text, source_url)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title.strip(), category.strip(), tags.strip(), body_html, body_text, source_url.strip()),
        )
        article_id = cur.lastrowid
        self._conn.execute(
            "INSERT INTO kompendium_articles_fts (rowid, title, category, tags, body_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (article_id, self._fold_pl(title.strip()), self._fold_pl(category.strip()),
             self._fold_pl(tags.strip()), self._fold_pl(body_text)),
        )
        self._conn.commit()
        return article_id

    def update_kompendium_article(self, article_id: int, title: str, category: str,
                                   tags: str, body_html: str, body_text: str,
                                   source_url: str = "") -> None:
        self._conn.execute(
            """UPDATE kompendium_articles
               SET title=?, category=?, tags=?, body_html=?, body_text=?, source_url=?,
                   updated_at=datetime('now')
               WHERE id=?""",
            (title.strip(), category.strip(), tags.strip(), body_html, body_text,
             source_url.strip(), article_id),
        )
        self._conn.execute("DELETE FROM kompendium_articles_fts WHERE rowid=?", (article_id,))
        self._conn.execute(
            "INSERT INTO kompendium_articles_fts (rowid, title, category, tags, body_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (article_id, self._fold_pl(title.strip()), self._fold_pl(category.strip()),
             self._fold_pl(tags.strip()), self._fold_pl(body_text)),
        )
        self._conn.commit()

    def delete_kompendium_article(self, article_id: int) -> None:
        self._conn.execute("DELETE FROM kompendium_articles WHERE id=?", (article_id,))
        self._conn.execute("DELETE FROM kompendium_articles_fts WHERE rowid=?", (article_id,))
        self._conn.commit()

    def get_all_kompendium_articles(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM kompendium_articles ORDER BY category COLLATE NOCASE, title COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_kompendium_article(self, article_id: int) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM kompendium_articles WHERE id=?", (article_id,)
        ).fetchone()
        return dict(row) if row else None

    # Wagi kolumn dla bm25() - kolejność MUSI zgadzać się z definicją
    # "CREATE VIRTUAL TABLE kompendium_articles_fts USING fts5(title, category, tags,
    # body_text)". Bez tego bm25 waży wszystkie kolumny jednakowo, więc słowo trafiające
    # w TYTUŁ liczyło się tyle samo co przypadkowe wystąpienie gdzieś w długiej treści -
    # a bm25 dodatkowo faworyzuje krótsze dokumenty, więc krótki, słabiej pasujący
    # artykuł potrafił wygrać z długim, ale trafniejszym tytułowo.
    _ARTICLES_BM25_WEIGHTS = "10.0, 3.0, 6.0, 1.0"  # title, category, tags, body_text

    def search_kompendium_articles(
        self, query: str, context_fields: Optional[dict] = None,
        absent_hardware: Optional[set] = None,
    ) -> list[dict]:
        """Wyszukiwanie ma dwa tryby - RÓŻNE ŹRÓDŁO sygnału, ale ten sam ważony silnik
        oceny trafności (nie osobne, prostsze bm25 dla ręcznego wpisywania):

        1. Pole szukania PUSTE -> sygnałem są pola formularza (`context_fields`, np.
           {"firma": "ROBANO", "marka_tachografu": "Stoneridge"}).
        2. Pole szukania NIEPUSTE -> użytkownik ręcznie coś wpisał, więc `context_
           fields` jest w całości porzucane - sygnałem jest WYŁĄCZNIE wpisany tekst
           (jako jedno "pole" o wysokiej wadze, bo to świadome, wprost wyrażone
           zapytanie - nigdy nie wymaga potwierdzenia drugim trafieniem, w
           przeciwieństwie do np. przypadkowego słowa w nazwie firmy).

        W obu trybach liczy się to samo: dopasowanie do tagu (ważone rzadkością słowa
        w całym korpusie - IDF) > tytuł/kategoria > treść, plus rozmyte (fuzzy)
        dopasowanie jako fallback na literówki/odmiany. Sam bm25 (z wagami kolumn)
        ustala tylko wstępną kolejność - dopiero ważona punktacja robi resztę.

        NIGDY nie usuwamy artykułu z wyniku w żadnym trybie - wyszukiwanie tylko
        PRZESTAWIA kolejność (najlepiej dopasowane wyżej, reszta niżej w normalnym
        porządku), inaczej przy niepełnym dopasowaniu użytkownik widziałby tylko część
        Kompendium, myśląc, że reszta nie istnieje."""
        query = query.strip()
        all_rows = self.get_all_kompendium_articles()

        if query:
            # Tryb 2: ręczne wyszukiwanie - kontekst formularza w całości porzucony,
            # jedynym sygnałem jest wpisany tekst. `absent_hardware` też odpada - jeśli
            # ktoś świadomie wpisuje "rfid", to szuka o RFID niezależnie od tego, czy
            # checkbox RFID akurat jest zaznaczony (np. dopiero planuje montaż).
            context_fields = {"zapytanie": query}
            absent_hardware = None
        else:
            # Tryb 1: sugestia z formularza.
            context_fields = {
                k: v.strip() for k, v in (context_fields or {}).items() if v and v.strip()
            }
        combined_context = " ".join(context_fields.values())
        if not combined_context:
            return all_rows

        fts_query = self._fts_escape(combined_context)
        matched_ids: list[int] = []
        if fts_query:
            fts_rows = self._conn.execute(
                f"""SELECT a.id FROM kompendium_articles a
                   JOIN kompendium_articles_fts f ON f.rowid = a.id
                   WHERE kompendium_articles_fts MATCH ?
                   ORDER BY bm25(kompendium_articles_fts, {self._ARTICLES_BM25_WEIGHTS})""",
                (fts_query,),
            ).fetchall()
            matched_ids = [r[0] for r in fts_rows]
        by_id = {row["id"]: row for row in all_rows}
        matched_set = set(matched_ids)
        ordered = [by_id[i] for i in matched_ids if i in by_id]
        ordered += [row for row in all_rows if row["id"] not in matched_set]
        # Pozycja w rankingu FTS liczy się jako bonus TYLKO dla artykułów, które
        # faktycznie coś dzielą z kontekstem tekstowo - artykuły dołożone na końcu (bo
        # nic nie trafiły) nie dostają żadnego bonusu za samą pozycję na liście,
        # inaczej sam alfabetyczny porządek podszywałby się pod realne dopasowanie.
        match_rank = {aid: idx for idx, aid in enumerate(matched_ids)}

        # Ważona punktacja po polach - kilka warstw naraz:
        # 1) trafienie w tag > tytuł/kategorię > samą treść artykułu (malejąca waga).
        # 2) rzadkość tagu w całym korpusie (IDF) - tag występujący w prawie każdym
        #    artykule (np. "d8", "bus", "konfiguracja") różnicuje słabo, więc waży
        #    mniej niż tag swoisty tylko dla jednego/dwóch artykułów (np. "robano").
        # 3) waga pola kontekstu (FIELD_IMPORTANCE) - marka/model urządzenia to dużo
        #    mocniejszy sygnał tożsamości montażu niż np. typ montażu czy flota.
        # 4) rozmyte dopasowanie (difflib) jako fallback, gdy nic nie trafiło
        #    dosłownie - łapie literówki/drobne odmiany (np. "stonerige" vs
        #    "stoneridge"), liczy się słabiej niż trafienie dosłowne.
        # bm25_rank (pozycja w `ordered`) liczy się jako niewielki bonus bazowy, żeby
        # przy remisie wygrywało trafniejsze tekstowo dopasowanie.
        TAG_WEIGHT = 10
        TITLE_CATEGORY_WEIGHT = 4
        BODY_WEIGHT = 2
        FUZZY_FACTOR = 0.5
        FUZZY_CUTOFF = 0.82
        FIELD_IMPORTANCE = {
            "zapytanie": 1.5,  # ręcznie wpisany tekst - świadome zapytanie, ufamy mu mocno
            "wykryta_marka_rejestratora": 1.5,
            "marka_tachografu": 1.5,
            "model_urzadzenia": 1.2,
            "marka_model": 1.2,
            "d8": 1.2,
            "firma": 1.0,
            "komentarz_prywatny": 1.0,
            "can_kierowca_statusy": 1.0,
            "komentarz_protokolu": 0.8,
            "typ_pojazdu": 0.8,
            "typ_montazu": 0.6,
            "din_funkcje": 0.6,
        }
        DEFAULT_FIELD_IMPORTANCE = 1.0
        # Pola-IDENTYFIKATORY wolnego tekstu (firma, model/marka wpisane ręcznie, opisy
        # DIN) mogą PRZYPADKOWO zawierać słowo, które akurat jest tagiem artykułu (np.
        # firma "BUS Krzysztof Łyziński" trafia w tag "bus" - typ pojazdu, kompletnie
        # inny temat) - to nazwy własne, nikt ich nie pisze z myślą o wyszukiwaniu.
        # Pojedyncze, niepotwierdzone niczym innym trafienie z takiego pola jest więc
        # mocno tłumione - potrzebuje potwierdzenia (drugiego trafienia, z dowolnego
        # pola) zanim będzie liczone w pełni.
        #
        # Komentarze (prywatny/do protokołu) NIE są tu ujęte - to wolny tekst pisany
        # ŚWIADOMIE przez montera jako notatka o tym, co robi (np. "KAMERA", "dołożenie
        # tacho") - to sygnał tej samej jakości co ręczne wpisanie w wyszukiwarkę, więc
        # traktujemy go tak samo ufnie (bez wymogu potwierdzenia). Pola z kontrolowanym
        # słownictwem (combo/radio/wykryta marka) też nie wymagają potwierdzenia - to
        # świadomy wybór, nie przypadkowe słowo.
        NEEDS_CORROBORATION = {"firma", "model_urzadzenia", "marka_model", "din_funkcje"}
        LONE_HIT_DAMPING = 0.35
        # Próg pokrycia, od którego dopasowanie tag/tytuł liczy się jako "prawdziwe"
        # potwierdzenie dla INNEGO trafienia z pola wymagającego korroboracji - inaczej
        # drobny, słabo pokrywający fragment (np. "konf" trafiające 1/3 słowa
        # "konfiguracja") potrafił sam w sobie "odblokować" pełną wagę zupełnie
        # osobnego, przypadkowego trafienia (np. "skaut" z modelu urządzenia) tylko
        # dlatego, że liczył się jako jakiekolwiek drugie trafienie, niezależnie od tego
        # jak mizerne. Body/fuzzy trafienia zawsze liczą się jako pełnoprawne - to już
        # dopasowanie całego słowa, nie fragmentu.
        MIN_CORROBORATION_COVERAGE = 0.5
        # Sprzęt POTWIERDZONY jako nieobecny (checkbox jawnie odznaczony w formularzu,
        # patrz _absent_hardware_tags w service_form.py) - żadne dopasowanie słów nie
        # ma sposobu, by wiedzieć, że dany sprzęt w ogóle nie występuje w tym montażu
        # (np. odmiana przez przypadki "kierowca"/"kierowcy" fuzzy-matchująca tag "id
        # kierowcy" z artykułu o RFID, mimo że RFID nie jest tu w ogóle instalowane) -
        # to jedyny sygnał w całym silniku, który może OBNIŻYĆ już policzony wynik,
        # zamiast tylko go podbijać. Mocna, ale nie zerowa kara - artykuł może wciąż
        # dotyczyć np. tego, co zrobić żeby COŚ NIE kolidowało z (nieobecnym) sprzętem.
        HARDWARE_ABSENT_PENALTY = 0.15
        absent_hardware = {h.lower() for h in (absent_hardware or set())}

        # UWAGA: dopasowanie tagu niżej w score() jest PODCIĄGOWE (np. "skaut" z modelu
        # "Skaut 8 LTE" musi trafić w tag "skaut8" - inaczej spacja w modelu a brak
        # spacji w tagu nigdy by się nie połączyły). Rzadkość (IDF) MUSI więc liczyć to
        # samo - ile dokumentów zawiera dane słowo jako PODCIĄG w tagach, a NIE ile
        # dokumentów ma je jako dokładnie CAŁY, osobny tag. Wcześniej liczyliśmy dokładne
        # tagi, więc krótki fragment słowa (np. "konf" ze skrótu "konf:" w komentarzu),
        # który nigdy nie jest sam w sobie tagiem, wyglądał jak ekstremalnie rzadkie
        # (unikalne) słowo i dostawał absurdalnie wysoką wagę - mimo że jako PODCIĄG
        # faktycznie trafiał w tag "konfiguracja" obecny w niemal każdym artykule.
        tags_blobs = [self._fold_pl(row.get("tags", "")).lower() for row in all_rows]
        # Tytuł/kategoria dostają TĘ SAMĄ ochronę IDF+coverage co tagi (patrz niżej) -
        # bez tego np. każdy artykuł skategoryzowany jako "Konfiguracja / ..." (ponad
        # połowa Kompendium) dostawał płaski bonus za samo słowo "konf" (skrót od
        # "konfiguracja", częsty w notatkach montera), kompletnie bez związku z
        # faktycznym tematem artykułu - i to fałszywe trafienie POTRAFIŁO dodatkowo
        # "odblokować" pełną wagę innego, osobno słabego/przypadkowego trafienia
        # (patrz NEEDS_CORROBORATION), bo liczyło się jako drugie, potwierdzające.
        title_cat_blobs = [
            self._fold_pl(f"{row.get('title','')} {row.get('category','')}").lower()
            for row in all_rows
        ]
        n_articles = max(1, len(all_rows))

        def tag_weight_for(word: str) -> float:
            df = sum(1 for blob in tags_blobs if word in blob)
            idf = math.log((n_articles + 1) / (df + 1)) + 1  # zawsze >= 1
            return TAG_WEIGHT * idf

        def title_cat_weight_for(word: str) -> float:
            df = sum(1 for blob in title_cat_blobs if word in blob)
            idf = math.log((n_articles + 1) / (df + 1)) + 1
            return TITLE_CATEGORY_WEIGHT * idf

        def score(row: dict) -> float:
            tag_words = set(self._tokenize(self._fold_pl(row.get("tags", ""))))
            tags_blob = self._fold_pl(row.get("tags", "")).lower()
            title_cat = self._fold_pl(f"{row.get('title','')} {row.get('category','')}").lower()
            title_cat_words = set(self._tokenize(title_cat))
            body = self._fold_pl(row.get("body_text", "")).lower()
            rank_bonus = 0.0
            rank = match_rank.get(row["id"])
            if rank is not None:
                rank_bonus = max(0, len(matched_ids) - rank) * 0.1

            # (wkład, czy pochodzi z pola wymagającego potwierdzenia) - dopiero po
            # zebraniu WSZYSTKICH trafień wiemy, czy pojedyncze trafienie z wolnego
            # tekstu jest odosobnione (tłumimy) czy potwierdzone czymś innym (liczy się
            # w pełni).
            contributions: list[tuple[float, bool, bool]] = []
            for field_name, value in context_fields.items():
                importance = FIELD_IMPORTANCE.get(field_name, DEFAULT_FIELD_IMPORTANCE)
                freeform = field_name in NEEDS_CORROBORATION
                words = [w for w in self._tokenize(self._fold_pl(value)) if len(w) >= 3]
                for w in words:
                    contrib = 0.0
                    corroborates = False
                    if w in tags_blob:
                        # Skalujemy wagę wg tego, JAK DUŻĄ częścią dopasowanego taga
                        # jest samo słowo - inaczej krótki fragment/skrót (np. "konf"
                        # z odręcznego "konf:" w komentarzu) trafiający w podciąg
                        # długiego taga ("konfiguracja") liczyłby się tak samo jak
                        # trafienie niemal w cały tag (np. "skaut" w "skaut8").
                        matched_tag = next((t for t in tag_words if w in t), None)
                        coverage = (len(w) / len(matched_tag)) if matched_tag else 1.0
                        contrib = tag_weight_for(w) * importance * coverage
                        corroborates = coverage >= MIN_CORROBORATION_COVERAGE
                    elif w in title_cat:
                        matched_word = next((t for t in title_cat_words if w in t), None)
                        coverage = (len(w) / len(matched_word)) if matched_word else 1.0
                        contrib = title_cat_weight_for(w) * importance * coverage
                        corroborates = coverage >= MIN_CORROBORATION_COVERAGE
                    elif w in body:
                        contrib = BODY_WEIGHT * importance
                        corroborates = True
                    else:
                        close = difflib.get_close_matches(w, tag_words, n=1, cutoff=FUZZY_CUTOFF)
                        if close:
                            contrib = tag_weight_for(close[0]) * FUZZY_FACTOR * importance
                            corroborates = True
                    if contrib > 0:
                        contributions.append((contrib, freeform, corroborates))

            # Liczy się tylko jako potwierdzenie dla INNEGO trafienia z pola wymagającego
            # korroboracji, jeśli samo jest wystarczająco pełnym dopasowaniem (patrz
            # MIN_CORROBORATION_COVERAGE) - drobny fragment nie wystarczy.
            substantial_hits = sum(1 for _, _, corroborates in contributions if corroborates)
            raw = 0.0
            for contrib, freeform, corroborates in contributions:
                if freeform and substantial_hits <= (1 if corroborates else 0):
                    contrib *= LONE_HIT_DAMPING
                raw += contrib
            total = rank_bonus + raw
            if absent_hardware and any(hw in tags_blob for hw in absent_hardware):
                total *= HARDWARE_ABSENT_PENALTY
            return total

        scored = [
            (score(row), idx, row) for idx, row in enumerate(ordered)
        ]
        scored.sort(key=lambda t: (-t[0], t[1]))
        # Surowy wynik dokładany do każdego wiersza jako "_match_score" - żeby UI mogło
        # pokazać obok artykułu, jak mocno pasuje względem najlepszego (np. gdy dwa
        # artykuły są potencjalnie pomocne, widać po liczbach który jest lepszy).
        for s, _, row in scored:
            row["_match_score"] = s
        return [row for _, _, row in scored]

    def clear_kompendium_articles(self) -> None:
        self._conn.execute("DELETE FROM kompendium_articles")
        self._conn.execute("DELETE FROM kompendium_articles_fts")
        self._conn.commit()

    # --- Komendy ---

    def add_kompendium_command(self, brand: str, command: str, description: str, typ: str = "") -> int:
        cur = self._conn.execute(
            "INSERT INTO kompendium_commands (brand, command, description, typ) VALUES (?, ?, ?, ?)",
            (brand.strip(), command.strip(), description.strip(), typ.strip()),
        )
        cmd_id = cur.lastrowid
        self._conn.execute(
            "INSERT INTO kompendium_commands_fts (rowid, brand, command, description) VALUES (?, ?, ?, ?)",
            (cmd_id, self._fold_pl(brand.strip()), self._fold_pl(command.strip()),
             self._fold_pl(description.strip())),
        )
        self._conn.commit()
        return cmd_id

    def update_kompendium_command(self, cmd_id: int, brand: str, command: str, description: str,
                                   typ: str = "") -> None:
        self._conn.execute(
            """UPDATE kompendium_commands
               SET brand=?, command=?, description=?, typ=?, updated_at=datetime('now')
               WHERE id=?""",
            (brand.strip(), command.strip(), description.strip(), typ.strip(), cmd_id),
        )
        self._conn.execute("DELETE FROM kompendium_commands_fts WHERE rowid=?", (cmd_id,))
        self._conn.execute(
            "INSERT INTO kompendium_commands_fts (rowid, brand, command, description) VALUES (?, ?, ?, ?)",
            (cmd_id, self._fold_pl(brand.strip()), self._fold_pl(command.strip()),
             self._fold_pl(description.strip())),
        )
        self._conn.commit()

    def delete_kompendium_command(self, cmd_id: int) -> None:
        self._conn.execute("DELETE FROM kompendium_commands WHERE id=?", (cmd_id,))
        self._conn.execute("DELETE FROM kompendium_commands_fts WHERE rowid=?", (cmd_id,))
        self._conn.commit()

    def get_all_kompendium_commands(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM kompendium_commands ORDER BY brand COLLATE NOCASE, command COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]

    def search_kompendium_commands(self, query: str, brand: str = "", typ: str = "") -> list[dict]:
        fts_query = self._fts_escape(query)
        if not fts_query:
            rows_source = self.get_all_kompendium_commands()
        else:
            # kolumny fts5: brand, command, description - komenda sama w sobie to
            # najbardziej wyróżniający tekst, więc waży najmocniej.
            rows = self._conn.execute(
                """SELECT c.* FROM kompendium_commands c
                   JOIN kompendium_commands_fts f ON f.rowid = c.id
                   WHERE kompendium_commands_fts MATCH ?
                   ORDER BY bm25(kompendium_commands_fts, 2.0, 6.0, 1.5)""",
                (fts_query,),
            ).fetchall()
            rows_source = [dict(r) for r in rows]
        if brand:
            rows_source = [r for r in rows_source if r["brand"].strip().lower() == brand.strip().lower()]
        if typ:
            rows_source = [r for r in rows_source if r["typ"].strip().lower() == typ.strip().lower()]
        return rows_source

    def clear_kompendium_commands(self) -> None:
        self._conn.execute("DELETE FROM kompendium_commands")
        self._conn.execute("DELETE FROM kompendium_commands_fts")
        self._conn.commit()

    def export_kompendium_data(self) -> dict:
        """Całość Bazy wiedzy (artykuły + komendy) do jednego słownika - używane
        zarówno przez ręczny eksport do pliku JSON, jak i przy budowaniu payloadu do
        cichej synchronizacji w tle (patrz main.py, KompendiumSyncChecker)."""
        return {
            "articles": self.get_all_kompendium_articles(),
            "commands": self.get_all_kompendium_commands(),
        }

    def import_kompendium_data(self, data: dict) -> tuple[int, int]:
        """Nadpisuje CAŁĄ lokalną Bazę wiedzy danymi z `data` (shape jak z export_
        kompendium_data: {"articles": [...], "commands": [...]}) - współdzielone przez
        ręczny import z pliku JSON (kompendium_dialog._on_import) i cichą synchronizację
        w tle. Zwraca (liczba_artykułów, liczba_komend)."""
        articles = data.get("articles", []) or []
        commands = data.get("commands", []) or []
        self.clear_kompendium_articles()
        self.clear_kompendium_commands()
        for a in articles:
            self.add_kompendium_article(
                a.get("title", ""), a.get("category", ""), a.get("tags", ""),
                a.get("body_html", ""), a.get("body_text", ""), a.get("source_url", ""),
            )
        for c in commands:
            self.add_kompendium_command(
                c.get("brand", ""), c.get("command", ""), c.get("description", ""), c.get("typ", "")
            )
        return len(articles), len(commands)

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
