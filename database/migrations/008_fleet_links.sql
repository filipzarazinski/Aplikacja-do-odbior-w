CREATE TABLE IF NOT EXISTS fleet_links (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fleet_name TEXT    NOT NULL COLLATE NOCASE,
    url        TEXT    NOT NULL,
    UNIQUE(fleet_name)
);
