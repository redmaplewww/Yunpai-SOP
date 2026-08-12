CREATE TABLE IF NOT EXISTS sop_chat_message (
    id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES product_route(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sop_chat_route_time ON sop_chat_message(route_id, created_at, id);
