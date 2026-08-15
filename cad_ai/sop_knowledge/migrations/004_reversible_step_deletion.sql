CREATE TABLE IF NOT EXISTS step_deletion (
    id INTEGER PRIMARY KEY,
    deletion_token TEXT NOT NULL UNIQUE,
    route_id INTEGER NOT NULL REFERENCES product_route(id) ON DELETE CASCADE,
    root_step_id INTEGER NOT NULL REFERENCES route_step(id) ON DELETE CASCADE,
    root_step_code TEXT NOT NULL,
    root_step_title TEXT NOT NULL,
    deleted_by TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    restore_deadline TEXT NOT NULL,
    restored_at TEXT,
    restored_by TEXT
);

CREATE TABLE IF NOT EXISTS step_deletion_item (
    deletion_id INTEGER NOT NULL REFERENCES step_deletion(id) ON DELETE CASCADE,
    route_step_id INTEGER NOT NULL REFERENCES route_step(id) ON DELETE CASCADE,
    original_sequence_no REAL NOT NULL,
    PRIMARY KEY(deletion_id, route_step_id)
);

CREATE INDEX IF NOT EXISTS idx_step_deletion_route
    ON step_deletion(route_id, restored_at, deleted_at DESC);

CREATE INDEX IF NOT EXISTS idx_step_deletion_item_step
    ON step_deletion_item(route_step_id);

DROP VIEW IF EXISTS active_route_step;
CREATE VIEW active_route_step AS
SELECT step.*
FROM route_step AS step
WHERE NOT EXISTS (
    SELECT 1
    FROM step_deletion_item AS item
    JOIN step_deletion AS deletion ON deletion.id = item.deletion_id
    WHERE item.route_step_id = step.id
      AND deletion.restored_at IS NULL
);
