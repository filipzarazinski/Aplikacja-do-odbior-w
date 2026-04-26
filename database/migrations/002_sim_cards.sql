-- migrations/002_sim_cards.sql
-- Tabela bazy kart SIM oraz ustawień aplikacji

CREATE TABLE IF NOT EXISTS sim_cards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sim        TEXT NOT NULL,
    ccid       TEXT NOT NULL UNIQUE,
    synced_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_sim_cards_ccid ON sim_cards(ccid);
CREATE INDEX IF NOT EXISTS idx_sim_cards_sim  ON sim_cards(sim);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
