CREATE TABLE IF NOT EXISTS route_section (
    id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES product_route(id) ON DELETE CASCADE,
    section_type TEXT NOT NULL CHECK(section_type IN (
        'product_identity','bom_material','equipment_fixture','process_parameter',
        'quality_control','packaging_label','ie_timing','release_signoff'
    )),
    version INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    review_state TEXT NOT NULL DEFAULT 'unreviewed' CHECK(review_state IN ('unreviewed','confirmed','rejected','needs_revision')),
    reviewer_comment TEXT NOT NULL DEFAULT '',
    source_json TEXT NOT NULL DEFAULT '[]',
    conflicts_json TEXT NOT NULL DEFAULT '[]',
    unknowns_json TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(route_id, section_type, version)
);

CREATE INDEX IF NOT EXISTS idx_route_section_latest ON route_section(route_id, section_type, version DESC);

CREATE TRIGGER IF NOT EXISTS route_section_approved_immutable_insert
BEFORE INSERT ON route_section
WHEN EXISTS (SELECT 1 FROM product_route r WHERE r.id = NEW.route_id AND r.status = 'approved')
BEGIN
    SELECT RAISE(ABORT, 'approved route sections are immutable; create a new revision');
END;

CREATE TRIGGER IF NOT EXISTS route_section_approved_immutable_update
BEFORE UPDATE ON route_section
WHEN EXISTS (SELECT 1 FROM product_route r WHERE r.id = OLD.route_id AND r.status = 'approved')
BEGIN
    SELECT RAISE(ABORT, 'approved route sections are immutable; create a new revision');
END;

CREATE TRIGGER IF NOT EXISTS route_section_approved_immutable_delete
BEFORE DELETE ON route_section
WHEN EXISTS (SELECT 1 FROM product_route r WHERE r.id = OLD.route_id AND r.status = 'approved')
BEGIN
    SELECT RAISE(ABORT, 'approved route sections are immutable');
END;
