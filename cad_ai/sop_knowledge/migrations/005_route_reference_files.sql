CREATE TABLE IF NOT EXISTS route_reference_file (
    id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES product_route(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    source_note TEXT NOT NULL DEFAULT '',
    review_state TEXT NOT NULL DEFAULT 'needs_revision'
        CHECK(review_state IN ('unreviewed','confirmed','rejected','needs_revision')),
    uploaded_by TEXT NOT NULL,
    confirmed_by TEXT,
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(route_id, sha256)
);

CREATE INDEX IF NOT EXISTS idx_route_reference_file_route_time
    ON route_reference_file(route_id, created_at, id);

CREATE TRIGGER IF NOT EXISTS route_reference_file_approved_route_immutable_insert
BEFORE INSERT ON route_reference_file
WHEN (SELECT status FROM product_route WHERE id=NEW.route_id)='approved'
BEGIN
    SELECT RAISE(ABORT, 'approved route reference files are immutable; create a revision');
END;

CREATE TRIGGER IF NOT EXISTS route_reference_file_approved_route_immutable_update
BEFORE UPDATE ON route_reference_file
WHEN (SELECT status FROM product_route WHERE id=OLD.route_id)='approved'
BEGIN
    SELECT RAISE(ABORT, 'approved route reference files are immutable; create a revision');
END;

CREATE TRIGGER IF NOT EXISTS route_reference_file_approved_route_immutable_delete
BEFORE DELETE ON route_reference_file
WHEN (SELECT status FROM product_route WHERE id=OLD.route_id)='approved'
BEGIN
    SELECT RAISE(ABORT, 'approved route reference files are immutable; create a revision');
END;
