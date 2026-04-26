-- migrations/004_extra_devices.sql
-- Słownik urządzeń dodatkowych pogrupowanych po flocie

CREATE TABLE IF NOT EXISTS extra_devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fleet_name  TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
    device_name TEXT NOT NULL COLLATE NOCASE,
    UNIQUE (fleet_name, device_name)
);

CREATE INDEX IF NOT EXISTS idx_ed_fleet ON extra_devices(fleet_name);
