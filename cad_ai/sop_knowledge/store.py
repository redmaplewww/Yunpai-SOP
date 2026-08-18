from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import ProductIdentity, RouteDraft, RouteMatch, RouteSectionDraft, RouteStepDraft


JSON_FIELDS = {
    "inputs": "input_json",
    "materials": "material_json",
    "tool_equipment": "tool_equipment_json",
    "fixtures": "fixture_json",
    "parameters": "parameter_json",
    "method": "method_json",
    "quality_check": "quality_check_json",
    "acceptance_criteria": "acceptance_criteria_json",
    "safety": "safety_json",
    "record_output": "record_output_json",
    "exception": "exception_json",
    "unknowns": "unknowns_json",
}
EDITABLE_FIELDS = {"title", "action", "why", "sequence_no", "parent_step_id", "review_state", "reviewer_comment", *JSON_FIELDS}
ROUTE_SECTION_TYPES = (
    "product_identity",
    "bom_material",
    "equipment_fixture",
    "process_parameter",
    "quality_control",
    "packaging_label",
    "ie_timing",
    "release_signoff",
)
SECTION_DECISION_ENTITY = {
    "product_identity": "product",
    "bom_material": "bom",
    "equipment_fixture": "tooling",
    "process_parameter": "parameter",
    "quality_control": "qc",
    "packaging_label": "packaging",
    "ie_timing": "ie_time",
    "release_signoff": "signoff",
}
ROUTE_REFERENCE_FILE_TYPES: dict[str, tuple[str, bytes | None]] = {
    "application/pdf": (".pdf", b"%PDF-"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (".docx", b"PK\x03\x04"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (".xlsx", b"PK\x03\x04"),
    "text/csv": (".csv", None),
    "text/plain": (".txt", None),
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8"),
}
ROUTE_REFERENCE_FILE_MAX_BYTES = 20 * 1024 * 1024


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def merge_json_object(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge an AI-authored section patch without dropping untouched keys."""
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_json_object(result[key], value)
        else:
            result[key] = value
    return result


class SopKnowledgeStore:
    """Independent SQLite knowledge store for reviewed SOP routes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema)
            migration_dir = Path(__file__).with_name("migrations")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                migration_id = migration_path.stem
                applied = connection.execute(
                    "SELECT 1 FROM schema_migration WHERE migration_id=?", (migration_id,)
                ).fetchone()
                if applied:
                    continue
                connection.executescript(migration_path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migration(migration_id,applied_at) VALUES(?,?)",
                    (migration_id, utcnow()),
                )

    def ensure_process_family(self, code: str, name: str, description: str = "") -> int:
        now = utcnow()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO process_family(code,name,description,status,created_at,updated_at)
                   VALUES(?,?,?,'approved',?,?)
                   ON CONFLICT(code) DO UPDATE SET name=excluded.name, description=excluded.description, updated_at=excluded.updated_at""",
                (code, name, description, now, now),
            )
            return int(connection.execute("SELECT id FROM process_family WHERE code=?", (code,)).fetchone()[0])

    def create_operation_template_version(
        self,
        *,
        family_code: str,
        template_code: str,
        template_name: str,
        version: int,
        content: dict[str, Any],
        status: str = "draft",
        created_by: str = "sop_route_pipeline",
    ) -> int:
        """Store a versioned family fallback; templates never bypass human route review."""
        if status not in {"draft", "under_review", "approved", "deprecated"}:
            raise ValueError(f"unsupported template status: {status}")
        family_id = self.ensure_process_family(family_code, family_code)
        now = utcnow()
        with self.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO operation_template(process_family_id,template_code,name,status,created_at)
                   VALUES(?,?,?,'draft',?)""",
                (family_id, template_code, template_name, now),
            )
            template_id = int(connection.execute(
                "SELECT id FROM operation_template WHERE template_code=?", (template_code,)
            ).fetchone()[0])
            cursor = connection.execute(
                """INSERT INTO operation_template_version(operation_template_id,version,status,definition_json,approved_by,approved_at,created_at)
                   VALUES(?,?,?,?,NULL,NULL,?)""",
                (template_id, version, status, json.dumps({"created_by": created_by, **content}, ensure_ascii=False), now),
            )
            return int(cursor.lastrowid)

    def approve_operation_template_version(self, version_id: int, *, approved_by: str) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT operation_template_id,status FROM operation_template_version WHERE id=?", (version_id,)
            ).fetchone()
            if not row or row["status"] not in {"draft", "under_review"}:
                raise ValueError("only a draft or under-review template version can be approved")
            now = utcnow()
            connection.execute(
                "UPDATE operation_template_version SET status='approved',approved_by=?,approved_at=? WHERE id=?",
                (approved_by, now, version_id),
            )
            connection.execute(
                "UPDATE operation_template SET status='approved' WHERE id=?", (row["operation_template_id"],)
            )

    def upsert_product(self, identity: ProductIdentity, features: dict[str, str], *, evidence_ids: dict[str, int] | None = None) -> int:
        family_id = self.ensure_process_family(identity.process_family_code, identity.process_family_code)
        now = utcnow()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO product(product_code,product_name,process_family_id,lifecycle_status,description,created_at,updated_at)
                   VALUES(?,?,?,'draft',?,?,?)
                   ON CONFLICT(product_code) DO UPDATE SET product_name=excluded.product_name,
                       process_family_id=excluded.process_family_id, description=excluded.description, updated_at=excluded.updated_at""",
                (identity.product_code, identity.product_name, family_id, identity.description, now, now),
            )
            product_id = int(connection.execute("SELECT id FROM product WHERE product_code=?", (identity.product_code,)).fetchone()[0])
            for alias in identity.aliases:
                connection.execute(
                    "INSERT OR IGNORE INTO product_alias(product_id,alias,alias_type) VALUES(?,?,'source')",
                    (product_id, alias),
                )
            for key, value in features.items():
                connection.execute(
                    """INSERT OR IGNORE INTO product_feature(product_id,feature_key,feature_value,normalized_value,confidence,conflict_status,evidence_id)
                       VALUES(?,?,?,?,1.0,'clear',?)""",
                    (product_id, key, value, self._normalize(value), (evidence_ids or {}).get(key)),
                )
            aliases = " ".join(row[0] for row in connection.execute("SELECT alias FROM product_alias WHERE product_id=?", (product_id,)))
            feature_text = " ".join(f"{row[0]}:{row[1]}" for row in connection.execute("SELECT feature_key,feature_value FROM product_feature WHERE product_id=?", (product_id,)))
            connection.execute("DELETE FROM product_fts WHERE product_code=?", (identity.product_code,))
            connection.execute(
                "INSERT INTO product_fts(product_code,product_name,aliases,features) VALUES(?,?,?,?)",
                (identity.product_code, identity.product_name, aliases, feature_text),
            )
            return product_id

    def add_evidence(self, product_code: str, *, source_type: str, source_path: str, page_or_sheet: str = "", excerpt: str = "") -> int:
        with self.connect() as connection:
            product = connection.execute("SELECT id FROM product WHERE product_code=?", (product_code,)).fetchone()
            if not product:
                raise KeyError(product_code)
            path = Path(source_path)
            digest = self._file_hash(path) if path.is_file() else None
            connection.execute(
                """INSERT OR IGNORE INTO evidence_source(product_id,source_type,source_path,source_hash,page_or_sheet,excerpt,captured_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (product[0], source_type, source_path, digest, page_or_sheet, excerpt, utcnow()),
            )
            return int(connection.execute(
                "SELECT id FROM evidence_source WHERE product_id=? AND source_path=? AND page_or_sheet=? AND excerpt=?",
                (product[0], source_path, page_or_sheet, excerpt),
            ).fetchone()[0])

    def create_route(self, draft: RouteDraft, *, created_by: str = "sop_route_pipeline") -> int:
        with self.connect() as connection:
            product = connection.execute(
                "SELECT p.id,p.process_family_id FROM product p WHERE p.product_code=?", (draft.product.product_code,)
            ).fetchone()
            if not product:
                raise KeyError(f"product not stored: {draft.product.product_code}")
            now = utcnow()
            cursor = connection.execute(
                """INSERT INTO product_route(product_id,process_family_id,version,status,approval_scope,route_name,route_summary,
                       source_kind,parent_route_id,created_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,NULL,?,?,?)""",
                (product[0], product[1], draft.version, draft.status, draft.approval_scope, draft.route_name,
                 draft.route_summary, draft.source_kind, created_by, now, now),
            )
            route_id = int(cursor.lastrowid)
            step_ids: dict[str, int] = {}
            pending = list(draft.steps)
            while pending:
                progressed = False
                for step in list(pending):
                    if step.parent_step_code and step.parent_step_code not in step_ids:
                        continue
                    step_id = self._insert_step(connection, route_id, step, step_ids.get(step.parent_step_code or ""))
                    step_ids[step.step_code] = step_id
                    pending.remove(step)
                    progressed = True
                if not progressed:
                    raise ValueError("cyclic or missing parent step")
            if draft.reuse_source_route_id:
                field_map = self._build_field_map(connection, draft.reuse_source_route_id, route_id)
                connection.execute(
                    """INSERT INTO reuse_link(target_route_id,source_route_id,source_route_version,similarity,match_basis_json,field_map_json,created_at)
                       SELECT ?,id,version,?,?,?,? FROM product_route WHERE id=?""",
                    (route_id, draft.similarity or 0.0, json.dumps(draft.match_basis, ensure_ascii=False),
                     json.dumps(field_map, ensure_ascii=False), utcnow(), draft.reuse_source_route_id),
                )
                self._write_reuse_provenance(connection, route_id, draft.reuse_source_route_id, draft.similarity or 0.0)
            return route_id

    def create_route_section(
        self,
        route_id: int,
        section: RouteSectionDraft,
        *,
        created_by: str = "sop_route_pipeline",
    ) -> int:
        now = utcnow()
        with self.connect() as connection:
            route = connection.execute("SELECT status FROM product_route WHERE id=?", (route_id,)).fetchone()
            if not route:
                raise KeyError(route_id)
            cursor = connection.execute(
                """INSERT INTO route_section(
                       route_id,section_type,version,content_json,review_state,reviewer_comment,
                       source_json,conflicts_json,unknowns_json,created_by,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    route_id,
                    section.section_type,
                    section.version,
                    json.dumps(section.content, ensure_ascii=False),
                    section.review_state,
                    section.reviewer_comment,
                    json.dumps([item.model_dump(mode="json") for item in section.sources], ensure_ascii=False),
                    json.dumps(section.conflicts, ensure_ascii=False),
                    json.dumps([item.model_dump(mode="json") for item in section.unknowns], ensure_ascii=False),
                    created_by,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def revise_route_section(
        self,
        section_id: int,
        *,
        content: dict[str, Any],
        review_state: str,
        reviewer_comment: str,
        sources: list[dict[str, Any]],
        conflicts: list[str],
        unknowns: list[dict[str, Any]],
        reviewer: str,
        decision: str,
    ) -> int:
        if review_state not in {"unreviewed", "confirmed", "rejected", "needs_revision"}:
            raise ValueError("invalid section review_state")
        if decision not in {"confirmed", "rejected", "needs_revision"}:
            raise ValueError("invalid section decision")
        if not reviewer.strip():
            raise ValueError("reviewer identity is required")
        now = utcnow()
        with self.connect() as connection:
            previous = connection.execute(
                """SELECT s.*,r.status AS route_status FROM route_section s
                   JOIN product_route r ON r.id=s.route_id WHERE s.id=?""",
                (section_id,),
            ).fetchone()
            if not previous:
                raise KeyError(section_id)
            if previous["route_status"] == "approved":
                raise sqlite3.IntegrityError("approved route sections are immutable; create a new revision")
            next_version = int(connection.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM route_section WHERE route_id=? AND section_type=?",
                (previous["route_id"], previous["section_type"]),
            ).fetchone()[0])
            cursor = connection.execute(
                """INSERT INTO route_section(
                       route_id,section_type,version,content_json,review_state,reviewer_comment,
                       source_json,conflicts_json,unknowns_json,created_by,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    previous["route_id"], previous["section_type"], next_version,
                    json.dumps(content, ensure_ascii=False), review_state, reviewer_comment,
                    json.dumps(sources, ensure_ascii=False), json.dumps(conflicts, ensure_ascii=False),
                    json.dumps(unknowns, ensure_ascii=False), reviewer, now, now,
                ),
            )
            new_id = int(cursor.lastrowid)
            session_id = self._active_review_session(connection, int(previous["route_id"]), reviewer)
            entity_type = SECTION_DECISION_ENTITY[previous["section_type"]]
            old_payload = {
                "content": json.loads(previous["content_json"]),
                "review_state": previous["review_state"],
                "reviewer_comment": previous["reviewer_comment"],
                "sources": json.loads(previous["source_json"]),
                "conflicts": json.loads(previous["conflicts_json"]),
                "unknowns": json.loads(previous["unknowns_json"]),
            }
            new_payload = {
                "content": content, "review_state": review_state, "reviewer_comment": reviewer_comment,
                "sources": sources, "conflicts": conflicts, "unknowns": unknowns,
            }
            connection.execute(
                """INSERT INTO review_decision(
                       review_session_id,entity_type,entity_id,field_name,decision,
                       old_value_json,new_value_json,comment,decided_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    session_id, entity_type, new_id, previous["section_type"], decision,
                    json.dumps(old_payload, ensure_ascii=False), json.dumps(new_payload, ensure_ascii=False),
                    reviewer_comment or f"section revised by {reviewer}", now,
                ),
            )
            return new_id

    def list_route_sections(self, route_id: int, *, include_history: bool = False) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if include_history:
                rows = connection.execute(
                    "SELECT * FROM route_section WHERE route_id=? ORDER BY section_type,version", (route_id,)
                )
            else:
                rows = connection.execute(
                    """SELECT s.* FROM route_section s
                       JOIN (SELECT section_type,MAX(version) AS version FROM route_section WHERE route_id=? GROUP BY section_type) latest
                         ON latest.section_type=s.section_type AND latest.version=s.version
                       WHERE s.route_id=? ORDER BY s.section_type""",
                    (route_id, route_id),
                )
            return [self._decode_section(row) for row in rows]

    def clone_approved_route_as_draft(
        self,
        source_route_id: int,
        target_identity: ProductIdentity,
        *,
        similarity: float,
        match_basis: dict[str, Any],
        created_by: str = "approved_route_retriever",
    ) -> int:
        source = self.get_route(source_route_id)
        if source["route"]["status"] != "approved":
            raise ValueError("only approved routes can be reused")
        id_to_code = {step["id"]: step["step_code"] for step in source["steps"]}
        steps = [
            RouteStepDraft(
                step_code=step["step_code"],
                sequence_no=step["sequence_no"],
                parent_step_code=id_to_code.get(step["parent_step_id"]),
                title=step["title"],
                action=step["action"],
                why=step["why"],
                work_image_slots=int(step.get("work_image_slots") or 6),
                inputs=step["input_json"],
                materials=step["material_json"],
                tool_equipment=step["tool_equipment_json"],
                fixtures=step["fixture_json"],
                parameters=step["parameter_json"],
                method=step["method_json"],
                quality_check=step["quality_check_json"],
                acceptance_criteria=step["acceptance_criteria_json"],
                safety=step["safety_json"],
                record_output=step["record_output_json"],
                exception=step["exception_json"],
                unknowns=step["unknowns_json"],
                review_state="unreviewed",
                reviewer_comment="reused from approved route; target-specific fields require human review",
            )
            for step in source["steps"]
        ]
        draft = RouteDraft(
            product=target_identity,
            route_name=f"{target_identity.product_code} 近似批准路线复用草案",
            route_summary=f"字段级复用自 {source['route']['product_code']} v{source['route']['version']}；目标产品差异必须逐项复核。",
            source_kind="similar_approved",
            steps=steps,
            similarity=similarity,
            reuse_source_route_id=source_route_id,
            match_basis=match_basis,
        )
        route_id = self.create_route(draft, created_by=created_by)
        for section in source.get("sections", []):
            sources = list(section["source_json"])
            sources.append({
                "source_type": "approved_route_reuse",
                "source_path": f"route:{source_route_id}:section:{section['section_type']}:v{section['version']}",
                "page_or_sheet": "",
                "excerpt": f"reused from {source['route']['product_code']} v{source['route']['version']}",
                "confidence": similarity,
                "conflict_status": "unknown",
            })
            self.create_route_section(
                route_id,
                RouteSectionDraft(
                    section_type=section["section_type"],
                    content=section["content_json"],
                    review_state="unreviewed",
                    reviewer_comment="reused section requires target-specific human review",
                    sources=sources,
                    conflicts=section["conflicts_json"],
                    unknowns=section["unknowns_json"],
                ),
                created_by=created_by,
            )
        return route_id

    def _insert_step(self, connection: sqlite3.Connection, route_id: int, step: RouteStepDraft, parent_step_id: int | None) -> int:
        now = utcnow()
        cursor = connection.execute(
            """INSERT INTO route_step(route_id,parent_step_id,sequence_no,step_code,title,action,why,work_image_slots,input_json,material_json,
                   tool_equipment_json,fixture_json,parameter_json,method_json,quality_check_json,acceptance_criteria_json,
                   safety_json,record_output_json,exception_json,unknowns_json,review_state,reviewer_comment,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (route_id, parent_step_id, step.sequence_no, step.step_code, step.title, step.action, step.why, step.work_image_slots,
             json.dumps(step.inputs, ensure_ascii=False), json.dumps(step.materials, ensure_ascii=False),
             json.dumps(step.tool_equipment, ensure_ascii=False), json.dumps(step.fixtures, ensure_ascii=False),
             json.dumps(step.parameters, ensure_ascii=False), json.dumps(step.method, ensure_ascii=False),
             json.dumps(step.quality_check, ensure_ascii=False), json.dumps(step.acceptance_criteria, ensure_ascii=False),
             json.dumps(step.safety, ensure_ascii=False), json.dumps(step.record_output, ensure_ascii=False),
             json.dumps(step.exception, ensure_ascii=False), json.dumps([item.model_dump(mode="json") for item in step.unknowns], ensure_ascii=False),
             step.review_state, step.reviewer_comment, now, now),
        )
        step_id = int(cursor.lastrowid)
        for field_name, refs in step.evidence.items():
            for ref in refs:
                evidence = connection.execute(
                    "SELECT id FROM evidence_source WHERE source_path=? AND page_or_sheet=? ORDER BY id DESC LIMIT 1",
                    (ref.source_path, ref.page_or_sheet),
                ).fetchone()
                connection.execute(
                    """INSERT INTO field_provenance(route_step_id,route_id,field_name,evidence_id,confidence,conflict_status,note)
                       VALUES(?,?,?,?,?,?,?)""",
                    (step_id, route_id, field_name, evidence[0] if evidence else None, ref.confidence, ref.conflict_status, ref.excerpt),
                )
        return step_id

    def get_route(self, route_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            route = connection.execute(
                """SELECT r.*,p.product_code,p.product_name,f.code AS process_family_code
                   FROM product_route r JOIN product p ON p.id=r.product_id JOIN process_family f ON f.id=r.process_family_id
                   WHERE r.id=?""", (route_id,)
            ).fetchone()
            if not route:
                raise KeyError(route_id)
            steps = [self._decode_step(row) for row in connection.execute(
                "SELECT * FROM active_route_step WHERE route_id=? ORDER BY sequence_no,id", (route_id,)
            )]
            provenance = [dict(row) for row in connection.execute(
                """SELECT fp.*,es.source_type,es.source_path,es.page_or_sheet,es.excerpt
                   FROM field_provenance fp LEFT JOIN evidence_source es ON es.id=fp.evidence_id
                   WHERE fp.route_id=?
                     AND (fp.route_step_id IS NULL OR fp.route_step_id IN (SELECT id FROM active_route_step))
                   ORDER BY fp.id""", (route_id,)
            )]
            reuse = [self._decode_json_columns(dict(row), ["match_basis_json", "field_map_json"]) for row in connection.execute(
                "SELECT * FROM reuse_link WHERE target_route_id=?", (route_id,)
            )]
            sections = [self._decode_section(row) for row in connection.execute(
                """SELECT s.* FROM route_section s
                   JOIN (SELECT section_type,MAX(version) AS version FROM route_section WHERE route_id=? GROUP BY section_type) latest
                     ON latest.section_type=s.section_type AND latest.version=s.version
                   WHERE s.route_id=? ORDER BY s.section_type""",
                (route_id, route_id),
            )]
            media = [dict(row) for row in connection.execute(
                """SELECT sm.id AS link_id,sm.route_step_id,sm.caption,sm.link_state,sm.confirmed_by,sm.confirmed_at,
                          ma.id AS asset_id,ma.original_name,ma.storage_path,ma.sha256,ma.mime_type,ma.source_note,ma.uploaded_by,ma.created_at
                   FROM step_media sm JOIN media_asset ma ON ma.id=sm.media_asset_id
                   JOIN active_route_step rs ON rs.id=sm.route_step_id WHERE rs.route_id=? ORDER BY rs.sequence_no,sm.id""",
                (route_id,),
            )]
            assets = [dict(row) for row in connection.execute(
                "SELECT * FROM media_asset WHERE route_id=? ORDER BY created_at,id", (route_id,)
            )]
            reference_files = [dict(row) for row in connection.execute(
                "SELECT * FROM route_reference_file WHERE route_id=? ORDER BY created_at,id", (route_id,)
            )]
            return {
                "route": dict(route),
                "steps": steps,
                "sections": sections,
                "provenance": provenance,
                "reuse_links": reuse,
                "media": media,
                "media_assets": assets,
                "reference_files": reference_files,
            }

    def list_products(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT p.id,p.product_code,p.product_name,f.code AS process_family_code,p.lifecycle_status,
                   (SELECT r.id FROM product_route r WHERE r.product_id=p.id ORDER BY r.version DESC LIMIT 1) AS latest_route_id
                   FROM product p LEFT JOIN process_family f ON f.id=p.process_family_id ORDER BY p.product_code"""
            )]

    def append_chat_message(
        self,
        route_id: int,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("unsupported chat role")
        if not content.strip():
            raise ValueError("chat content is required")
        with self.connect() as connection:
            if not connection.execute("SELECT 1 FROM product_route WHERE id=?", (route_id,)).fetchone():
                raise KeyError(route_id)
            cursor = connection.execute(
                "INSERT INTO sop_chat_message(route_id,role,content,metadata_json,created_at) VALUES(?,?,?,?,?)",
                (route_id, role, content.strip(), json.dumps(metadata or {}, ensure_ascii=False), utcnow()),
            )
            return int(cursor.lastrowid)

    def list_chat_messages(self, route_id: int, *, limit: int = 40) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self.connect() as connection:
            rows = list(connection.execute(
                """SELECT * FROM (
                       SELECT * FROM sop_chat_message WHERE route_id=? ORDER BY created_at DESC,id DESC LIMIT ?
                   ) ORDER BY created_at,id""",
                (route_id, safe_limit),
            ))
            return [self._decode_json_columns(dict(row), ["metadata_json"]) for row in rows]

    def update_step_field(self, step_id: int, field_name: str, value: Any, *, reviewer: str, decision: str = "confirmed", comment: str = "") -> None:
        column = JSON_FIELDS.get(field_name, field_name)
        if field_name not in EDITABLE_FIELDS:
            raise ValueError(f"field is not editable: {field_name}")
        encoded = json.dumps(value, ensure_ascii=False) if field_name in JSON_FIELDS else value
        with self.connect() as connection:
            row = connection.execute(
                "SELECT s.*,r.status AS route_status,r.id AS route_id FROM active_route_step s JOIN product_route r ON r.id=s.route_id WHERE s.id=?",
                (step_id,),
            ).fetchone()
            if not row:
                raise KeyError(step_id)
            if row["route_status"] == "approved":
                raise sqlite3.IntegrityError("approved route is immutable; create a new revision")
            old_value = row[column]
            connection.execute(f"UPDATE route_step SET {column}=?, updated_at=? WHERE id=?", (encoded, utcnow(), step_id))
            session_id = self._active_review_session(connection, int(row["route_id"]), reviewer)
            connection.execute(
                """INSERT INTO review_decision(review_session_id,entity_type,entity_id,field_name,decision,old_value_json,new_value_json,comment,decided_at)
                   VALUES(?,'field',?,?,?,?,?,?,?)""",
                (session_id, step_id, field_name, decision, json.dumps(old_value, ensure_ascii=False),
                 json.dumps(value, ensure_ascii=False), comment or f"field edited by {reviewer}", utcnow()),
            )

    def set_step_work_image_slots(self, step_id: int, slot_count: int, *, reviewer: str) -> dict[str, Any]:
        clean_reviewer = reviewer.strip()
        if not clean_reviewer:
            raise ValueError("worker identity is required")
        if isinstance(slot_count, bool) or not 1 <= int(slot_count) <= 6:
            raise ValueError("work image layout must contain 1 to 6 slots")
        requested = int(slot_count)
        with self.connect() as connection:
            row = connection.execute(
                """SELECT step.*,route.status AS route_status
                   FROM active_route_step AS step
                   JOIN product_route AS route ON route.id=step.route_id
                   WHERE step.id=?""",
                (step_id,),
            ).fetchone()
            if not row:
                raise KeyError(step_id)
            self._require_mutable_route(connection, int(row["route_id"]), route=row)
            confirmed_images = int(connection.execute(
                "SELECT COUNT(*) FROM step_media WHERE route_step_id=? AND link_state='confirmed'",
                (step_id,),
            ).fetchone()[0])
            if requested < confirmed_images:
                raise ValueError(
                    f"该工序已有 {confirmed_images} 张已确认图片；请先解除多余图片，或选择不少于 {confirmed_images} 格。"
                )
            previous = int(row["work_image_slots"] or 6)
            page_number = self._active_step_page(connection, int(row["route_id"]), step_id)
            if previous == requested:
                return {
                    "status": "unchanged",
                    "changed": False,
                    "route_id": int(row["route_id"]),
                    "step_id": step_id,
                    "step_title": str(row["title"]),
                    "work_image_slots": requested,
                    "affected_step_ids": [],
                    "page_number": page_number,
                }
            now = utcnow()
            connection.execute(
                """UPDATE route_step
                   SET work_image_slots=?,review_state='needs_revision',reviewer_comment=?,updated_at=?
                   WHERE id=?""",
                (requested, f"工图版式由 {previous} 格调整为 {requested} 格，待人工核对", now, step_id),
            )
            self._remove_step_knowledge(connection, [step_id])
            session_id = self._active_review_session(
                connection, int(row["route_id"]), clean_reviewer, "人工调整工图版式"
            )
            self._record_structure_decision(
                connection,
                session_id=session_id,
                step_id=step_id,
                field_name="work_image_slots",
                old_value=previous,
                new_value=requested,
                comment=f"指导书工图版式由 {previous} 格调整为 {requested} 格",
            )
            connection.execute(
                "UPDATE product_route SET updated_at=? WHERE id=?", (now, int(row["route_id"]))
            )
            return {
                "status": "layout_updated",
                "changed": True,
                "route_id": int(row["route_id"]),
                "step_id": step_id,
                "step_title": str(row["title"]),
                "work_image_slots": requested,
                "affected_step_ids": [step_id],
                "page_number": page_number,
            }

    def create_nl_proposal(
        self,
        route_id: int,
        instruction: str,
        parsed: dict[str, Any],
        *,
        parser_kind: str,
        requested_by: str,
    ) -> int:
        if parser_kind not in {"deterministic", "llm", "deterministic_fallback"}:
            raise ValueError("unsupported parser kind")
        if not requested_by.strip():
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            route = connection.execute("SELECT status FROM product_route WHERE id=?", (route_id,)).fetchone()
            if not route:
                raise KeyError(route_id)
            if route["status"] == "approved":
                raise sqlite3.IntegrityError("approved route is immutable; create a new revision")
            cursor = connection.execute(
                """INSERT INTO nl_change_proposal(route_id,instruction,parsed_json,parser_kind,status,requested_by,created_at)
                   VALUES(?,?,?,?,'preview',?,?)""",
                (route_id, instruction.strip(), json.dumps(parsed, ensure_ascii=False), parser_kind, requested_by.strip(), utcnow()),
            )
            return int(cursor.lastrowid)

    def apply_nl_proposal(self, proposal_id: int, *, reviewer: str) -> dict[str, Any]:
        if not reviewer.strip():
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            proposal = connection.execute("SELECT * FROM nl_change_proposal WHERE id=?", (proposal_id,)).fetchone()
            if not proposal:
                raise KeyError(proposal_id)
            if proposal["status"] != "preview":
                raise ValueError("proposal has already been handled")
            route_id = int(proposal["route_id"])
            route = connection.execute("SELECT status FROM product_route WHERE id=?", (route_id,)).fetchone()
            if not route or route["status"] == "approved":
                raise sqlite3.IntegrityError("approved route is immutable; create a new revision")
            parsed = json.loads(proposal["parsed_json"])
            session_id = self._active_review_session(connection, route_id, reviewer.strip(), "自然语言修改草稿")
            changed: list[dict[str, Any]] = []
            for change in parsed.get("changes", []):
                field_name = str(change.get("field_name", ""))
                if field_name not in EDITABLE_FIELDS or field_name in {"review_state", "sequence_no", "parent_step_id"}:
                    continue
                step_id = int(change["step_id"])
                column = JSON_FIELDS.get(field_name, field_name)
                row = connection.execute("SELECT * FROM active_route_step WHERE id=? AND route_id=?", (step_id, route_id)).fetchone()
                if not row:
                    continue
                value = change.get("value")
                encoded = json.dumps(value, ensure_ascii=False) if field_name in JSON_FIELDS else str(value)
                connection.execute(
                    f"UPDATE route_step SET {column}=?,review_state='needs_revision',reviewer_comment=?,updated_at=? WHERE id=?",
                    (encoded, "自然语言已填入草稿，待人工核对", utcnow(), step_id),
                )
                connection.execute(
                    """INSERT INTO review_decision(review_session_id,entity_type,entity_id,field_name,decision,old_value_json,new_value_json,comment,decided_at)
                       VALUES(?,'field',?,?, 'needs_revision',?,?,?,?)""",
                    (session_id, step_id, field_name, json.dumps(row[column], ensure_ascii=False), json.dumps(value, ensure_ascii=False), "自然语言提案应用到草稿；尚未人工确认", utcnow()),
                )
                changed.append({"step_id": step_id, "field_name": field_name})

            added: list[dict[str, Any]] = []
            next_sequence = float(connection.execute("SELECT COALESCE(MAX(sequence_no),0) FROM active_route_step WHERE route_id=?", (route_id,)).fetchone()[0])
            existing_codes = {row[0] for row in connection.execute("SELECT step_code FROM route_step WHERE route_id=?", (route_id,))}
            for raw in parsed.get("new_steps", []):
                insert_after = str(raw.get("after_step_ref", "")).strip()
                desired_sequence: float | None = None
                if insert_after:
                    after = connection.execute(
                        """SELECT id,sequence_no FROM active_route_step WHERE route_id=?
                           AND (CAST(id AS TEXT)=? OR lower(step_code)=lower(?) OR lower(title)=lower(?))
                           ORDER BY id LIMIT 1""",
                        (route_id, insert_after, insert_after, insert_after),
                    ).fetchone()
                    if after:
                        desired_sequence = float(after["sequence_no"]) + 0.5
                if desired_sequence is None:
                    next_sequence += 1.0
                    desired_sequence = next_sequence
                code_index = 1
                while f"NL-{code_index:03d}" in existing_codes:
                    code_index += 1
                code = f"NL-{code_index:03d}"
                existing_codes.add(code)
                title = str(raw.get("title", "")).strip()
                methods = [str(item).strip() for item in raw.get("method", []) if str(item).strip()]
                if not title:
                    continue
                if not methods:
                    methods = ["根据受控资料补充本工序的可执行作业步骤。"]
                step = RouteStepDraft(
                    step_code=code,
                    sequence_no=desired_sequence,
                    title=title,
                    action=str(raw.get("action") or f"执行“{title}”工序；具体动作待人工核对。"),
                    why=str(raw.get("why") or f"完成“{title}”对应的受控作业输出。"),
                    method=methods,
                    quality_check=raw.get("quality_check") or ["由人工补充本工序的检查方法。"],
                    acceptance_criteria=raw.get("acceptance_criteria") or ["由责任人依据受控规范补充合格判据。"],
                    safety=raw.get("safety") or [],
                    record_output=raw.get("record_output") or ["记录人工确认后的工序执行结果。"],
                    exception=raw.get("exception") or ["信息不完整或结果异常时停止流转并提交人工判定。"],
                    unknowns=[{
                        "field_name": "new_step_review",
                        "reason": "自然语言新增工序尚未完成受控资料与现场动作核对。",
                        "owner_role": "工艺工程师",
                        "required_evidence": "受控工艺文件、现场核对记录和责任人确认",
                        "blocking": True,
                    }],
                    review_state="needs_revision",
                    reviewer_comment="自然语言新增草稿，待人工逐项核对",
                )
                step_id = self._insert_step(connection, route_id, step, None)
                added.append({"step_id": step_id, "step_code": code, "title": title})

            if added:
                ordered = list(connection.execute(
                    "SELECT id FROM active_route_step WHERE route_id=? ORDER BY sequence_no,id", (route_id,)
                ))
                for index, row in enumerate(ordered, start=1):
                    connection.execute(
                        "UPDATE route_step SET sequence_no=?,updated_at=? WHERE id=?",
                        (float(index), utcnow(), int(row["id"])),
                    )

            section_changed: list[dict[str, Any]] = []
            for raw in parsed.get("section_changes", []):
                section_type = str(raw.get("section_type", "")).strip()
                patch = raw.get("patch")
                if section_type not in ROUTE_SECTION_TYPES or not isinstance(patch, dict) or not patch:
                    continue
                previous = connection.execute(
                    """SELECT * FROM route_section WHERE route_id=? AND section_type=?
                       ORDER BY version DESC,id DESC LIMIT 1""",
                    (route_id, section_type),
                ).fetchone()
                if not previous:
                    continue
                old_content = json.loads(previous["content_json"])
                content = merge_json_object(old_content, patch)
                next_version = int(previous["version"]) + 1
                now = utcnow()
                cursor = connection.execute(
                    """INSERT INTO route_section(
                           route_id,section_type,version,content_json,review_state,reviewer_comment,
                           source_json,conflicts_json,unknowns_json,created_by,created_at,updated_at
                       ) VALUES(?,?,?,?,'needs_revision',?,?,?,?,?,?,?)""",
                    (
                        route_id, section_type, next_version, json.dumps(content, ensure_ascii=False),
                        "自然语言已填入章节草稿，待人工核对", previous["source_json"],
                        previous["conflicts_json"], previous["unknowns_json"], reviewer.strip(), now, now,
                    ),
                )
                section_id = int(cursor.lastrowid)
                connection.execute(
                    """INSERT INTO review_decision(
                           review_session_id,entity_type,entity_id,field_name,decision,
                           old_value_json,new_value_json,comment,decided_at
                       ) VALUES(?,?,?,?, 'needs_revision',?,?,?,?)""",
                    (
                        session_id, SECTION_DECISION_ENTITY[section_type], section_id, section_type,
                        json.dumps(old_content, ensure_ascii=False), json.dumps(content, ensure_ascii=False),
                        str(raw.get("reason") or "自然语言章节修改；尚未人工确认"), now,
                    ),
                )
                section_changed.append({"section_id": section_id, "section_type": section_type, "version": next_version})

            linked: list[dict[str, Any]] = []
            unresolved_images: list[dict[str, Any]] = []
            refs = list(parsed.get("image_refs", []))
            for raw in parsed.get("new_steps", []):
                if raw.get("image_ref") and added:
                    matched_added = next((item for item in added if item["title"] == raw.get("title")), None)
                    if matched_added:
                        refs.append({"step_id": matched_added["step_id"], "reference": raw["image_ref"]})
            assets = [dict(row) for row in connection.execute("SELECT * FROM media_asset WHERE route_id=?", (route_id,))]
            for ref in refs:
                step_id = int(ref.get("step_id") or 0)
                token = Path(str(ref.get("reference", ""))).stem.lower()
                matches = [asset for asset in assets if token and token in Path(asset["original_name"]).stem.lower()]
                if len(matches) != 1 or not connection.execute("SELECT 1 FROM active_route_step WHERE id=? AND route_id=?", (step_id, route_id)).fetchone():
                    unresolved_images.append({"step_id": step_id, "reference": ref.get("reference", ""), "reason": "图片未唯一匹配"})
                    continue
                asset = matches[0]
                connection.execute(
                    """INSERT INTO step_media(route_step_id,media_asset_id,caption,link_state,created_at)
                       VALUES(?,?,?,'draft',?) ON CONFLICT(route_step_id,media_asset_id) DO UPDATE SET caption=excluded.caption,link_state='draft'""",
                    (step_id, asset["id"], str(ref.get("reference", "")), utcnow()),
                )
                linked.append({"step_id": step_id, "asset_id": asset["id"], "original_name": asset["original_name"]})
            connection.execute("UPDATE nl_change_proposal SET status='applied',applied_at=? WHERE id=?", (utcnow(), proposal_id))
            return {
                "route_id": route_id,
                "changed": changed,
                "added": added,
                "section_changed": section_changed,
                "linked_media": linked,
                "unresolved_images": unresolved_images,
                "status": "draft_needs_human_review",
            }

    def upload_media_asset(
        self,
        route_id: int,
        *,
        original_name: str,
        mime_type: str,
        data: bytes,
        uploaded_by: str,
        source_note: str = "",
    ) -> dict[str, Any]:
        if not uploaded_by.strip():
            raise ValueError("worker identity is required")
        allowed = {"image/png": ".png", "image/jpeg": ".jpg"}
        if mime_type not in allowed:
            raise ValueError("仅支持 PNG 或 JPEG 图片")
        if not data or len(data) > 10 * 1024 * 1024:
            raise ValueError("图片必须大于 0 且不超过 10MB")
        if mime_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("PNG 文件签名无效")
        if mime_type == "image/jpeg" and not data.startswith(b"\xff\xd8"):
            raise ValueError("JPEG 文件签名无效")
        with self.connect() as connection:
            route = connection.execute("SELECT status FROM product_route WHERE id=?", (route_id,)).fetchone()
            if not route:
                raise KeyError(route_id)
            if route["status"] == "approved":
                raise sqlite3.IntegrityError("approved route is immutable; create a new revision")
        digest = hashlib.sha256(data).hexdigest()
        media_root = self.path.parent / "sop_media"
        media_root.mkdir(parents=True, exist_ok=True)
        target = media_root / f"{digest}{allowed[mime_type]}"
        if not target.exists():
            target.write_bytes(data)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO media_asset(route_id,original_name,storage_path,sha256,mime_type,source_note,uploaded_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(route_id,sha256) DO UPDATE SET original_name=excluded.original_name,source_note=excluded.source_note""",
                (route_id, Path(original_name).name, str(target.resolve()), digest, mime_type, source_note.strip(), uploaded_by.strip(), utcnow()),
            )
            row = connection.execute("SELECT * FROM media_asset WHERE route_id=? AND sha256=?", (route_id, digest)).fetchone()
            return dict(row)

    def link_media_asset(self, step_id: int, asset_id: int, *, caption: str = "") -> int:
        with self.connect() as connection:
            pair = connection.execute(
                """SELECT s.route_id,r.status FROM active_route_step s JOIN product_route r ON r.id=s.route_id
                   JOIN media_asset ma ON ma.route_id=s.route_id WHERE s.id=? AND ma.id=?""",
                (step_id, asset_id),
            ).fetchone()
            if not pair:
                raise ValueError("图片与工序不属于同一路线")
            if pair["status"] == "approved":
                raise sqlite3.IntegrityError("approved route is immutable; create a new revision")
            connection.execute(
                """INSERT INTO step_media(route_step_id,media_asset_id,caption,link_state,created_at)
                   VALUES(?,?,?,'draft',?) ON CONFLICT(route_step_id,media_asset_id) DO UPDATE SET caption=excluded.caption,link_state='draft'""",
                (step_id, asset_id, caption.strip(), utcnow()),
            )
            return int(connection.execute("SELECT id FROM step_media WHERE route_step_id=? AND media_asset_id=?", (step_id, asset_id)).fetchone()[0])

    def replace_route_media_bindings(
        self,
        route_id: int,
        bindings: list[dict[str, Any]],
        *,
        reviewer: str,
    ) -> dict[str, Any]:
        """Atomically persist the route-wide image layout without promoting draft links."""
        if not reviewer.strip():
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            route = connection.execute("SELECT status FROM product_route WHERE id=?", (route_id,)).fetchone()
            if not route:
                raise KeyError(route_id)
            if route["status"] == "approved":
                raise sqlite3.IntegrityError("approved route is immutable; create a new revision")

            steps = {
                int(row["id"]): dict(row)
                for row in connection.execute(
                    "SELECT id,sequence_no FROM active_route_step WHERE route_id=? ORDER BY sequence_no,id",
                    (route_id,),
                )
            }
            assets = {
                int(row["id"])
                for row in connection.execute("SELECT id FROM media_asset WHERE route_id=?", (route_id,))
            }
            requested: dict[int, list[dict[str, Any]]] = {step_id: [] for step_id in steps}
            seen_pairs: set[tuple[int, int]] = set()
            for raw in bindings:
                step_id = int(raw.get("step_id") or 0)
                asset_id = int(raw.get("asset_id") or 0)
                if step_id not in steps:
                    raise ValueError("image binding references a step outside the route")
                if asset_id not in assets:
                    raise ValueError("image binding references an asset outside the route")
                if (step_id, asset_id) in seen_pairs:
                    raise ValueError("the same image cannot be bound to one step more than once")
                if len(requested[step_id]) >= 6:
                    raise ValueError("each step can contain at most 6 images")
                seen_pairs.add((step_id, asset_id))
                requested[step_id].append({
                    "asset_id": asset_id,
                    "caption": str(raw.get("caption") or "").strip(),
                })

            existing_rows = [dict(row) for row in connection.execute(
                """SELECT sm.* FROM step_media sm
                   JOIN active_route_step rs ON rs.id=sm.route_step_id
                   WHERE rs.route_id=? ORDER BY rs.sequence_no,sm.id""",
                (route_id,),
            )]
            existing_by_pair = {
                (int(row["route_step_id"]), int(row["media_asset_id"])): row
                for row in existing_rows
            }
            existing_layout = [
                (int(row["route_step_id"]), int(row["media_asset_id"]), str(row["caption"]))
                for row in existing_rows
            ]
            requested_layout = [
                (step_id, item["asset_id"], item["caption"])
                for step_id in steps
                for item in requested[step_id]
            ]
            if existing_layout == requested_layout:
                return {
                    "status": "unchanged",
                    "changed": False,
                    "route_id": route_id,
                    "affected_step_ids": [],
                    "link_count": len(existing_layout),
                }

            previous_pairs = set(existing_by_pair)
            requested_pairs = set(seen_pairs)
            affected_step_ids = sorted({
                step_id
                for step_id in steps
                if [item for item in existing_layout if item[0] == step_id]
                != [item for item in requested_layout if item[0] == step_id]
            })
            now = utcnow()
            connection.execute(
                "DELETE FROM step_media WHERE route_step_id IN (SELECT id FROM active_route_step WHERE route_id=?)",
                (route_id,),
            )
            for step_id in steps:
                for item in requested[step_id]:
                    prior = existing_by_pair.get((step_id, item["asset_id"]))
                    connection.execute(
                        """INSERT INTO step_media(route_step_id,media_asset_id,caption,link_state,confirmed_by,confirmed_at,created_at)
                           VALUES(?,?,?,?,?,?,?)""",
                        (
                            step_id,
                            item["asset_id"],
                            item["caption"],
                            prior["link_state"] if prior else "draft",
                            prior["confirmed_by"] if prior else None,
                            prior["confirmed_at"] if prior else None,
                            prior["created_at"] if prior else now,
                        ),
                    )
            if affected_step_ids:
                connection.executemany(
                    "UPDATE route_step SET review_state='needs_revision',updated_at=? WHERE id=?",
                    [(now, step_id) for step_id in affected_step_ids],
                )
            return {
                "status": "saved",
                "changed": True,
                "route_id": route_id,
                "affected_step_ids": affected_step_ids,
                "link_count": len(requested_layout),
                "added_links": len(requested_pairs - previous_pairs),
                "removed_links": len(previous_pairs - requested_pairs),
            }

    def confirm_media_link(self, link_id: int, *, reviewer: str) -> dict[str, Any]:
        if not reviewer.strip():
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            row = connection.execute(
                """SELECT sm.id,sm.link_state,sm.route_step_id,rs.route_id,rs.work_image_slots,r.status
                   FROM step_media sm
                   JOIN active_route_step rs ON rs.id=sm.route_step_id
                   JOIN product_route r ON r.id=rs.route_id
                   WHERE sm.id=?""",
                (link_id,),
            ).fetchone()
            if not row:
                raise KeyError(link_id)
            if row["status"] == "approved":
                raise sqlite3.IntegrityError("approved route is immutable; create a new revision")
            changed = row["link_state"] != "confirmed"
            if changed:
                confirmed_count = int(connection.execute(
                    "SELECT COUNT(*) FROM step_media WHERE route_step_id=? AND link_state='confirmed'",
                    (int(row["route_step_id"]),),
                ).fetchone()[0])
                if confirmed_count >= int(row["work_image_slots"] or 6):
                    raise ValueError(
                        f"当前指导书只有 {int(row['work_image_slots'] or 6)} 个工图位置；请先扩大版式再确认图片。"
                    )
                connection.execute(
                    "UPDATE step_media SET link_state='confirmed',confirmed_by=?,confirmed_at=? WHERE id=?",
                    (reviewer.strip(), utcnow(), link_id),
                )
            return {
                "status": "confirmed" if changed else "already_confirmed",
                "changed": changed,
                "route_id": int(row["route_id"]),
                "step_id": int(row["route_step_id"]),
                "link_id": link_id,
            }

    def get_media_asset(self, asset_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM media_asset WHERE id=?", (asset_id,)).fetchone()
            if not row:
                raise KeyError(asset_id)
            return dict(row)

    def delete_media_asset(self, asset_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT ma.*,r.status AS route_status
                   FROM media_asset ma JOIN product_route r ON r.id=ma.route_id
                   WHERE ma.id=?""",
                (asset_id,),
            ).fetchone()
            if not row:
                raise KeyError(asset_id)
            if row["route_status"] == "approved":
                raise sqlite3.IntegrityError("approved route media is immutable; create a revision")
            link_count = int(connection.execute(
                "SELECT COUNT(*) FROM step_media WHERE media_asset_id=?", (asset_id,)
            ).fetchone()[0])
            storage_path = Path(row["storage_path"])
            connection.execute("DELETE FROM media_asset WHERE id=?", (asset_id,))
            remaining_file_refs = int(connection.execute(
                "SELECT COUNT(*) FROM media_asset WHERE storage_path=?", (str(storage_path),)
            ).fetchone()[0])
        if remaining_file_refs == 0 and storage_path.is_file():
            storage_path.unlink()
        return {
            "status": "deleted",
            "asset_id": asset_id,
            "route_id": int(row["route_id"]),
            "removed_links": link_count,
        }

    def upload_route_reference_file(
        self,
        route_id: int,
        *,
        original_name: str,
        mime_type: str,
        data: bytes,
        uploaded_by: str,
        source_note: str = "",
    ) -> dict[str, Any]:
        if not uploaded_by.strip():
            raise ValueError("worker identity is required")
        suffix = self._validate_route_reference_file(original_name, mime_type, data)
        with self.connect() as connection:
            route = connection.execute("SELECT status FROM product_route WHERE id=?", (route_id,)).fetchone()
            if not route:
                raise KeyError(route_id)
            if route["status"] == "approved":
                raise sqlite3.IntegrityError("approved route reference files are immutable; create a revision")
        digest = hashlib.sha256(data).hexdigest()
        reference_root = self.path.parent / "sop_route_references"
        reference_root.mkdir(parents=True, exist_ok=True)
        target = reference_root / f"{digest}{suffix}"
        if not target.exists():
            target.write_bytes(data)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO route_reference_file(
                       route_id,original_name,storage_path,sha256,mime_type,size_bytes,source_note,
                       review_state,uploaded_by,created_at
                   ) VALUES(?,?,?,?,?,?,?,'needs_revision',?,?)
                   ON CONFLICT(route_id,sha256) DO NOTHING""",
                (
                    route_id,
                    Path(original_name).name,
                    str(target.resolve()),
                    digest,
                    mime_type,
                    len(data),
                    source_note.strip(),
                    uploaded_by.strip(),
                    utcnow(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM route_reference_file WHERE route_id=? AND sha256=?", (route_id, digest)
            ).fetchone()
            return dict(row)

    def get_route_reference_file(self, file_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM route_reference_file WHERE id=?", (file_id,)).fetchone()
            if not row:
                raise KeyError(file_id)
            return dict(row)

    def confirm_route_reference_file(self, file_id: int, *, reviewer: str) -> dict[str, Any]:
        if not reviewer.strip():
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            row = connection.execute(
                """SELECT rf.*,r.status AS route_status
                   FROM route_reference_file rf JOIN product_route r ON r.id=rf.route_id
                   WHERE rf.id=?""",
                (file_id,),
            ).fetchone()
            if not row:
                raise KeyError(file_id)
            if row["route_status"] == "approved":
                raise sqlite3.IntegrityError("approved route reference files are immutable; create a revision")
            changed = row["review_state"] != "confirmed"
            if changed:
                connection.execute(
                    """UPDATE route_reference_file
                       SET review_state='confirmed',confirmed_by=?,confirmed_at=? WHERE id=?""",
                    (reviewer.strip(), utcnow(), file_id),
                )
            return {
                "status": "confirmed" if changed else "already_confirmed",
                "changed": changed,
                "file_id": file_id,
                "route_id": int(row["route_id"]),
            }

    def delete_route_reference_file(self, file_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT rf.*,r.status AS route_status
                   FROM route_reference_file rf JOIN product_route r ON r.id=rf.route_id
                   WHERE rf.id=?""",
                (file_id,),
            ).fetchone()
            if not row:
                raise KeyError(file_id)
            if row["route_status"] == "approved":
                raise sqlite3.IntegrityError("approved route reference files are immutable; create a revision")
            storage_path = Path(row["storage_path"])
            connection.execute("DELETE FROM route_reference_file WHERE id=?", (file_id,))
            remaining_file_refs = int(connection.execute(
                "SELECT COUNT(*) FROM route_reference_file WHERE storage_path=?", (str(storage_path),)
            ).fetchone()[0])
        if remaining_file_refs == 0 and storage_path.is_file():
            storage_path.unlink()
        return {"status": "deleted", "file_id": file_id, "route_id": int(row["route_id"])}

    @staticmethod
    def _validate_route_reference_file(original_name: str, mime_type: str, data: bytes) -> str:
        file_type = ROUTE_REFERENCE_FILE_TYPES.get(mime_type)
        if not file_type:
            raise ValueError("路线资料仅支持 PDF、DOCX、XLSX、CSV、TXT、PNG 或 JPEG")
        suffix, signature = file_type
        supplied_suffix = Path(original_name).suffix.lower()
        allowed_suffixes = {suffix}
        if mime_type == "image/jpeg":
            allowed_suffixes.add(".jpeg")
        if supplied_suffix not in allowed_suffixes:
            raise ValueError("文件扩展名与资料类型不一致")
        if not data or len(data) > ROUTE_REFERENCE_FILE_MAX_BYTES:
            raise ValueError("路线资料必须大于 0 且不超过 20MB")
        if signature and not data.startswith(signature):
            raise ValueError("路线资料文件签名无效")
        return suffix

    def confirm_step(self, step_id: int, *, reviewer: str, comment: str = "") -> dict[str, Any]:
        if not reviewer.strip():
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            row = connection.execute(
                """SELECT s.*,r.id AS route_id,r.version AS route_version,r.status AS route_status,
                          p.product_code,f.code AS process_family_code
                   FROM active_route_step s JOIN product_route r ON r.id=s.route_id JOIN product p ON p.id=r.product_id
                   JOIN process_family f ON f.id=r.process_family_id WHERE s.id=?""",
                (step_id,),
            ).fetchone()
            if not row:
                raise KeyError(step_id)
            if row["route_status"] == "approved":
                raise sqlite3.IntegrityError("approved route is immutable; create a new revision")
            linked_images = int(connection.execute(
                "SELECT COUNT(*) FROM step_media WHERE route_step_id=?",
                (step_id,),
            ).fetchone()[0])
            if linked_images > int(row["work_image_slots"] or 6):
                raise ValueError(
                    f"当前工序绑定了 {linked_images} 张图片，但指导书只有 {int(row['work_image_slots'] or 6)} 个工图位置；请先扩大版式或解除多余图片。"
                )
            now = utcnow()
            connection.execute("UPDATE route_step SET review_state='confirmed',reviewer_comment=?,updated_at=? WHERE id=?", (comment.strip() or "人工已核对本工序", now, step_id))
            connection.execute("UPDATE step_media SET link_state='confirmed',confirmed_by=?,confirmed_at=? WHERE route_step_id=? AND link_state='draft'", (reviewer.strip(), now, step_id))
            session_id = self._active_review_session(connection, int(row["route_id"]), reviewer.strip())
            connection.execute(
                """INSERT INTO review_decision(review_session_id,entity_type,entity_id,field_name,decision,old_value_json,new_value_json,comment,decided_at)
                   VALUES(?,'step',?,'review_state','confirmed',?,'\"confirmed\"',?,?)""",
                (session_id, step_id, json.dumps(row["review_state"], ensure_ascii=False), comment.strip() or "人工确认整步", now),
            )
            decoded = self._decode_step(row)
            decoded["review_state"] = "confirmed"
            decoded["reviewer_comment"] = comment.strip() or "人工已核对本工序"
            searchable = " ".join([
                decoded["title"], decoded["action"], decoded["why"],
                *decoded["method_json"], *decoded["quality_check_json"], *decoded["acceptance_criteria_json"],
            ])
            snapshot_json = json.dumps(decoded, ensure_ascii=False, sort_keys=True)
            connection.execute(
                """INSERT INTO knowledge_fragment(route_step_id,route_id,route_version,product_code,process_family_code,step_code,title,searchable_text,snapshot_json,confirmed_by,confirmed_at,reuse_eligible)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,0)
                   ON CONFLICT(route_step_id) DO UPDATE SET title=excluded.title,searchable_text=excluded.searchable_text,snapshot_json=excluded.snapshot_json,confirmed_by=excluded.confirmed_by,confirmed_at=excluded.confirmed_at,reuse_eligible=0""",
                (step_id, row["route_id"], row["route_version"], row["product_code"], row["process_family_code"], row["step_code"], row["title"], searchable, snapshot_json, reviewer.strip(), now),
            )
            fragment_id = int(connection.execute("SELECT id FROM knowledge_fragment WHERE route_step_id=?", (step_id,)).fetchone()[0])
            connection.execute("DELETE FROM knowledge_fragment_fts WHERE fragment_id=?", (str(fragment_id),))
            connection.execute(
                "INSERT INTO knowledge_fragment_fts(fragment_id,product_code,process_family_code,title,searchable_text) VALUES(?,?,?,?,?)",
                (str(fragment_id), row["product_code"], row["process_family_code"], row["title"], searchable),
            )
            return {"step_id": step_id, "review_state": "confirmed", "knowledge_fragment_id": fragment_id, "media_confirmed": connection.execute("SELECT COUNT(*) FROM step_media WHERE route_step_id=? AND link_state='confirmed'", (step_id,)).fetchone()[0]}

    def search_confirmed_knowledge(self, query: str, *, route_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        text = query.strip()
        if not text:
            return []
        safe_limit = max(1, min(int(limit), 20))
        tokens = [item for item in re.split(r"\s+", text) if item][:5]
        clauses = ["(title LIKE ? OR searchable_text LIKE ? OR product_code LIKE ? OR step_code LIKE ?)"] * len(tokens)
        params: list[Any] = []
        for token in tokens:
            like = f"%{token}%"
            params.extend([like, like, like, like])
        scope_column = "'other_route' AS source_scope"
        scope_order = ""
        if route_id is not None:
            scope_column = "CASE WHEN route_id=? THEN 'current_route' ELSE 'other_route' END AS source_scope"
            scope_order = "CASE WHEN route_id=? THEN 1 ELSE 0 END,"
            params = [route_id, *params, route_id]
        params.append(safe_limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT id,route_step_id,route_id,route_version,product_code,process_family_code,step_code,title,
                           searchable_text,confirmed_by,confirmed_at,reuse_eligible,snapshot_json,{scope_column}
                    FROM knowledge_fragment
                    WHERE route_step_id IN (SELECT id FROM active_route_step)
                      AND {' AND '.join(clauses)}
                    ORDER BY {scope_order} reuse_eligible DESC,confirmed_at DESC LIMIT ?""",
                params,
            )
            return [self._decode_json_columns(dict(row), ["snapshot_json"]) for row in rows]

    def add_step(self, route_id: int, step: RouteStepDraft) -> int:
        with self.connect() as connection:
            route = connection.execute("SELECT status FROM product_route WHERE id=?", (route_id,)).fetchone()
            if not route:
                raise KeyError(route_id)
            if route[0] == "approved":
                raise sqlite3.IntegrityError("approved route is immutable; create a new revision")
            parent_id = None
            if step.parent_step_code:
                parent = connection.execute("SELECT id FROM active_route_step WHERE route_id=? AND step_code=?", (route_id, step.parent_step_code)).fetchone()
                if not parent:
                    raise ValueError("parent step not found")
                parent_id = int(parent[0])
            return self._insert_step(connection, route_id, step, parent_id)

    def add_reviewable_step(
        self,
        route_id: int,
        *,
        title: str,
        reviewer: str,
        before_step_id: int | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        clean_title = title.strip()
        clean_reviewer = reviewer.strip()
        if not clean_title:
            raise ValueError("step title is required")
        if not clean_reviewer:
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            self._require_mutable_route(connection, route_id)
            ordered_ids = [
                int(row[0]) for row in connection.execute(
                    "SELECT id FROM active_route_step WHERE route_id=? ORDER BY sequence_no,id",
                    (route_id,),
                )
            ]
            if before_step_id is not None and before_step_id not in ordered_ids:
                raise ValueError("insert position is not an active step in this route")
            draft = self._reviewable_step_draft(
                connection,
                route_id,
                title=clean_title,
                sequence_no=float(len(ordered_ids) + 1),
                note=note,
            )
            step_id = self._insert_step(connection, route_id, draft, None)
            insert_at = ordered_ids.index(before_step_id) if before_step_id is not None else len(ordered_ids)
            ordered_ids.insert(insert_at, step_id)
            self._write_step_order(connection, route_id, ordered_ids)
            session_id = self._active_review_session(connection, route_id, clean_reviewer, "人工编辑工艺路线")
            self._record_structure_decision(
                connection,
                session_id=session_id,
                step_id=step_id,
                field_name="route_insert",
                old_value=None,
                new_value={"title": clean_title, "position": insert_at + 1},
                comment="新增工序已写入草稿，全部内容待人工核对",
            )
            connection.execute("UPDATE product_route SET updated_at=? WHERE id=?", (utcnow(), route_id))
            return {
                "status": "added",
                "changed": True,
                "route_id": route_id,
                "step_id": step_id,
                "step_title": clean_title,
                "affected_step_ids": [step_id],
                "page_number": insert_at + 2,
            }

    def split_step_actions(self, step_id: int, *, titles: list[str], reviewer: str) -> dict[str, Any]:
        clean_titles = [item.strip() for item in titles if item.strip()]
        clean_reviewer = reviewer.strip()
        if len(clean_titles) < 2:
            raise ValueError("split requires at least two concrete substeps")
        if len(clean_titles) > 20:
            raise ValueError("a single operation cannot create more than 20 substeps")
        if not clean_reviewer:
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            row = connection.execute(
                """SELECT step.*,route.status AS route_status
                   FROM active_route_step AS step
                   JOIN product_route AS route ON route.id=step.route_id
                   WHERE step.id=?""",
                (step_id,),
            ).fetchone()
            if not row:
                raise KeyError(step_id)
            self._require_mutable_route(connection, int(row["route_id"]), route=row)
            old_methods = json.loads(row["method_json"])
            now = utcnow()
            connection.execute(
                """UPDATE route_step
                   SET method_json=?,review_state='needs_revision',reviewer_comment=?,updated_at=?
                   WHERE id=?""",
                (
                    json.dumps(clean_titles, ensure_ascii=False),
                    "作业动作已拆分，待人工逐项核对",
                    now,
                    step_id,
                ),
            )
            self._remove_step_knowledge(connection, [step_id])
            session_id = self._active_review_session(
                connection, int(row["route_id"]), clean_reviewer, "人工拆分作业动作"
            )
            self._record_structure_decision(
                connection,
                session_id=session_id,
                step_id=step_id,
                field_name="method",
                old_value=old_methods,
                new_value=clean_titles,
                comment="拆分为同一张指导书内的作业动作，路线页数不变",
            )
            connection.execute(
                "UPDATE product_route SET updated_at=? WHERE id=?", (now, int(row["route_id"]))
            )
            page_number = self._active_step_page(connection, int(row["route_id"]), step_id)
            return {
                "status": "split_actions",
                "changed": old_methods != clean_titles,
                "route_id": int(row["route_id"]),
                "step_id": step_id,
                "step_title": row["title"],
                "affected_step_ids": [step_id],
                "page_number": page_number,
                "substep_count": len(clean_titles),
            }

    def split_step_independent(
        self,
        step_id: int,
        *,
        titles: list[str],
        reviewer: str,
    ) -> dict[str, Any]:
        clean_titles = [item.strip() for item in titles if item.strip()]
        clean_reviewer = reviewer.strip()
        if len(clean_titles) < 2:
            raise ValueError("split requires at least two independent step titles")
        if len(clean_titles) > 12:
            raise ValueError("a single operation cannot create more than 12 independent steps")
        if not clean_reviewer:
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            row = connection.execute(
                """SELECT step.*,route.status AS route_status
                   FROM active_route_step AS step
                   JOIN product_route AS route ON route.id=step.route_id
                   WHERE step.id=?""",
                (step_id,),
            ).fetchone()
            if not row:
                raise KeyError(step_id)
            route_id = int(row["route_id"])
            self._require_mutable_route(connection, route_id, route=row)
            ordered_ids = [
                int(item[0]) for item in connection.execute(
                    "SELECT id FROM active_route_step WHERE route_id=? ORDER BY sequence_no,id",
                    (route_id,),
                )
            ]
            subtree_ids = {
                int(item[0]) for item in connection.execute(
                    """WITH RECURSIVE subtree(id) AS (
                           SELECT id FROM active_route_step WHERE id=?
                           UNION ALL
                           SELECT child.id FROM active_route_step AS child
                           JOIN subtree AS parent ON child.parent_step_id=parent.id
                       ) SELECT id FROM subtree""",
                    (step_id,),
                )
            }
            insert_at = max(ordered_ids.index(item) for item in subtree_ids) + 1
            old_title = str(row["title"])
            now = utcnow()
            connection.execute(
                """UPDATE route_step
                   SET title=?,review_state='needs_revision',reviewer_comment=?,updated_at=?
                   WHERE id=?""",
                (clean_titles[0], "工序已拆分，原有内容仅保留在第一道工序并待人工重新分配", now, step_id),
            )
            added_ids: list[int] = []
            for title in clean_titles[1:]:
                draft = self._reviewable_step_draft(
                    connection,
                    route_id,
                    title=title,
                    sequence_no=float(len(ordered_ids) + len(added_ids) + 1),
                    note="",
                )
                added_ids.append(self._insert_step(connection, route_id, draft, None))
            ordered_ids[insert_at:insert_at] = added_ids
            self._write_step_order(connection, route_id, ordered_ids)
            affected_ids = [step_id, *added_ids]
            self._remove_step_knowledge(connection, affected_ids)
            session_id = self._active_review_session(connection, route_id, clean_reviewer, "人工拆分独立工序")
            self._record_structure_decision(
                connection,
                session_id=session_id,
                step_id=step_id,
                field_name="route_split",
                old_value={"title": old_title, "step_id": step_id},
                new_value={"titles": clean_titles, "step_ids": affected_ids},
                comment="原工序拆分为多道独立工序；字段内容需要人工重新分配和核对",
            )
            connection.execute("UPDATE product_route SET updated_at=? WHERE id=?", (now, route_id))
            page_numbers = [self._active_step_page(connection, route_id, item) for item in affected_ids]
            return {
                "status": "split_independent",
                "changed": True,
                "route_id": route_id,
                "step_id": step_id,
                "step_title": clean_titles[0],
                "added_step_ids": added_ids,
                "affected_step_ids": affected_ids,
                "page_number": page_numbers[0],
                "page_numbers": page_numbers,
                "warnings": ["原工序的既有字段只保留在拆分后的第一道工序，其他工序内容需要人工补充。"],
            }

    def delete_step(self, step_id: int, *, deleted_by: str = "web_reviewer") -> dict[str, Any]:
        if not deleted_by.strip():
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            row = connection.execute(
                """SELECT step.route_id,step.step_code,step.title,route.status AS route_status
                   FROM active_route_step AS step
                   JOIN product_route AS route ON route.id=step.route_id
                   WHERE step.id=?""",
                (step_id,),
            ).fetchone()
            if not row:
                raise KeyError(step_id)
            if row["route_status"] == "approved":
                raise sqlite3.IntegrityError("approved route is immutable; create a new revision")
            affected = list(connection.execute(
                """WITH RECURSIVE affected(id) AS (
                       SELECT id FROM active_route_step WHERE id=?
                       UNION ALL
                       SELECT child.id FROM active_route_step AS child
                       JOIN affected AS parent ON child.parent_step_id=parent.id
                   )
                   SELECT step.id,step.sequence_no FROM active_route_step AS step
                   JOIN affected ON affected.id=step.id ORDER BY step.sequence_no,step.id""",
                (step_id,),
            ))
            now = datetime.now(timezone.utc)
            deadline = now + timedelta(hours=24)
            token = secrets.token_urlsafe(24)
            cursor = connection.execute(
                """INSERT INTO step_deletion(
                       deletion_token,route_id,root_step_id,root_step_code,root_step_title,
                       deleted_by,deleted_at,restore_deadline
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    token, row["route_id"], step_id, row["step_code"], row["title"],
                    deleted_by.strip(), now.isoformat(), deadline.isoformat(),
                ),
            )
            deletion_id = int(cursor.lastrowid)
            connection.executemany(
                """INSERT INTO step_deletion_item(deletion_id,route_step_id,original_sequence_no)
                   VALUES(?,?,?)""",
                [(deletion_id, int(item["id"]), float(item["sequence_no"])) for item in affected],
            )
            return {
                "status": "deleted",
                "route_id": int(row["route_id"]),
                "step_id": step_id,
                "step_code": row["step_code"],
                "step_title": row["title"],
                "affected_step_count": len(affected),
                "deletion_token": token,
                "restore_deadline": deadline.isoformat(),
            }

    def list_recent_step_deletions(self, route_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        now = utcnow()
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT deletion.id,deletion.deletion_token,deletion.route_id,
                          deletion.root_step_id AS step_id,deletion.root_step_code AS step_code,
                          deletion.root_step_title AS step_title,deletion.deleted_by,
                          deletion.deleted_at,deletion.restore_deadline,
                          COUNT(item.route_step_id) AS affected_step_count
                   FROM step_deletion AS deletion
                   JOIN step_deletion_item AS item ON item.deletion_id=deletion.id
                   WHERE deletion.route_id=? AND deletion.restored_at IS NULL
                     AND deletion.restore_deadline>=?
                   GROUP BY deletion.id
                   ORDER BY deletion.deleted_at DESC,deletion.id DESC LIMIT ?""",
                (route_id, now, safe_limit),
            )]

    def restore_step_deletion(self, deletion_token: str, *, reviewer: str) -> dict[str, Any]:
        if not deletion_token.strip():
            raise ValueError("deletion token is required")
        if not reviewer.strip():
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            deletion = connection.execute(
                """SELECT deletion.*,route.status AS route_status
                   FROM step_deletion AS deletion
                   JOIN product_route AS route ON route.id=deletion.route_id
                   WHERE deletion.deletion_token=?""",
                (deletion_token.strip(),),
            ).fetchone()
            if not deletion:
                raise KeyError("step deletion not found")
            affected_count = int(connection.execute(
                "SELECT COUNT(*) FROM step_deletion_item WHERE deletion_id=?",
                (deletion["id"],),
            ).fetchone()[0])
            if deletion["restored_at"]:
                return {
                    "status": "already_restored",
                    "route_id": int(deletion["route_id"]),
                    "step_id": int(deletion["root_step_id"]),
                    "restored_step_count": affected_count,
                }
            if deletion["route_status"] == "approved":
                raise sqlite3.IntegrityError("approved route is immutable; create a new revision")
            if datetime.fromisoformat(deletion["restore_deadline"]) < datetime.now(timezone.utc):
                raise ValueError("undo period has expired")
            restored_at = utcnow()
            connection.execute(
                "UPDATE step_deletion SET restored_at=?,restored_by=? WHERE id=?",
                (restored_at, reviewer.strip(), deletion["id"]),
            )
            return {
                "status": "restored",
                "route_id": int(deletion["route_id"]),
                "step_id": int(deletion["root_step_id"]),
                "step_code": deletion["root_step_code"],
                "step_title": deletion["root_step_title"],
                "restored_step_count": affected_count,
                "restored_at": restored_at,
            }

    def reorder_steps(
        self,
        route_id: int,
        ordered_step_ids: list[int],
        *,
        reviewer: str = "web_reviewer",
    ) -> dict[str, Any]:
        if not reviewer.strip():
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            self._require_mutable_route(connection, route_id)
            rows = list(connection.execute(
                "SELECT id,parent_step_id FROM active_route_step WHERE route_id=? ORDER BY sequence_no,id",
                (route_id,),
            ))
            original_ids = [int(row["id"]) for row in rows]
            actual = set(original_ids)
            if len(ordered_step_ids) != len(actual) or set(ordered_step_ids) != actual:
                raise ValueError("reorder list must contain every route step exactly once")
            parent_by_id = {int(row["id"]): row["parent_step_id"] for row in rows}
            active_parent: int | None = None
            for item in ordered_step_ids:
                parent_id = parent_by_id[item]
                if parent_id is None:
                    active_parent = item
                elif int(parent_id) != active_parent:
                    raise ValueError("child steps must stay directly below their parent during reorder")
            changed_ids = [
                step_id for index, step_id in enumerate(ordered_step_ids)
                if original_ids[index] != step_id
            ]
            if not changed_ids:
                return {
                    "status": "unchanged",
                    "changed": False,
                    "route_id": route_id,
                    "affected_step_ids": [],
                    "page_number": 1,
                }
            now = utcnow()
            self._write_step_order(connection, route_id, ordered_step_ids, updated_at=now)
            connection.executemany(
                """UPDATE route_step SET review_state='needs_revision',reviewer_comment=?,updated_at=?
                   WHERE id=?""",
                [("工序顺序已调整，待人工核对流程与上下游关系", now, item) for item in changed_ids],
            )
            self._remove_step_knowledge(connection, changed_ids)
            session_id = self._active_review_session(connection, route_id, reviewer.strip(), "人工调整工序顺序")
            self._record_structure_decision(
                connection,
                session_id=session_id,
                step_id=changed_ids[0],
                field_name="route_order",
                old_value=original_ids,
                new_value=ordered_step_ids,
                comment="工序顺序已调整，流程图和指导书必须同步重排",
            )
            connection.execute("UPDATE product_route SET updated_at=? WHERE id=?", (now, route_id))
            return {
                "status": "reordered",
                "changed": True,
                "route_id": route_id,
                "affected_step_ids": changed_ids,
                "page_number": min(ordered_step_ids.index(item) for item in changed_ids) + 2,
            }

    def merge_steps(
        self,
        route_id: int,
        target_step_id: int,
        source_step_ids: list[int],
        *,
        reviewer: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        if target_step_id in source_step_ids:
            source_step_ids = [item for item in source_step_ids if item != target_step_id]
        if len(source_step_ids) != 1:
            raise ValueError("merge requires exactly one adjacent source step")
        if not reviewer.strip():
            raise ValueError("worker identity is required")
        with self.connect() as connection:
            self._require_mutable_route(connection, route_id)
            target = connection.execute("SELECT * FROM active_route_step WHERE id=? AND route_id=?", (target_step_id, route_id)).fetchone()
            source = connection.execute(
                "SELECT * FROM active_route_step WHERE id=? AND route_id=?",
                (source_step_ids[0], route_id),
            ).fetchone()
            if not target or not source:
                raise KeyError("merge step not found")
            if target["parent_step_id"] != source["parent_step_id"]:
                raise ValueError("only steps at the same hierarchy level can be merged")
            child_count = int(connection.execute(
                "SELECT COUNT(*) FROM active_route_step WHERE parent_step_id IN (?,?)",
                (target_step_id, int(source["id"])),
            ).fetchone()[0])
            if child_count:
                raise ValueError("steps with child processes must be resolved before merge")
            sibling_rows = list(connection.execute(
                """SELECT id FROM active_route_step WHERE route_id=?
                   AND ((parent_step_id IS NULL AND ? IS NULL) OR parent_step_id=?)
                   ORDER BY sequence_no,id""",
                (route_id, target["parent_step_id"], target["parent_step_id"]),
            ))
            sibling_ids = [int(item[0]) for item in sibling_rows]
            if abs(sibling_ids.index(target_step_id) - sibling_ids.index(int(source["id"]))) != 1:
                raise ValueError("only adjacent steps can be merged")
            old_payload = {
                "target": self._decode_step(target),
                "source": self._decode_step(source),
            }
            merged_title = (title or str(target["title"])).strip()
            if not merged_title:
                raise ValueError("merged step title is required")
            scalar_values = {
                "title": merged_title,
                "action": self._merge_text_values(str(target["action"]), str(source["action"])),
                "why": self._merge_text_values(str(target["why"]), str(source["why"])),
            }
            json_values = {
                column: self._merge_json_lists(json.loads(target[column]), json.loads(source[column]))
                for column in JSON_FIELDS.values()
            }
            merged_media_ids = {
                int(item[0]) for item in connection.execute(
                    "SELECT media_asset_id FROM step_media WHERE route_step_id IN (?,?)",
                    (target_step_id, int(source["id"])),
                )
            }
            if len(merged_media_ids) > 6:
                raise ValueError("合并后图片数量超过 6 张，请先解除多余图片关联")
            merged_image_slots = max(
                int(target["work_image_slots"] or 6),
                int(source["work_image_slots"] or 6),
                len(merged_media_ids),
            )
            now = utcnow()
            connection.execute(
                """UPDATE route_step SET title=?,action=?,why=?,work_image_slots=?,input_json=?,material_json=?,tool_equipment_json=?,
                       fixture_json=?,parameter_json=?,method_json=?,quality_check_json=?,acceptance_criteria_json=?,
                       safety_json=?,record_output_json=?,exception_json=?,unknowns_json=?,review_state='needs_revision',
                       reviewer_comment=?,updated_at=? WHERE id=?""",
                (
                    scalar_values["title"], scalar_values["action"], scalar_values["why"], merged_image_slots,
                    *(json.dumps(json_values[column], ensure_ascii=False) for column in JSON_FIELDS.values()),
                    "相邻工序已合并，字段冲突和上下游关系待人工核对", now, target_step_id,
                ),
            )
            for media in connection.execute("SELECT * FROM step_media WHERE route_step_id=?", (int(source["id"]),)):
                connection.execute(
                    """INSERT OR IGNORE INTO step_media(
                           route_step_id,media_asset_id,caption,link_state,confirmed_by,confirmed_at,created_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        target_step_id, media["media_asset_id"], media["caption"], media["link_state"],
                        media["confirmed_by"], media["confirmed_at"], media["created_at"],
                    ),
                )
            for provenance in connection.execute("SELECT * FROM field_provenance WHERE route_step_id=?", (int(source["id"]),)):
                connection.execute(
                    """INSERT INTO field_provenance(
                           route_step_id,route_id,field_name,evidence_id,source_route_id,source_route_version,
                           source_step_code,source_field_name,confidence,conflict_status,note
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        target_step_id, route_id, provenance["field_name"], provenance["evidence_id"],
                        provenance["source_route_id"], provenance["source_route_version"],
                        provenance["source_step_code"] or source["step_code"], provenance["source_field_name"],
                        provenance["confidence"], provenance["conflict_status"],
                        f"合并自 {source['step_code']}；{provenance['note']}",
                    ),
                )
            self._remove_step_knowledge(connection, [target_step_id, int(source["id"])])
            connection.execute("DELETE FROM route_step WHERE id=?", (int(source["id"]),))
            ordered_ids = [
                int(item[0]) for item in connection.execute(
                    "SELECT id FROM active_route_step WHERE route_id=? ORDER BY sequence_no,id",
                    (route_id,),
                )
            ]
            self._write_step_order(connection, route_id, ordered_ids, updated_at=now)
            session_id = self._active_review_session(connection, route_id, reviewer.strip(), "人工合并相邻工序")
            merged_row = connection.execute("SELECT * FROM active_route_step WHERE id=?", (target_step_id,)).fetchone()
            self._record_structure_decision(
                connection,
                session_id=session_id,
                step_id=target_step_id,
                field_name="route_merge",
                old_value=old_payload,
                new_value=self._decode_step(merged_row),
                comment=f"{source['step_code']} 已合并到 {target['step_code']}，全部字段待人工核对",
            )
            connection.execute("UPDATE product_route SET updated_at=? WHERE id=?", (now, route_id))
            return {
                "status": "merged",
                "changed": True,
                "route_id": route_id,
                "step_id": target_step_id,
                "step_title": merged_title,
                "removed_step_id": int(source["id"]),
                "removed_step_title": source["title"],
                "affected_step_ids": [target_step_id],
                "page_number": self._active_step_page(connection, route_id, target_step_id),
                "warnings": ["合并结果包含两道工序的全部字段，冲突内容必须人工逐项核对。"],
            }

    @staticmethod
    def _require_mutable_route(
        connection: sqlite3.Connection,
        route_id: int,
        *,
        route: sqlite3.Row | None = None,
    ) -> None:
        status = route["route_status"] if route is not None and "route_status" in route.keys() else None
        if status is None:
            row = connection.execute("SELECT status FROM product_route WHERE id=?", (route_id,)).fetchone()
            if not row:
                raise KeyError(route_id)
            status = row["status"]
        if status == "approved":
            raise sqlite3.IntegrityError("approved route is immutable; create a new revision")

    def _reviewable_step_draft(
        self,
        connection: sqlite3.Connection,
        route_id: int,
        *,
        title: str,
        sequence_no: float,
        note: str,
    ) -> RouteStepDraft:
        clean_note = note.strip()
        return RouteStepDraft(
            step_code=self._next_web_step_code(connection, route_id),
            sequence_no=sequence_no,
            title=title,
            action=clean_note or f"执行“{title}”工序；具体动作尚未由人工提供。",
            why=f"“{title}”的工序目的需要工艺工程师依据受控文件核对。",
            method=[clean_note] if clean_note else ["本工序的可执行作业步骤尚未由人工提供。"],
            quality_check=["本工序检查方法尚未由人工提供。"],
            acceptance_criteria=["本工序合格判据尚未由责任人依据受控规范提供。"],
            record_output=["本工序记录要求尚未由人工提供。"],
            exception=["信息不完整或结果异常时停止流转并提交人工判定。"],
            unknowns=[{
                "field_name": "route_structure_review",
                "reason": "人工新增工序尚未完成受控资料、现场动作和质量要求核对。",
                "owner_role": "工艺工程师",
                "required_evidence": "受控工艺文件、现场核对记录和责任人确认",
                "blocking": True,
            }],
            review_state="needs_revision",
            reviewer_comment="人工新增路线草稿，待逐项核对",
        )

    @staticmethod
    def _next_web_step_code(connection: sqlite3.Connection, route_id: int) -> str:
        existing = {
            str(row[0]) for row in connection.execute(
                "SELECT step_code FROM route_step WHERE route_id=?", (route_id,)
            )
        }
        index = 1
        while f"WEB-{index:03d}" in existing:
            index += 1
        return f"WEB-{index:03d}"

    @staticmethod
    def _write_step_order(
        connection: sqlite3.Connection,
        route_id: int,
        ordered_step_ids: list[int],
        *,
        updated_at: str | None = None,
    ) -> None:
        moment = updated_at or utcnow()
        connection.executemany(
            "UPDATE route_step SET sequence_no=?,updated_at=? WHERE id=? AND route_id=?",
            [(float(index), moment, step_id, route_id) for index, step_id in enumerate(ordered_step_ids, start=1)],
        )

    @staticmethod
    def _active_step_page(connection: sqlite3.Connection, route_id: int, step_id: int) -> int:
        ids = [
            int(row[0]) for row in connection.execute(
                "SELECT id FROM active_route_step WHERE route_id=? ORDER BY sequence_no,id", (route_id,)
            )
        ]
        if step_id not in ids:
            raise KeyError(step_id)
        return ids.index(step_id) + 2

    @staticmethod
    def _merge_text_values(first: str, second: str) -> str:
        values = [item.strip() for item in (first, second) if item.strip()]
        return "；".join(dict.fromkeys(values))

    @staticmethod
    def _merge_json_lists(first: list[Any], second: list[Any]) -> list[Any]:
        merged: list[Any] = []
        seen: set[str] = set()
        for item in [*first, *second]:
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(item)
        return merged

    @staticmethod
    def _remove_step_knowledge(connection: sqlite3.Connection, step_ids: list[int]) -> None:
        for step_id in step_ids:
            row = connection.execute(
                "SELECT id FROM knowledge_fragment WHERE route_step_id=?", (step_id,)
            ).fetchone()
            if not row:
                continue
            connection.execute("DELETE FROM knowledge_fragment_fts WHERE fragment_id=?", (str(row["id"]),))
            connection.execute("DELETE FROM knowledge_fragment WHERE id=?", (row["id"],))

    @staticmethod
    def _record_structure_decision(
        connection: sqlite3.Connection,
        *,
        session_id: int,
        step_id: int,
        field_name: str,
        old_value: Any,
        new_value: Any,
        comment: str,
    ) -> None:
        connection.execute(
            """INSERT INTO review_decision(
                   review_session_id,entity_type,entity_id,field_name,decision,
                   old_value_json,new_value_json,comment,decided_at
               ) VALUES(?,'field',?,?, 'needs_revision',?,?,?,?)""",
            (
                session_id,
                step_id,
                field_name,
                json.dumps(old_value, ensure_ascii=False),
                json.dumps(new_value, ensure_ascii=False),
                comment,
                utcnow(),
            ),
        )

    def create_review_session(self, route_id: int, reviewer: str, comment: str = "") -> int:
        if not reviewer.strip():
            raise ValueError("reviewer identity is required")
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM review_session WHERE route_id=? AND status IN ('draft','under_review') ORDER BY id DESC LIMIT 1",
                (route_id,),
            ).fetchone()
            if existing:
                return int(existing[0])
            return self._active_review_session(connection, route_id, reviewer, comment)

    def submit_review(self, session_id: int) -> None:
        with self.connect() as connection:
            row = connection.execute("SELECT route_id,status FROM review_session WHERE id=?", (session_id,)).fetchone()
            if not row or row["status"] != "draft":
                raise ValueError("review session is not a draft")
            connection.execute("UPDATE review_session SET status='under_review',submitted_at=? WHERE id=?", (utcnow(), session_id))
            connection.execute("UPDATE product_route SET status='under_review',updated_at=? WHERE id=?", (utcnow(), row["route_id"]))

    def approve(
        self,
        session_id: int,
        *,
        approved_by: str,
        approval_scope: str,
        confirmation_token: str | None = None,
    ) -> int:
        if approval_scope not in {"formal_production", "demonstration_only"}:
            raise ValueError("approval_scope must be explicit")
        if not approved_by.strip():
            raise ValueError("approved_by identity is required")
        with self.connect() as connection:
            session = connection.execute("SELECT * FROM review_session WHERE id=?", (session_id,)).fetchone()
            if not session or session["status"] != "under_review":
                raise ValueError("review must be submitted before approval")
            route_id = int(session["route_id"])
            if approval_scope == "formal_production":
                gate_errors = self._formal_approval_gate_errors(connection, route_id, confirmation_token)
                if gate_errors:
                    raise ValueError("formal approval gate failed: " + json.dumps(gate_errors, ensure_ascii=False))
            snapshot = self._snapshot_with_connection(connection, route_id)
            snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
            now = utcnow()
            connection.execute(
                "UPDATE product_route SET status='approved',approval_scope=?,updated_at=? WHERE id=?",
                (approval_scope, now, route_id),
            )
            connection.execute(
                "UPDATE review_session SET status='approved',closed_at=? WHERE id=?", (now, session_id)
            )
            connection.execute(
                """INSERT INTO approval_snapshot(route_id,route_version,approval_scope,snapshot_json,snapshot_sha256,approved_by,approved_at)
                   SELECT id,version,?,?,?,?,? FROM product_route WHERE id=?""",
                (approval_scope, snapshot_json, digest, approved_by, now, route_id),
            )
            route_text = " ".join(step["title"] + " " + step["action"] + " " + step["why"] for step in snapshot["steps"])
            connection.execute("DELETE FROM route_fts WHERE route_id=?", (str(route_id),))
            connection.execute(
                "INSERT INTO route_fts(route_id,product_code,route_name,route_text) VALUES(?,?,?,?)",
                (str(route_id), snapshot["route"]["product_code"], snapshot["route"]["route_name"], route_text),
            )
            if approval_scope == "formal_production":
                connection.execute("UPDATE knowledge_fragment SET reuse_eligible=1 WHERE route_id=?", (route_id,))
            return route_id

    def reject(self, session_id: int, *, reviewer: str, comment: str) -> None:
        with self.connect() as connection:
            session = connection.execute("SELECT route_id,status FROM review_session WHERE id=?", (session_id,)).fetchone()
            if not session or session["status"] not in {"draft", "under_review"}:
                raise ValueError("review session cannot be rejected")
            now = utcnow()
            connection.execute("UPDATE review_session SET status='rejected',comment=?,closed_at=? WHERE id=?", (comment, now, session_id))
            connection.execute("UPDATE product_route SET status='deprecated',updated_at=? WHERE id=?", (now, session["route_id"]))
            connection.execute(
                """INSERT INTO review_decision(review_session_id,entity_type,entity_id,field_name,decision,comment,decided_at)
                   VALUES(?,'route',?,'','rejected',?,?)""",
                (session_id, session["route_id"], f"{reviewer}: {comment}", now),
            )

    def create_revision(self, approved_route_id: int, *, created_by: str) -> int:
        snapshot = self.get_route(approved_route_id)
        if snapshot["route"]["status"] != "approved":
            raise ValueError("new revision can only be created from an approved route")
        with self.connect() as connection:
            route = snapshot["route"]
            next_version = int(connection.execute("SELECT COALESCE(MAX(version),0)+1 FROM product_route WHERE product_id=?", (route["product_id"],)).fetchone()[0])
            now = utcnow()
            cursor = connection.execute(
                """INSERT INTO product_route(product_id,process_family_id,version,status,approval_scope,route_name,route_summary,source_kind,parent_route_id,created_by,created_at,updated_at)
                   VALUES(?,?,?,'draft','none',?,?, 'exact_approved',?,?,?,?)""",
                (route["product_id"], route["process_family_id"], next_version, route["route_name"], route["route_summary"], approved_route_id, created_by, now, now),
            )
            new_route_id = int(cursor.lastrowid)
            old_to_new: dict[int, int] = {}
            for step in snapshot["steps"]:
                parent_new = old_to_new.get(step["parent_step_id"]) if step["parent_step_id"] else None
                scalar_values = [step[key] for key in ("sequence_no", "step_code", "title", "action", "why", "work_image_slots")]
                json_values = [
                    json.dumps(step[key], ensure_ascii=False)
                    for key in (
                        "input_json", "material_json", "tool_equipment_json", "fixture_json",
                        "parameter_json", "method_json", "quality_check_json", "acceptance_criteria_json",
                        "safety_json", "record_output_json", "exception_json", "unknowns_json",
                    )
                ]
                c = connection.execute(
                    """INSERT INTO route_step(route_id,parent_step_id,sequence_no,step_code,title,action,why,work_image_slots,input_json,material_json,tool_equipment_json,fixture_json,parameter_json,method_json,quality_check_json,acceptance_criteria_json,safety_json,record_output_json,exception_json,unknowns_json,review_state,reviewer_comment,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'unreviewed','',?,?)""",
                    (new_route_id, parent_new, *scalar_values, *json_values, now, now),
                )
                old_to_new[step["id"]] = int(c.lastrowid)
            for section in snapshot.get("sections", []):
                connection.execute(
                    """INSERT INTO route_section(
                           route_id,section_type,version,content_json,review_state,reviewer_comment,
                           source_json,conflicts_json,unknowns_json,created_by,created_at,updated_at
                       ) VALUES(?,?,1,?,'unreviewed','',?,?,?,?,?,?)""",
                    (
                        new_route_id, section["section_type"],
                        json.dumps(section["content_json"], ensure_ascii=False),
                        json.dumps(section["source_json"], ensure_ascii=False),
                        json.dumps(section["conflicts_json"], ensure_ascii=False),
                        json.dumps(section["unknowns_json"], ensure_ascii=False),
                        created_by, now, now,
                    ),
                )
            for reference in snapshot.get("reference_files", []):
                connection.execute(
                    """INSERT INTO route_reference_file(
                           route_id,original_name,storage_path,sha256,mime_type,size_bytes,source_note,
                           review_state,uploaded_by,created_at
                       ) VALUES(?,?,?,?,?,?,?,'needs_revision',?,?)""",
                    (
                        new_route_id,
                        reference["original_name"],
                        reference["storage_path"],
                        reference["sha256"],
                        reference["mime_type"],
                        reference["size_bytes"],
                        f"修订版来自路线 {approved_route_id}：{reference.get('source_note', '')}".strip(),
                        created_by,
                        now,
                    ),
                )
            return new_route_id

    def retrieve_approved(self, product_code: str, features: dict[str, str], *, allow_demonstration: bool = False, limit: int = 5) -> list[RouteMatch]:
        normalized = {key: self._normalize(value) for key, value in features.items()}
        with self.connect() as connection:
            scope_clause = "r.approval_scope IN ('formal_production','demonstration_only')" if allow_demonstration else "r.approval_scope='formal_production'"
            rows = connection.execute(
                f"""SELECT r.id,r.version,r.approval_scope,p.product_code,p.process_family_id
                    FROM product_route r JOIN product p ON p.id=r.product_id
                    WHERE r.status='approved' AND {scope_clause}"""
            ).fetchall()
            matches: list[RouteMatch] = []
            target_family = connection.execute("SELECT process_family_id FROM product WHERE product_code=?", (product_code,)).fetchone()
            for row in rows:
                source_features = {item["feature_key"]: item["normalized_value"] for item in connection.execute(
                    "SELECT feature_key,normalized_value FROM product_feature WHERE product_id=(SELECT product_id FROM product_route WHERE id=?)", (row["id"],)
                )}
                keys = set(normalized) | set(source_features)
                exact = [key for key in keys if normalized.get(key) and normalized.get(key) == source_features.get(key)]
                conflicts = [key for key in keys if normalized.get(key) and source_features.get(key) and normalized[key] != source_features[key]]
                feature_score = len(exact) / max(1, len(keys))
                family_bonus = 0.25 if target_family and target_family[0] == row["process_family_id"] else 0.0
                similarity = min(1.0, feature_score * 0.75 + family_bonus)
                field_sources = self._field_sources(connection, int(row["id"]))
                matches.append(RouteMatch(
                    source_route_id=int(row["id"]), source_product_code=row["product_code"], source_version=int(row["version"]),
                    approval_scope=row["approval_scope"], similarity=round(similarity, 4),
                    match_basis={"exact_features": exact, "conflicting_features": conflicts, "same_process_family": bool(family_bonus)},
                    field_sources=field_sources,
                ))
            return sorted(matches, key=lambda item: item.similarity, reverse=True)[:limit]

    def record_rejected_candidate(self, source_path: str, batch_id: str, reason: str, metadata: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO route_candidate(source_path,batch_id,status,rejection_reason,metadata_json,created_at)
                   VALUES(?,?,'rejected',?,?,?)
                   ON CONFLICT(source_path,batch_id) DO UPDATE SET status='rejected',rejection_reason=excluded.rejection_reason,metadata_json=excluded.metadata_json""",
                (source_path, batch_id, reason, json.dumps(metadata, ensure_ascii=False), utcnow()),
            )

    def backup(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as source, sqlite3.connect(target) as dest:
            source.backup(dest)
        return target

    def export_json(self, destination: str | Path) -> Path:
        target = Path(destination)
        with self.connect() as connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%' AND name NOT LIKE '%_data' AND name NOT LIKE '%_idx' AND name NOT LIKE '%_content' AND name NOT LIKE '%_docsize' AND name NOT LIKE '%_config'")]
            payload = {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in tables}
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    @staticmethod
    def formal_confirmation_token(product_code: str) -> str:
        return f"FORMAL_APPROVE:{product_code}"

    def _active_review_session(
        self,
        connection: sqlite3.Connection,
        route_id: int,
        reviewer: str,
        comment: str = "auto-created for field/section review trace",
    ) -> int:
        existing = connection.execute(
            "SELECT id FROM review_session WHERE route_id=? AND status IN ('draft','under_review') ORDER BY id DESC LIMIT 1",
            (route_id,),
        ).fetchone()
        if existing:
            return int(existing[0])
        cursor = connection.execute(
            "INSERT INTO review_session(route_id,reviewer,status,comment,started_at) VALUES(?,?,'draft',?,?)",
            (route_id, reviewer, comment, utcnow()),
        )
        return int(cursor.lastrowid)

    def _formal_approval_gate_errors(
        self,
        connection: sqlite3.Connection,
        route_id: int,
        confirmation_token: str | None,
    ) -> list[dict[str, Any]]:
        route = connection.execute(
            """SELECT r.*,p.product_code FROM product_route r
               JOIN product p ON p.id=r.product_id WHERE r.id=?""",
            (route_id,),
        ).fetchone()
        if not route:
            return [{"gate": "route_exists", "detail": "route not found"}]
        product_code = route["product_code"]
        errors: list[dict[str, Any]] = []
        expected_token = self.formal_confirmation_token(product_code)
        if confirmation_token != expected_token:
            errors.append({
                "gate": "confirmation_token",
                "detail": f"token must explicitly confirm product {product_code}",
            })

        steps = [self._decode_step(row) for row in connection.execute(
            "SELECT * FROM active_route_step WHERE route_id=? ORDER BY sequence_no,id", (route_id,)
        )]
        unconfirmed_steps = [step["step_code"] for step in steps if step["review_state"] != "confirmed"]
        if unconfirmed_steps:
            errors.append({"gate": "steps_confirmed", "items": unconfirmed_steps})
        confirmed_step_decisions = {
            int(row[0]) for row in connection.execute(
                """SELECT DISTINCT d.entity_id FROM review_decision d
                   JOIN review_session s ON s.id=d.review_session_id
                   WHERE s.route_id=? AND d.entity_type='field' AND d.field_name='review_state'
                     AND d.decision='confirmed'""",
                (route_id,),
            )
        }
        steps_without_human_decision = [
            step["step_code"] for step in steps
            if step["review_state"] == "confirmed" and step["id"] not in confirmed_step_decisions
        ]
        if steps_without_human_decision:
            errors.append({"gate": "step_human_confirmation_decisions", "items": steps_without_human_decision})

        sections = [self._decode_section(row) for row in connection.execute(
            """SELECT s.* FROM route_section s
               JOIN (SELECT section_type,MAX(version) AS version FROM route_section WHERE route_id=? GROUP BY section_type) latest
                 ON latest.section_type=s.section_type AND latest.version=s.version
               WHERE s.route_id=? ORDER BY s.section_type""",
            (route_id, route_id),
        )]
        section_by_type = {section["section_type"]: section for section in sections}
        missing_sections = [name for name in ROUTE_SECTION_TYPES if name not in section_by_type]
        if missing_sections:
            errors.append({"gate": "sections_present", "items": missing_sections})
        unconfirmed_sections = [
            name for name in ROUTE_SECTION_TYPES
            if name in section_by_type and section_by_type[name]["review_state"] != "confirmed"
        ]
        if unconfirmed_sections:
            errors.append({"gate": "sections_confirmed", "items": unconfirmed_sections})
        confirmed_section_decisions = {
            int(row[0]) for row in connection.execute(
                """SELECT DISTINCT d.entity_id FROM review_decision d
                   JOIN review_session s ON s.id=d.review_session_id
                   WHERE s.route_id=? AND d.entity_type IN ('product','bom','tooling','parameter','qc','packaging','ie_time','signoff')
                     AND d.decision='confirmed'""",
                (route_id,),
            )
        }
        sections_without_human_decision = [
            section["section_type"] for section in sections
            if section["review_state"] == "confirmed" and section["id"] not in confirmed_section_decisions
        ]
        if sections_without_human_decision:
            errors.append({"gate": "section_human_confirmation_decisions", "items": sections_without_human_decision})

        blocking_unknowns: list[str] = []
        for step in steps:
            for item in step["unknowns_json"]:
                if item.get("blocking", True):
                    blocking_unknowns.append(f"step:{step['step_code']}:{item.get('field_name','unknown')}")
        for section in sections:
            for item in section["unknowns_json"]:
                if item.get("blocking", True):
                    blocking_unknowns.append(f"section:{section['section_type']}:{item.get('field_name','unknown')}")
        if blocking_unknowns:
            errors.append({"gate": "blocking_unknowns_zero", "count": len(blocking_unknowns), "items": blocking_unknowns})

        conflicts = {
            section["section_type"]: section["conflicts_json"]
            for section in sections if section["conflicts_json"]
        }
        if conflicts:
            errors.append({"gate": "section_conflicts_zero", "items": conflicts})

        validation_payload = {
            "product_code": product_code,
            "steps": [
                {key: step[key] for key in (
                    "step_code", "title", "action", "why", "input_json", "material_json",
                    "tool_equipment_json", "fixture_json", "parameter_json", "method_json",
                    "quality_check_json", "acceptance_criteria_json", "safety_json",
                    "record_output_json", "exception_json",
                )}
                for step in steps
            ],
            "sections": [
                {"section_type": section["section_type"], "content": section["content_json"]}
                for section in sections
            ],
        }
        validation_text = json.dumps(validation_payload, ensure_ascii=False)
        unresolved = sorted(set(re.findall(r"\{[A-Za-z_][A-Za-z0-9_]*\}", validation_text)))
        if unresolved:
            errors.append({"gate": "unresolved_placeholders_zero", "items": unresolved})
        if "待确认" in validation_text:
            errors.append({"gate": "generic_unknown_absent", "items": ["待确认"]})
        wrong_models: list[str] = []
        if product_code.startswith("YA.C.06."):
            wrong_models = [token for token in re.findall(r"YA\.C\.06\.\d{4}", validation_text) if token != product_code]
        elif product_code.startswith("W-H"):
            wrong_models = [token for token in re.findall(r"W-H\d+", validation_text) if token != product_code]
        wrong_models = sorted(set(wrong_models))
        if wrong_models:
            errors.append({"gate": "model_conflicts_zero", "items": wrong_models})
        return errors

    def _snapshot_with_connection(self, connection: sqlite3.Connection, route_id: int) -> dict[str, Any]:
        route = dict(connection.execute(
            """SELECT r.*,p.product_code,p.product_name,f.code AS process_family_code
               FROM product_route r JOIN product p ON p.id=r.product_id JOIN process_family f ON f.id=r.process_family_id WHERE r.id=?""",
            (route_id,),
        ).fetchone())
        steps = [self._decode_step(row) for row in connection.execute("SELECT * FROM active_route_step WHERE route_id=? ORDER BY sequence_no,id", (route_id,))]
        sections = [self._decode_section(row) for row in connection.execute(
            """SELECT s.* FROM route_section s
               JOIN (SELECT section_type,MAX(version) AS version FROM route_section WHERE route_id=? GROUP BY section_type) latest
                 ON latest.section_type=s.section_type AND latest.version=s.version
               WHERE s.route_id=? ORDER BY s.section_type""",
            (route_id, route_id),
        )]
        return {"route": route, "steps": steps, "sections": sections}

    def _decode_step(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for column in JSON_FIELDS.values():
            item[column] = json.loads(item[column])
        return item

    @staticmethod
    def _decode_section(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for column in ("content_json", "source_json", "conflicts_json", "unknowns_json"):
            item[column] = json.loads(item[column])
        if item["section_type"] == "ie_timing":
            # Surface new editable fields for pre-existing routes without rewriting their history.
            content = dict(item["content_json"])
            content.setdefault("单价", "")
            content.setdefault("人数", "")
            item["content_json"] = content
        return item

    @staticmethod
    def _decode_json_columns(item: dict[str, Any], columns: list[str]) -> dict[str, Any]:
        for column in columns:
            item[column] = json.loads(item[column])
        return item

    @staticmethod
    def _normalize(value: Any) -> str:
        return " ".join(str(value).strip().lower().replace("_", " ").split())

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _field_sources(self, connection: sqlite3.Connection, route_id: int) -> dict[str, dict[str, Any]]:
        sources: dict[str, dict[str, Any]] = {}
        for row in connection.execute("SELECT step_code FROM active_route_step WHERE route_id=?", (route_id,)):
            for field_name in ("action","why",*JSON_FIELDS):
                sources[f"{row['step_code']}.{field_name}"] = {"source_route_id": route_id, "source_step_code": row["step_code"], "source_field": field_name}
        return sources

    def _build_field_map(self, connection: sqlite3.Connection, source_route_id: int, target_route_id: int) -> dict[str, Any]:
        source = {row["step_code"]: dict(row) for row in connection.execute("SELECT * FROM active_route_step WHERE route_id=?", (source_route_id,))}
        target = {row["step_code"]: dict(row) for row in connection.execute("SELECT * FROM active_route_step WHERE route_id=?", (target_route_id,))}
        result: dict[str, Any] = {}
        for code in set(source) & set(target):
            for field in ("action","why",*JSON_FIELDS.values()):
                result[f"{code}.{field}"] = {"source_route_id": source_route_id, "source_step_code": code, "source_field": field}
        return result

    def _write_reuse_provenance(self, connection: sqlite3.Connection, target_route_id: int, source_route_id: int, similarity: float) -> None:
        version = int(connection.execute("SELECT version FROM product_route WHERE id=?", (source_route_id,)).fetchone()[0])
        source_steps = {row["step_code"]: row for row in connection.execute("SELECT * FROM active_route_step WHERE route_id=?", (source_route_id,))}
        for target in connection.execute("SELECT * FROM active_route_step WHERE route_id=?", (target_route_id,)):
            if target["step_code"] not in source_steps:
                continue
            for field_name in ("action","why",*JSON_FIELDS):
                connection.execute(
                    """INSERT INTO field_provenance(route_step_id,route_id,field_name,source_route_id,source_route_version,source_step_code,source_field_name,confidence,conflict_status,note)
                       VALUES(?,?,?,?,?,?,?,?, 'clear','approved route field reuse')""",
                    (target["id"], target_route_id, field_name, source_route_id, version, target["step_code"], field_name, similarity),
                )
