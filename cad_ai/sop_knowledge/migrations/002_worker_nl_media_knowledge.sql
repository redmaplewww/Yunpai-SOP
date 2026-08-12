CREATE TABLE IF NOT EXISTS nl_change_proposal (
    id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES product_route(id) ON DELETE CASCADE,
    instruction TEXT NOT NULL,
    parsed_json TEXT NOT NULL,
    parser_kind TEXT NOT NULL CHECK(parser_kind IN ('deterministic','llm','deterministic_fallback')),
    status TEXT NOT NULL DEFAULT 'preview' CHECK(status IN ('preview','applied','dismissed')),
    requested_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS media_asset (
    id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES product_route(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    source_note TEXT NOT NULL DEFAULT '',
    uploaded_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(route_id, sha256)
);

CREATE TABLE IF NOT EXISTS step_media (
    id INTEGER PRIMARY KEY,
    route_step_id INTEGER NOT NULL REFERENCES route_step(id) ON DELETE CASCADE,
    media_asset_id INTEGER NOT NULL REFERENCES media_asset(id) ON DELETE CASCADE,
    caption TEXT NOT NULL DEFAULT '',
    link_state TEXT NOT NULL DEFAULT 'draft' CHECK(link_state IN ('draft','confirmed','rejected')),
    confirmed_by TEXT,
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(route_step_id, media_asset_id)
);

CREATE TABLE IF NOT EXISTS knowledge_fragment (
    id INTEGER PRIMARY KEY,
    route_step_id INTEGER NOT NULL UNIQUE REFERENCES route_step(id) ON DELETE CASCADE,
    route_id INTEGER NOT NULL REFERENCES product_route(id) ON DELETE CASCADE,
    route_version INTEGER NOT NULL,
    product_code TEXT NOT NULL,
    process_family_code TEXT NOT NULL,
    step_code TEXT NOT NULL,
    title TEXT NOT NULL,
    searchable_text TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    confirmed_by TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    reuse_eligible INTEGER NOT NULL DEFAULT 0 CHECK(reuse_eligible IN (0,1))
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fragment_fts USING fts5(fragment_id UNINDEXED, product_code, process_family_code, title, searchable_text);
CREATE INDEX IF NOT EXISTS idx_proposal_route_status ON nl_change_proposal(route_id, status);
CREATE INDEX IF NOT EXISTS idx_step_media_step_state ON step_media(route_step_id, link_state);
CREATE INDEX IF NOT EXISTS idx_media_asset_route_name ON media_asset(route_id, original_name);
CREATE INDEX IF NOT EXISTS idx_knowledge_fragment_family ON knowledge_fragment(process_family_code, confirmed_at DESC);

CREATE TRIGGER IF NOT EXISTS media_asset_approved_route_immutable_insert
BEFORE INSERT ON media_asset
WHEN (SELECT status FROM product_route WHERE id=NEW.route_id)='approved'
BEGIN
    SELECT RAISE(ABORT, 'approved route media is immutable; create a revision');
END;

CREATE TRIGGER IF NOT EXISTS media_asset_approved_route_immutable_update
BEFORE UPDATE ON media_asset
WHEN (SELECT status FROM product_route WHERE id=OLD.route_id)='approved'
BEGIN
    SELECT RAISE(ABORT, 'approved route media is immutable; create a revision');
END;

CREATE TRIGGER IF NOT EXISTS media_asset_approved_route_immutable_delete
BEFORE DELETE ON media_asset
WHEN (SELECT status FROM product_route WHERE id=OLD.route_id)='approved'
BEGIN
    SELECT RAISE(ABORT, 'approved route media is immutable; create a revision');
END;

CREATE TRIGGER IF NOT EXISTS step_media_approved_route_immutable_insert
BEFORE INSERT ON step_media
WHEN (SELECT status FROM product_route WHERE id=(SELECT route_id FROM route_step WHERE id=NEW.route_step_id))='approved'
BEGIN
    SELECT RAISE(ABORT, 'approved route media is immutable; create a revision');
END;

CREATE TRIGGER IF NOT EXISTS step_media_approved_route_immutable_update
BEFORE UPDATE ON step_media
WHEN (SELECT status FROM product_route WHERE id=(SELECT route_id FROM route_step WHERE id=OLD.route_step_id))='approved'
BEGIN
    SELECT RAISE(ABORT, 'approved route media is immutable; create a revision');
END;

CREATE TRIGGER IF NOT EXISTS step_media_approved_route_immutable_delete
BEFORE DELETE ON step_media
WHEN (SELECT status FROM product_route WHERE id=(SELECT route_id FROM route_step WHERE id=OLD.route_step_id))='approved'
BEGIN
    SELECT RAISE(ABORT, 'approved route media is immutable; create a revision');
END;
