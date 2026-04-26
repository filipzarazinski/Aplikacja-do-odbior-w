-- migrations/001_initial.sql
-- Schemat bazy danych aplikacji Odbiory
-- Odzwierciedla strukturę arkusza "Odbiory" z pliku xlsb
-- oraz słowniki z arkuszy pomocniczych (Firmy, CanConfig itp.)

-- ============================================================
-- SŁOWNIKI (tabele referencyjne)
-- ============================================================

CREATE TABLE IF NOT EXISTS companies (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    -- Typ protokołu urządzeń tej firmy (FM/FM2/FM3/FM4)
    -- Pozwala na auto-dobór ustawień przy wyborze firmy
    device_protocol TEXT DEFAULT 'FM3',
    fleet_type      TEXT DEFAULT 'Zwykłe',  -- Zwykłe / VIP_A / VIP_B
    is_active       INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS vehicle_brands (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS technicians (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    is_active INTEGER DEFAULT 1
);

-- Konfiguracje CAN – słownik z arkusza CanConfig
CREATE TABLE IF NOT EXISTS can_configs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    -- JSON z pełną konfiguracją magistrali CAN (dla protokołu montażowego)
    config_json TEXT    NOT NULL DEFAULT '{}'
);

-- Listy funkcji DIN – wartości do ComboBoxów DIN
CREATE TABLE IF NOT EXISTS din_functions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    -- czy wymaga pola S/N (zabezpieczenie, wlew)
    needs_sn INTEGER DEFAULT 0
);

-- ============================================================
-- GŁÓWNA TABELA REKORDÓW SERWISOWYCH
-- ============================================================

CREATE TABLE IF NOT EXISTS service_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- === SEKCJA: Identyfikacja ===
    -- Typ wpisu: 'Montaż' lub 'Serwis' (demontaż – przyszłość)
    record_type     TEXT    NOT NULL DEFAULT 'Montaż',
    service_date    TEXT,               -- format ISO: YYYY-MM-DD
    service_hour    INTEGER,            -- godzina (0-23)
    service_minute  INTEGER,            -- minuta (0-59)

    -- === SEKCJA: Firma i pojazd ===
    company_name    TEXT,               -- nazwa firmy (denormalizacja dla szybkości)
    fleet_name      TEXT,               -- nazwa floty / oddziału
    license_plate   TEXT,               -- numer rejestracyjny
    side_number     TEXT,               -- numer boczny
    vehicle_brand   TEXT,               -- marka i model pojazdu (z ListBox4)
    vehicle_type    TEXT,               -- Ciężarowy / Osobowy / Maszyna / Autobus / Inny

    -- === SEKCJA: Urządzenie ===
    device_id       TEXT,               -- ID urządzenia (IMEI/S/N)
    sim_number      TEXT,               -- numer SIM
    device_model    TEXT,               -- model urządzenia (np. FM3, Albatros 8.3)

    -- === SEKCJA: Tacho ===
    firmware_tacho  TEXT,               -- firmware tachografu (np. "Siemens 133")
    recorder_location TEXT,             -- gdzie rejestratory (podBranie D8)
    mileage         INTEGER,            -- przebieg pojazdu (km)

    -- === SEKCJA: Sondy temperatury (Teltonika) ===
    probe1_id       TEXT,
    probe1_capacity REAL,
    probe1_length   REAL,
    probe2_id       TEXT,
    probe2_capacity REAL,
    probe2_length   REAL,
    right_tank_probe TEXT,              -- który zbiornik sondą prawą
    temp1_response  TEXT,               -- TMR1Response
    temp2_response  TEXT,               -- TMR2Response
    thermometer1    TEXT,
    thermometer2    TEXT,
    thermometer_ela TEXT,

    -- === SEKCJA: CAN / Konfiguracja ===
    can_config_name TEXT,               -- nazwa wybranej konfiguracji CAN
    fuel_from_can   INTEGER DEFAULT 0,  -- czy paliwo z CAN (CheckBox12)
    -- CheckBox50 – czy CAN aktywny
    can_active      INTEGER DEFAULT 0,
    -- CheckBox51-58 – bity CAN (przechowujemy jako 8-znakowy string "11111110")
    can_checkboxes  TEXT    DEFAULT '00000000',
    -- RadioButton: Ciężarowy(10) / Osobowy(11)
    can_vehicle_type TEXT,

    -- === SEKCJA: DIN (3 kanały) ===
    -- Każdy kanał: funkcja, typ DIN, poziom niski/wysoki, S/N
    din1_function   TEXT,
    din1_type       TEXT,
    din1_low        INTEGER DEFAULT 0,
    din1_high       INTEGER DEFAULT 0,
    din1_sn         TEXT,

    din2_function   TEXT,
    din2_type       TEXT,
    din2_low        INTEGER DEFAULT 0,
    din2_high       INTEGER DEFAULT 0,
    din2_sn         TEXT,

    din3_function   TEXT,
    din3_type       TEXT,
    din3_low        INTEGER DEFAULT 0,
    din3_high       INTEGER DEFAULT 0,
    din3_sn         TEXT,

    -- === SEKCJA: Dodatkowe funkcje ===
    -- CheckbokiRFID, Immo, Tablet, Zasilanie, itp.
    has_rfid        INTEGER DEFAULT 0,
    has_immo        INTEGER DEFAULT 0,
    has_tablet      INTEGER DEFAULT 0,
    tablet_sn       TEXT,
    has_power       INTEGER DEFAULT 0,  -- zewnętrzne zasilanie

    -- === SEKCJA: Termometry (CheckBox13, 19, 20, 21) ===
    -- JSON z listą aktywnych sond i ich konfiguracją
    termometry_json TEXT    DEFAULT '[]',

    -- === SEKCJA: Czynności montera ===
    -- CheckBox70-77 – czynności na zakładce 3 (markPageUsage 3)
    actions_page3   TEXT    DEFAULT '00000000',
    -- Pełna lista czynności – JSON array bool
    actions_json    TEXT    DEFAULT '[]',

    -- === SEKCJA: Konfiguracja JSON (do protokołu) ===
    -- Pełny JSON generowany przez createJSON() w VBA
    config_json     TEXT    DEFAULT '{}',

    -- === SEKCJA: Technician & Komentarz ===
    technician_name TEXT,
    comment         TEXT,
    duty_time_min   INTEGER,            -- czas dyżurowy (minuty)

    -- === SEKCJA: Metadane ===
    last_page       INTEGER DEFAULT 0,  -- ostatnia otwarta zakładka MultiPage
    has_letters     INTEGER DEFAULT 0,  -- MaBoLitery
    recorder_type   TEXT,               -- TypRejestratora

    created_at      TEXT    DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- Trigger aktualizujący updated_at przy każdym UPDATE
CREATE TRIGGER IF NOT EXISTS trg_service_records_updated
AFTER UPDATE ON service_records
FOR EACH ROW
BEGIN
    UPDATE service_records
    SET updated_at = datetime('now', 'localtime')
    WHERE id = OLD.id;
END;

-- Indeksy dla najczęstszych zapytań (filtrowanie w głównej tabeli)
CREATE INDEX IF NOT EXISTS idx_sr_company    ON service_records(company_name);
CREATE INDEX IF NOT EXISTS idx_sr_plate      ON service_records(license_plate);
CREATE INDEX IF NOT EXISTS idx_sr_date       ON service_records(service_date);
CREATE INDEX IF NOT EXISTS idx_sr_technician ON service_records(technician_name);
CREATE INDEX IF NOT EXISTS idx_sr_device     ON service_records(device_id);
