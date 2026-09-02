-- Kompendium wiedzy: artykuły (redagowane w aplikacji) + komendy do rejestratorów
-- (Setivo/Teltonika/Albatros itd.). Osobne tabele FTS5 do szybkiego, "inteligentnego"
-- wyszukiwania (dopasowanie słów/prefiksów + ranking trafności), utrzymywane ręcznie
-- z poziomu Pythona (bez triggerów SQL - prościej i pewniej dla tak małego zbioru danych).

CREATE TABLE IF NOT EXISTS kompendium_articles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    category   TEXT    NOT NULL DEFAULT '',
    tags       TEXT    NOT NULL DEFAULT '',
    body_html  TEXT    NOT NULL DEFAULT '',
    body_text  TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS kompendium_articles_fts USING fts5(
    title, category, tags, body_text
);

CREATE TABLE IF NOT EXISTS kompendium_commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brand       TEXT    NOT NULL DEFAULT '',
    command     TEXT    NOT NULL DEFAULT '',
    description TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS kompendium_commands_fts USING fts5(
    brand, command, description
);
