-- migrations/003_dictionaries.sql
-- Tabele słowników: modele pojazdów i lokalizacje rejestratora

CREATE TABLE IF NOT EXISTS vehicle_models (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_model  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    vehicle_type TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_vm_brand ON vehicle_models(brand_model);

CREATE TABLE IF NOT EXISTS recorder_locations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    location TEXT NOT NULL UNIQUE COLLATE NOCASE
);
