PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_migration (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS process_family (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','under_review','approved','deprecated')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product (
    id INTEGER PRIMARY KEY,
    product_code TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    process_family_id INTEGER REFERENCES process_family(id),
    lifecycle_status TEXT NOT NULL DEFAULT 'draft' CHECK(lifecycle_status IN ('draft','under_review','approved','deprecated')),
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_alias (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'source',
    UNIQUE(product_id, alias)
);

CREATE TABLE IF NOT EXISTS product_feature (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
    feature_key TEXT NOT NULL,
    feature_value TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    conflict_status TEXT NOT NULL DEFAULT 'clear' CHECK(conflict_status IN ('clear','conflict','unknown')),
    evidence_id INTEGER,
    UNIQUE(product_id, feature_key, normalized_value)
);

CREATE TABLE IF NOT EXISTS operation_template (
    id INTEGER PRIMARY KEY,
    process_family_id INTEGER NOT NULL REFERENCES process_family(id),
    template_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','under_review','approved','deprecated')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_template_version (
    id INTEGER PRIMARY KEY,
    operation_template_id INTEGER NOT NULL REFERENCES operation_template(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','under_review','approved','deprecated')),
    definition_json TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(operation_template_id, version)
);

CREATE TABLE IF NOT EXISTS product_route (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES product(id),
    process_family_id INTEGER NOT NULL REFERENCES process_family(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','under_review','approved','deprecated')),
    approval_scope TEXT NOT NULL DEFAULT 'none' CHECK(approval_scope IN ('none','formal_production','demonstration_only')),
    route_name TEXT NOT NULL,
    route_summary TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL CHECK(source_kind IN ('exact_approved','similar_approved','family_template','manual','legacy_candidate')),
    parent_route_id INTEGER REFERENCES product_route(id),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(product_id, version)
);

CREATE TABLE IF NOT EXISTS route_step (
    id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES product_route(id) ON DELETE CASCADE,
    parent_step_id INTEGER REFERENCES route_step(id) ON DELETE CASCADE,
    sequence_no REAL NOT NULL,
    step_code TEXT NOT NULL,
    title TEXT NOT NULL,
    action TEXT NOT NULL,
    why TEXT NOT NULL,
    input_json TEXT NOT NULL,
    material_json TEXT NOT NULL,
    tool_equipment_json TEXT NOT NULL,
    fixture_json TEXT NOT NULL,
    parameter_json TEXT NOT NULL,
    method_json TEXT NOT NULL,
    quality_check_json TEXT NOT NULL,
    acceptance_criteria_json TEXT NOT NULL,
    safety_json TEXT NOT NULL,
    record_output_json TEXT NOT NULL,
    exception_json TEXT NOT NULL,
    unknowns_json TEXT NOT NULL,
    review_state TEXT NOT NULL DEFAULT 'unreviewed' CHECK(review_state IN ('unreviewed','confirmed','rejected','needs_revision')),
    reviewer_comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(route_id, step_code)
);

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

CREATE TABLE IF NOT EXISTS evidence_source (
    id INTEGER PRIMARY KEY,
    product_id INTEGER REFERENCES product(id),
    source_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_hash TEXT,
    page_or_sheet TEXT NOT NULL DEFAULT '',
    excerpt TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL,
    UNIQUE(product_id, source_path, page_or_sheet, excerpt)
);

CREATE TABLE IF NOT EXISTS field_provenance (
    id INTEGER PRIMARY KEY,
    route_step_id INTEGER REFERENCES route_step(id) ON DELETE CASCADE,
    route_id INTEGER REFERENCES product_route(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    evidence_id INTEGER REFERENCES evidence_source(id),
    source_route_id INTEGER REFERENCES product_route(id),
    source_route_version INTEGER,
    source_step_code TEXT,
    source_field_name TEXT,
    confidence REAL NOT NULL DEFAULT 0.0,
    conflict_status TEXT NOT NULL DEFAULT 'clear' CHECK(conflict_status IN ('clear','conflict','unknown')),
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS review_session (
    id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES product_route(id),
    reviewer TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','under_review','approved','rejected')),
    comment TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    submitted_at TEXT,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS review_decision (
    id INTEGER PRIMARY KEY,
    review_session_id INTEGER NOT NULL REFERENCES review_session(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('product','route','step','field','bom','tooling','parameter','qc','packaging','ie_time','signoff')),
    entity_id INTEGER,
    field_name TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL CHECK(decision IN ('confirmed','rejected','needs_revision')),
    old_value_json TEXT,
    new_value_json TEXT,
    comment TEXT NOT NULL DEFAULT '',
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_snapshot (
    id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL UNIQUE REFERENCES product_route(id),
    route_version INTEGER NOT NULL,
    approval_scope TEXT NOT NULL CHECK(approval_scope IN ('formal_production','demonstration_only')),
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reuse_link (
    id INTEGER PRIMARY KEY,
    target_route_id INTEGER NOT NULL REFERENCES product_route(id) ON DELETE CASCADE,
    source_route_id INTEGER NOT NULL REFERENCES product_route(id),
    source_route_version INTEGER NOT NULL,
    similarity REAL NOT NULL,
    match_basis_json TEXT NOT NULL,
    field_map_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_candidate (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft','rejected')),
    rejection_reason TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(source_path, batch_id)
);

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

CREATE TABLE IF NOT EXISTS sop_chat_message (
    id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES product_route(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS product_fts USING fts5(product_code, product_name, aliases, features);
CREATE VIRTUAL TABLE IF NOT EXISTS route_fts USING fts5(route_id UNINDEXED, product_code, route_name, route_text);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fragment_fts USING fts5(fragment_id UNINDEXED, product_code, process_family_code, title, searchable_text);

CREATE INDEX IF NOT EXISTS idx_product_alias_alias ON product_alias(alias);
CREATE INDEX IF NOT EXISTS idx_product_feature_lookup ON product_feature(feature_key, normalized_value);
CREATE INDEX IF NOT EXISTS idx_product_route_lookup ON product_route(product_id, status, version);
CREATE INDEX IF NOT EXISTS idx_route_step_route_seq ON route_step(route_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_route_section_latest ON route_section(route_id, section_type, version DESC);
CREATE INDEX IF NOT EXISTS idx_provenance_step_field ON field_provenance(route_step_id, field_name);
CREATE INDEX IF NOT EXISTS idx_review_route ON review_session(route_id, status);
CREATE INDEX IF NOT EXISTS idx_reuse_target ON reuse_link(target_route_id);
CREATE INDEX IF NOT EXISTS idx_proposal_route_status ON nl_change_proposal(route_id, status);
CREATE INDEX IF NOT EXISTS idx_step_media_step_state ON step_media(route_step_id, link_state);
CREATE INDEX IF NOT EXISTS idx_media_asset_route_name ON media_asset(route_id, original_name);
CREATE INDEX IF NOT EXISTS idx_knowledge_fragment_family ON knowledge_fragment(process_family_code, confirmed_at DESC);
CREATE INDEX IF NOT EXISTS idx_sop_chat_route_time ON sop_chat_message(route_id, created_at, id);

CREATE TRIGGER IF NOT EXISTS product_route_approved_immutable
BEFORE UPDATE ON product_route
WHEN OLD.status = 'approved'
BEGIN
    SELECT RAISE(ABORT, 'approved route is immutable; create a new revision');
END;

CREATE TRIGGER IF NOT EXISTS product_route_approved_no_delete
BEFORE DELETE ON product_route
WHEN OLD.status = 'approved'
BEGIN
    SELECT RAISE(ABORT, 'approved route is immutable');
END;

CREATE TRIGGER IF NOT EXISTS route_step_approved_immutable_update
BEFORE UPDATE ON route_step
WHEN EXISTS (SELECT 1 FROM product_route r WHERE r.id = OLD.route_id AND r.status = 'approved')
BEGIN
    SELECT RAISE(ABORT, 'approved route steps are immutable; create a new revision');
END;

CREATE TRIGGER IF NOT EXISTS route_step_approved_immutable_delete
BEFORE DELETE ON route_step
WHEN EXISTS (SELECT 1 FROM product_route r WHERE r.id = OLD.route_id AND r.status = 'approved')
BEGIN
    SELECT RAISE(ABORT, 'approved route steps are immutable');
END;

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

CREATE TRIGGER IF NOT EXISTS operation_template_version_approved_immutable
BEFORE UPDATE ON operation_template_version
WHEN OLD.status = 'approved'
BEGIN
    SELECT RAISE(ABORT, 'approved operation template version is immutable; create a new version');
END;

CREATE TRIGGER IF NOT EXISTS operation_template_version_approved_no_delete
BEFORE DELETE ON operation_template_version
WHEN OLD.status = 'approved'
BEGIN
    SELECT RAISE(ABORT, 'approved operation template version is immutable');
END;
