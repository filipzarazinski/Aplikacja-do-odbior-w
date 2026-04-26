-- 006_cleanup.sql
-- Usuwa nieużywane tabele (brak UI, brak wywołań w kodzie)
DROP TABLE IF EXISTS can_configs;
DROP TABLE IF EXISTS din_functions;
DROP TABLE IF EXISTS vehicle_brands;
