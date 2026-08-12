from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SopImportReport:
    case_count: int
    step_count: int
    flow_node_count: int
    flow_edge_count: int
    ie_time_row_count: int
    material_item_count: int
    section_count: int
    artifact_count: int
    skipped_reasons: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SopSearchHit:
    sop_id: int
    product_name: str
    part_no: str
    document_no: str
    station: str
    status: str
    score: float
    reasons: list[str]


class SopTableCaseStore:
    """SQLite SOP table case library for deterministic retrieval and evaluation."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if self.db_path.parent and str(self.db_path.parent) != ".":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.initialize()

    def __enter__(self) -> "SopTableCaseStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS sop_case (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sop_code TEXT NOT NULL UNIQUE,
                product_name TEXT NOT NULL,
                part_no TEXT NOT NULL DEFAULT '',
                document_no TEXT NOT NULL DEFAULT '',
                drawing_no TEXT NOT NULL DEFAULT '',
                station TEXT NOT NULL DEFAULT '',
                version TEXT NOT NULL DEFAULT 'DRAFT',
                status TEXT NOT NULL DEFAULT 'demo_not_for_release',
                source_dir TEXT NOT NULL DEFAULT '',
                source_manifest TEXT NOT NULL DEFAULT '',
                source_parsed_sop TEXT NOT NULL DEFAULT '',
                search_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sop_step (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sop_id INTEGER NOT NULL REFERENCES sop_case(id) ON DELETE CASCADE,
                slot_no INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                visual_type TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sop_flow_node (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sop_id INTEGER NOT NULL REFERENCES sop_case(id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                node_id TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                node_type TEXT NOT NULL DEFAULT '',
                shape TEXT NOT NULL DEFAULT '',
                station TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sop_flow_edge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sop_id INTEGER NOT NULL REFERENCES sop_case(id) ON DELETE CASCADE,
                from_node TEXT NOT NULL DEFAULT '',
                to_node TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sop_ie_time_row (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sop_id INTEGER NOT NULL REFERENCES sop_case(id) ON DELETE CASCADE,
                action TEXT NOT NULL DEFAULT '',
                machine_model TEXT NOT NULL DEFAULT '',
                measurement_method TEXT NOT NULL DEFAULT '',
                observations TEXT NOT NULL DEFAULT '',
                average_observed_time_s TEXT NOT NULL DEFAULT '',
                rating_factor TEXT NOT NULL DEFAULT '',
                allowance_rate TEXT NOT NULL DEFAULT '',
                standard_time_s TEXT NOT NULL DEFAULT '',
                time_source TEXT NOT NULL DEFAULT '',
                dynamic_adjustment TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sop_material_item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sop_id INTEGER NOT NULL REFERENCES sop_case(id) ON DELETE CASCADE,
                item_no TEXT NOT NULL DEFAULT '',
                material_code TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                specification TEXT NOT NULL DEFAULT '',
                quantity TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sop_section (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sop_id INTEGER NOT NULL REFERENCES sop_case(id) ON DELETE CASCADE,
                section_type TEXT NOT NULL DEFAULT 'side',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sop_artifact (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sop_id INTEGER NOT NULL REFERENCES sop_case(id) ON DELETE CASCADE,
                artifact_key TEXT NOT NULL DEFAULT '',
                path TEXT NOT NULL DEFAULT '',
                sha256 TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sop_retrieval_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                object_type TEXT NOT NULL,
                object_id INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                keywords TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        try:
            self.connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS sop_retrieval_index_fts
                USING fts5(
                    object_type UNINDEXED,
                    object_id UNINDEXED,
                    title,
                    content,
                    keywords,
                    tokenize='unicode61'
                )
                """
            )
        except sqlite3.OperationalError:
            pass
        self.connection.commit()

    def import_package(self, package_dir: str | Path) -> SopImportReport:
        root = Path(package_dir)
        files = _resolve_sop_files(root)
        skipped_reasons: dict[str, int] = {}
        if files["manifest"] is None:
            parsed_path = files["parsed"]
            if parsed_path is None:
                _bump(skipped_reasons, "missing_manifest_json")
                return SopImportReport(0, 0, 0, 0, 0, 0, 0, 0, skipped_reasons)
            parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
            manifest = _manifest_from_parsed(parsed, source_path=parsed_path)
            return self._import_manifest_and_parsed(root, parsed_path, parsed_path, manifest, parsed, skipped_reasons)
        manifest_path = files["manifest"]
        parsed_path = files["parsed"]

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if parsed_path is not None:
            parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
        else:
            parsed = _parsed_sop_from_manifest(manifest)
            if not parsed:
                _bump(skipped_reasons, "manifest_without_importable_sop_content")
                return SopImportReport(0, 0, 0, 0, 0, 0, 0, 0, skipped_reasons)
        return self._import_manifest_and_parsed(root, manifest_path, parsed_path, manifest, parsed, skipped_reasons)

    def import_any(self, input_path: str | Path) -> SopImportReport:
        path = Path(input_path)
        if path.is_dir():
            return self.import_package(path)
        suffix = path.suffix.lower()
        if suffix == ".json":
            return self._import_json_file(path)
        if suffix == ".docx":
            parsed = _parsed_sop_from_docx(path)
            manifest = _manifest_from_parsed(parsed, source_path=path)
            return self._import_manifest_and_parsed(
                path.parent,
                path,
                path,
                manifest,
                parsed,
                {},
            )
        return SopImportReport(0, 0, 0, 0, 0, 0, 0, 0, {f"unsupported_source:{suffix or 'unknown'}": 1})

    def import_many(self, input_paths: list[str | Path]) -> SopImportReport:
        reports = [self.import_any(path) for path in input_paths]
        skipped: dict[str, int] = {}
        for report in reports:
            for key, value in report.skipped_reasons.items():
                skipped[key] = skipped.get(key, 0) + value
        return SopImportReport(
            case_count=sum(report.case_count for report in reports),
            step_count=sum(report.step_count for report in reports),
            flow_node_count=sum(report.flow_node_count for report in reports),
            flow_edge_count=sum(report.flow_edge_count for report in reports),
            ie_time_row_count=sum(report.ie_time_row_count for report in reports),
            material_item_count=sum(report.material_item_count for report in reports),
            section_count=sum(report.section_count for report in reports),
            artifact_count=sum(report.artifact_count for report in reports),
            skipped_reasons=skipped,
        )

    def _import_json_file(self, path: Path) -> SopImportReport:
        data = json.loads(path.read_text(encoding="utf-8"))
        if _looks_like_parsed_sop(data):
            parsed = data
            manifest = _manifest_from_parsed(parsed, source_path=path)
            return self._import_manifest_and_parsed(path.parent, path, path, manifest, parsed, {})
        if _looks_like_manifest_sop(data):
            manifest = data
            parsed_path = _parsed_path_from_manifest(path.parent, manifest)
            parsed = json.loads(parsed_path.read_text(encoding="utf-8")) if parsed_path else _parsed_sop_from_manifest(manifest)
            if not parsed:
                return SopImportReport(0, 0, 0, 0, 0, 0, 0, 0, {"json_manifest_without_importable_sop_content": 1})
            return self._import_manifest_and_parsed(path.parent, path, parsed_path, manifest, parsed, {})
        return SopImportReport(0, 0, 0, 0, 0, 0, 0, 0, {"json_without_sop_contract": 1})

    def _import_manifest_and_parsed(
        self,
        root: Path,
        manifest_path: Path,
        parsed_path: Path | None,
        manifest: dict[str, Any],
        parsed: dict[str, Any],
        skipped_reasons: dict[str, int],
    ) -> SopImportReport:
        metadata = parsed.get("metadata") or {}
        product_name = _clean(metadata.get("product_name") or manifest.get("product_name"))
        if not product_name:
            _bump(skipped_reasons, "missing_product_name")
            return SopImportReport(0, 0, 0, 0, 0, 0, 0, 0, skipped_reasons)

        part_no = _clean(metadata.get("part_no") or manifest.get("part_no"))
        document_no = _clean(metadata.get("document_no") or manifest.get("document_no"))
        drawing_no = _clean(metadata.get("drawing_no") or manifest.get("drawing_no"))
        station = _clean(metadata.get("station") or manifest.get("station"))
        status = _clean(parsed.get("status") or manifest.get("status") or "demo_not_for_release")
        version = _clean(metadata.get("version") or "DRAFT")
        sop_code = _stable_code("SOP", f"{document_no}|{part_no}|{product_name}|{station}")
        self._delete_existing(sop_code)
        search_text = _search_text(parsed, manifest)
        now = _now()
        cursor = self.connection.execute(
            """
            INSERT INTO sop_case (
                sop_code, product_name, part_no, document_no, drawing_no, station,
                version, status, source_dir, source_manifest, source_parsed_sop,
                search_text, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sop_code,
                product_name,
                part_no,
                document_no,
                drawing_no,
                station,
                version,
                status,
                str(root),
                str(manifest_path),
                str(parsed_path or ""),
                search_text,
                now,
                now,
            ),
        )
        sop_id = int(cursor.lastrowid)

        step_count = self._insert_steps(sop_id, parsed.get("step_slots") or [])
        flow = parsed.get("flowchart") or {}
        flow_node_count = self._insert_flow_nodes(sop_id, flow.get("nodes") or [])
        flow_edge_count = self._insert_flow_edges(sop_id, flow.get("edges") or [])
        ie_time_row_count = self._insert_ie_rows(sop_id, parsed.get("ie_time_study_rows") or [])
        material_item_count = self._insert_material_items(sop_id, parsed.get("bom_items") or [])
        section_count = self._insert_sections(sop_id, parsed.get("side_sections") or [], parsed.get("bottom_sections") or [])
        artifact_count = self._insert_artifacts(sop_id, manifest.get("artifacts") or {}, root)
        self._index_object("sop_case", sop_id, product_name, search_text)
        self.connection.commit()
        return SopImportReport(
            case_count=1,
            step_count=step_count,
            flow_node_count=flow_node_count,
            flow_edge_count=flow_edge_count,
            ie_time_row_count=ie_time_row_count,
            material_item_count=material_item_count,
            section_count=section_count,
            artifact_count=artifact_count,
            skipped_reasons=skipped_reasons,
        )

    def search_requirement(self, requirement_text: str, *, limit: int = 5) -> list[SopSearchHit]:
        rows = self.connection.execute(
            """
            SELECT id, product_name, part_no, document_no, station, status, search_text
            FROM sop_case
            """
        ).fetchall()
        hits: list[SopSearchHit] = []
        for row in rows:
            score, reasons = _score_requirement(requirement_text, row["search_text"], row["product_name"], row["station"])
            if score <= 0:
                continue
            hits.append(
                SopSearchHit(
                    sop_id=int(row["id"]),
                    product_name=row["product_name"],
                    part_no=row["part_no"],
                    document_no=row["document_no"],
                    station=row["station"],
                    status=row["status"],
                    score=score,
                    reasons=reasons,
                )
            )
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:limit]

    def table_counts(self) -> dict[str, int]:
        tables = [
            "sop_case",
            "sop_step",
            "sop_flow_node",
            "sop_flow_edge",
            "sop_ie_time_row",
            "sop_material_item",
            "sop_section",
            "sop_artifact",
            "sop_retrieval_index",
        ]
        return {table: int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}

    def _delete_existing(self, sop_code: str) -> None:
        row = self.connection.execute("SELECT id FROM sop_case WHERE sop_code = ?", (sop_code,)).fetchone()
        if row is None:
            return
        sop_id = int(row["id"])
        self.connection.execute("DELETE FROM sop_retrieval_index WHERE object_type = ? AND object_id = ?", ("sop_case", sop_id))
        try:
            self.connection.execute(
                "DELETE FROM sop_retrieval_index_fts WHERE object_type = ? AND object_id = ?",
                ("sop_case", sop_id),
            )
        except sqlite3.OperationalError:
            pass
        self.connection.execute("DELETE FROM sop_case WHERE id = ?", (sop_id,))

    def _insert_steps(self, sop_id: int, steps: list[dict[str, Any]]) -> int:
        count = 0
        for index, step in enumerate(steps, start=1):
            self.connection.execute(
                """
                INSERT INTO sop_step (sop_id, slot_no, title, description, visual_type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    sop_id,
                    int(step.get("slot_no") or index),
                    _clean(step.get("title")),
                    _clean(step.get("description") or step.get("text_placeholder")),
                    _clean(step.get("visual_type") or (step.get("visual") or {}).get("type")),
                ),
            )
            count += 1
        return count

    def _insert_flow_nodes(self, sop_id: int, nodes: list[dict[str, Any]]) -> int:
        count = 0
        for index, node in enumerate(nodes, start=1):
            self.connection.execute(
                """
                INSERT INTO sop_flow_node (
                    sop_id, seq, node_id, label, name, node_type, shape, station, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sop_id,
                    int(node.get("seq") or index),
                    _clean(node.get("id")),
                    _clean(node.get("label")),
                    _clean(node.get("name")),
                    _clean(node.get("type")),
                    _clean(node.get("shape")),
                    _clean(node.get("station")),
                    _clean(node.get("note")),
                ),
            )
            count += 1
        return count

    def _insert_flow_edges(self, sop_id: int, edges: list[dict[str, Any]]) -> int:
        count = 0
        for edge in edges:
            self.connection.execute(
                """
                INSERT INTO sop_flow_edge (sop_id, from_node, to_node, label)
                VALUES (?, ?, ?, ?)
                """,
                (sop_id, _clean(edge.get("from")), _clean(edge.get("to")), _clean(edge.get("label"))),
            )
            count += 1
        return count

    def _insert_ie_rows(self, sop_id: int, rows: list[dict[str, Any]]) -> int:
        count = 0
        for row in rows:
            self.connection.execute(
                """
                INSERT INTO sop_ie_time_row (
                    sop_id, action, machine_model, measurement_method, observations,
                    average_observed_time_s, rating_factor, allowance_rate,
                    standard_time_s, time_source, dynamic_adjustment
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sop_id,
                    _clean(row.get("action") or row.get("动作")),
                    _clean(row.get("machine_model") or row.get("机器型号")),
                    _clean(row.get("measurement_method") or row.get("IE测量方法")),
                    _clean(row.get("observations") or row.get("观测次数")),
                    _clean(row.get("average_observed_time_s") or row.get("平均观测工时(s)")),
                    _clean(row.get("rating_factor") or row.get("评比系数")),
                    _clean(row.get("allowance_rate") or row.get("宽放率")),
                    _clean(row.get("standard_time_s") or row.get("标准工时(s)")),
                    _clean(row.get("time_source") or row.get("工时来源")),
                    _clean(row.get("dynamic_adjustment") or row.get("动态调整")),
                ),
            )
            count += 1
        return count

    def _insert_material_items(self, sop_id: int, items: list[dict[str, Any]]) -> int:
        count = 0
        for item in items:
            self.connection.execute(
                """
                INSERT INTO sop_material_item (
                    sop_id, item_no, material_code, name, specification, quantity, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sop_id,
                    _clean(item.get("item_no")),
                    _clean(item.get("material_code")),
                    _clean(item.get("name")),
                    _clean(item.get("specification")),
                    _clean(item.get("quantity")),
                    _clean(item.get("note")),
                ),
            )
            count += 1
        return count

    def _insert_sections(self, sop_id: int, side_sections: list[dict[str, Any]], bottom_sections: list[dict[str, Any]]) -> int:
        count = 0
        for section_type, sections in (("side", side_sections), ("bottom", bottom_sections)):
            for section in sections:
                lines = section.get("lines")
                content = "\n".join(str(item) for item in lines) if isinstance(lines, list) else _clean(section.get("value"))
                self.connection.execute(
                    """
                    INSERT INTO sop_section (sop_id, section_type, title, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    (sop_id, section_type, _clean(section.get("title")), content),
                )
                count += 1
        return count

    def _insert_artifacts(self, sop_id: int, artifacts: dict[str, Any], root: Path) -> int:
        count = 0
        for key, raw_path in artifacts.items():
            if raw_path is None:
                continue
            path = Path(str(raw_path))
            if not path.is_absolute():
                path = root / path
            self.connection.execute(
                """
                INSERT INTO sop_artifact (sop_id, artifact_key, path, sha256)
                VALUES (?, ?, ?, ?)
                """,
                (sop_id, str(key), str(path), _sha256_file(path)),
            )
            count += 1
        return count

    def _index_object(self, object_type: str, object_id: int, title: str, content: str) -> None:
        keywords = " ".join(sorted(_tokens(f"{title} {content}")))
        now = _now()
        self.connection.execute(
            """
            INSERT INTO sop_retrieval_index (object_type, object_id, title, content, keywords, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (object_type, object_id, title, content, keywords, now),
        )
        try:
            self.connection.execute(
                """
                INSERT INTO sop_retrieval_index_fts (object_type, object_id, title, content, keywords)
                VALUES (?, ?, ?, ?, ?)
                """,
                (object_type, object_id, title, content, keywords),
            )
        except sqlite3.OperationalError:
            pass


def _search_text(parsed: dict[str, Any], manifest: dict[str, Any]) -> str:
    metadata = parsed.get("metadata") or {}
    parts = [
        metadata.get("product_name"),
        metadata.get("part_no"),
        metadata.get("document_no"),
        metadata.get("drawing_no"),
        metadata.get("station"),
        parsed.get("operation_order"),
        manifest.get("run_id"),
    ]
    for step in parsed.get("step_slots") or []:
        parts.extend([step.get("title"), step.get("description"), step.get("visual_type")])
    flow = parsed.get("flowchart") or {}
    for node in flow.get("nodes") or []:
        parts.extend([node.get("id"), node.get("name"), node.get("type"), node.get("shape"), node.get("station")])
    for row in parsed.get("ie_time_study_rows") or []:
        parts.extend([row.get("action"), row.get("machine_model"), row.get("time_source")])
    for section in parsed.get("side_sections") or []:
        parts.append(section.get("title"))
        parts.extend(section.get("lines") or [])
    return " ".join(_clean(part) for part in parts if _clean(part))


def _resolve_sop_files(root: Path) -> dict[str, Path | None]:
    manifest = root / "manifest.json"
    if not manifest.exists():
        manifests = sorted(root.glob("*manifest*.json"))
        manifest = next((path for path in manifests if _json_file_looks_like_manifest_sop(path)), None)
    elif not _json_file_looks_like_manifest_sop(manifest):
        manifests = sorted(root.glob("*manifest*.json"))
        manifest = next((path for path in manifests if _json_file_looks_like_manifest_sop(path)), None)
    parsed: Path | None = None
    if manifest is not None and manifest.exists():
        try:
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest_data = {}
        parsed = _parsed_path_from_manifest(root, manifest_data)
    if parsed is None:
        parsed_candidates = [
            root / "parsed_sop.json",
            root / "qwen35b_parsed_sop.json",
            *sorted(root.glob("*parsed_sop*.json")),
        ]
        parsed = next((path for path in parsed_candidates if path.exists()), None)
    return {"manifest": manifest if manifest and manifest.exists() else None, "parsed": parsed}


def _json_file_looks_like_manifest_sop(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return _looks_like_manifest_sop(data)


def _parsed_path_from_manifest(root: Path, manifest: dict[str, Any]) -> Path | None:
    artifacts = manifest.get("artifacts") or {}
    for key in ("parsed_sop_json", "parsed_json", "parsed_sop"):
        raw = artifacts.get(key)
        if raw:
            path = Path(str(raw))
            if not path.is_absolute():
                path = root / path
            if path.exists():
                return path
    return None


def _looks_like_parsed_sop(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("metadata"), dict) and (
        "step_slots" in data or "flowchart" in data or "ie_time_study_rows" in data
    )


def _looks_like_manifest_sop(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    artifacts = data.get("artifacts") if isinstance(data.get("artifacts"), dict) else {}
    if {"document_docx", "parsed_sop_json", "center_flowchart_png", "format_check_json"} & set(artifacts):
        return True
    if isinstance(data.get("center_flowchart"), dict) and isinstance(data["center_flowchart"].get("nodes"), list):
        return True
    has_identity = bool(
        data.get("product_name")
        or data.get("document_no")
        or data.get("part_no")
        or data.get("station")
        or data.get("slug")
        or data.get("requirement_slug")
    )
    return has_identity and (
        "generation_sequence" in data
        or "tables_filled_before_flowchart" in data
        or "center_flowchart_target" in data
    )


def _manifest_from_parsed(parsed: dict[str, Any], *, source_path: Path) -> dict[str, Any]:
    metadata = parsed.get("metadata") or {}
    return {
        "status": parsed.get("status") or "demo_not_for_release",
        "run_id": source_path.stem,
        "product_name": metadata.get("product_name") or source_path.stem,
        "part_no": metadata.get("part_no") or "",
        "document_no": metadata.get("document_no") or source_path.stem,
        "station": metadata.get("station") or "",
        "generation_sequence": [],
        "artifacts": {"source": str(source_path)},
        "ai_boundary": "best_effort_ingestion; draft_not_for_release",
    }


def _parsed_sop_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    product_name = _clean(manifest.get("product_name") or manifest.get("requirement_slug") or manifest.get("slug"))
    if not product_name:
        return {}
    chart = manifest.get("center_flowchart") or {}
    nodes = chart.get("nodes") or []
    step_slots = [
        {
            "slot_no": index,
            "title": _clean(node.get("name")) or f"步骤{index}",
            "description": _clean(node.get("note")) or "由 manifest 中心流程图重构，需人工复核",
            "visual_type": _clean(node.get("type")) or "process",
        }
        for index, node in enumerate(nodes[:6], start=1)
    ]
    if not step_slots:
        step_slots = _step_slots_from_operation_text(manifest.get("operation_order") or manifest.get("requirement_text"))
    if not step_slots:
        step_slots = [{"slot_no": 1, "title": product_name, "description": "manifest-only SOP case", "visual_type": "process"}]
    station = _clean(manifest.get("station"))
    return {
        "status": _clean(manifest.get("status")) or "demo_not_for_release",
        "metadata": {
            "product_name": product_name,
            "part_no": _clean(manifest.get("part_no")),
            "document_no": _clean(manifest.get("document_no")) or _clean(manifest.get("slug")) or product_name,
            "drawing_no": _clean(manifest.get("drawing_no")),
            "station": station,
            "version": "DRAFT",
        },
        "operation_order": " -> ".join(step["title"] for step in step_slots),
        "step_slots": step_slots,
        "side_sections": _default_side_sections_from_manifest(manifest),
        "ie_time_study_rows": _draft_ie_rows_from_steps(step_slots),
        "flowchart": {
            "flowchart_title": chart.get("flowchart_title") or f"{product_name} 工艺流程",
            "nodes": nodes or _flow_nodes_from_steps(step_slots, station),
            "edges": chart.get("edges") or _linear_edges_for_count(max(len(nodes), len(step_slots))),
        },
        "bom_items": [],
        "notes": ["manifest_only_best_effort_ingestion; demo_not_for_release"],
    }


def _parsed_sop_from_docx(path: Path) -> dict[str, Any]:
    from docx import Document

    document = Document(path)
    values: list[str] = []
    seen: set[object] = set()
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell._tc in seen:
                    continue
                seen.add(cell._tc)
                text = _clean(cell.text)
                if text:
                    values.append(text)
    full_text = " ".join(values)
    product_name = _infer_docx_product_name(values, path)
    station = _infer_docx_station(values)
    step_slots = _step_slots_from_docx_values(values)
    if not step_slots:
        step_slots = _step_slots_from_operation_text(full_text)
    if not step_slots:
        step_slots = [{"slot_no": 1, "title": product_name, "description": "docx-only SOP case", "visual_type": "process"}]
    return {
        "status": "demo_not_for_release",
        "metadata": {
            "product_name": product_name,
            "part_no": "",
            "document_no": path.stem,
            "drawing_no": "",
            "station": station,
            "version": "DRAFT",
        },
        "operation_order": " -> ".join(step["title"] for step in step_slots),
        "step_slots": step_slots[:6],
        "side_sections": [{"title": "docx_text", "lines": values[:12]}],
        "ie_time_study_rows": _draft_ie_rows_from_steps(step_slots[:6]),
        "flowchart": {
            "flowchart_title": f"{product_name} 工艺流程",
            "nodes": _flow_nodes_from_steps(step_slots[:8], station),
            "edges": _linear_edges_for_count(min(len(step_slots), 8)),
        },
        "bom_items": [],
        "notes": ["docx_only_best_effort_ingestion; demo_not_for_release"],
    }


def _step_slots_from_docx_values(values: list[str]) -> list[dict[str, str | int]]:
    slots: list[dict[str, str | int]] = []
    for value in values:
        for match in re.finditer(r"(?P<slot>[1-6])[.．、]\s*(?P<title>[^:：\n]{2,24})(?:[:：](?P<desc>[^|]{0,80}))?", value):
            title = _clean(match.group("title"))
            if title and not any(slot["title"] == title for slot in slots):
                slots.append(
                    {
                        "slot_no": len(slots) + 1,
                        "title": title,
                        "description": _clean(match.group("desc")) or "docx 表格抽取，需人工复核",
                        "visual_type": _visual_type_for_text(title),
                    }
                )
            if len(slots) >= 6:
                return slots
    return slots


def _step_slots_from_operation_text(value: Any) -> list[dict[str, str | int]]:
    text = _clean(value)
    if not text:
        return []
    parts = [
        _clean(part)
        for part in re.split(r"->|→|－>|—>|、|;|；|\n", text)
        if 1 < len(_clean(part)) <= 30
    ]
    return [
        {
            "slot_no": index,
            "title": part,
            "description": "由流程文本重构，需人工复核",
            "visual_type": _visual_type_for_text(part),
        }
        for index, part in enumerate(parts[:6], start=1)
    ]


def _default_side_sections_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"title": "作业标准", "lines": ["由 manifest 入库，需人工复核"]},
        {"title": "设备/工具", "lines": ["待现场确认"]},
        {"title": "注意事项", "lines": [_clean(manifest.get("ai_boundary")) or "demo_not_for_release"]},
    ]


def _draft_ie_rows_from_steps(steps: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "action": _clean(step.get("title")),
            "machine_model": "待确认",
            "measurement_method": "入库草案，待现场IE实测",
            "observations": "",
            "average_observed_time_s": "",
            "rating_factor": "",
            "allowance_rate": "",
            "standard_time_s": "",
            "time_source": "draft_not_for_release",
            "dynamic_adjustment": "补齐现场数据后更新",
        }
        for step in steps
    ]


def _flow_nodes_from_steps(steps: list[dict[str, Any]], station: str) -> list[dict[str, Any]]:
    return [
        {
            "seq": index,
            "id": f"OP{index:02d}",
            "label": f"工序{index}",
            "name": _clean(step.get("title")),
            "type": _visual_type_for_text(step.get("title")),
            "shape": "diamond" if _visual_type_for_text(step.get("title")) in {"inspection", "test", "measurement"} else "ellipse",
            "station": station,
            "note": "demo_not_for_release",
        }
        for index, step in enumerate(steps, start=1)
    ]


def _linear_edges_for_count(count: int) -> list[dict[str, str]]:
    return [{"from": f"OP{index:02d}", "to": f"OP{index + 1:02d}", "label": "next"} for index in range(1, count)]


def _visual_type_for_text(value: Any) -> str:
    text = _clean(value).lower()
    if any(token in text for token in ["测试", "test", "eol", "ict", "fct"]):
        return "test"
    if any(token in text for token in ["检查", "检验", "量测", "测量", "判定", "inspection", "measurement", "aoi"]):
        return "inspection"
    return "process"


def _infer_docx_product_name(values: list[str], path: Path) -> str:
    for value in values[:20]:
        if any(token in value for token in ["产品", "品名", "Product"]):
            parts = re.split(r"[:：\n]", value)
            for part in reversed(parts):
                cleaned = _clean(part)
                if 2 <= len(cleaned) <= 40 and not any(label in cleaned for label in ["产品", "品名", "Product"]):
                    return cleaned
    return path.stem


def _infer_docx_station(values: list[str]) -> str:
    for value in values[:30]:
        if any(token in value for token in ["工站", "工位", "station", "Station"]):
            parts = re.split(r"[:：\n]", value)
            for part in reversed(parts):
                cleaned = _clean(part)
                if 2 <= len(cleaned) <= 40 and not any(label in cleaned for label in ["工站", "工位", "station", "Station"]):
                    return cleaned
    return ""


def _score_requirement(requirement_text: str, search_text: str, product_name: str, station: str) -> tuple[float, list[str]]:
    query_tokens = _tokens(requirement_text)
    if not query_tokens:
        return 0.0, []
    content_tokens = _tokens(search_text)
    matched = query_tokens & content_tokens
    score = float(len(matched))
    reasons: list[str] = []
    if matched:
        reasons.append("token_overlap:" + ",".join(sorted(matched)[:8]))
    normalized_query = _normalize(requirement_text)
    if product_name and _normalize(product_name) in normalized_query:
        score += 25
        reasons.append("product_name_exact")
    if station and _normalize(station) in normalized_query:
        score += 10
        reasons.append("station_exact")
    if _normalize(product_name).replace("demo", "") and _normalize(product_name).replace("demo", "") in normalized_query:
        score += 8
        reasons.append("product_name_without_demo")
    return score, reasons


def _tokens(text: Any) -> set[str]:
    normalized = _normalize(text)
    tokens = set(re.findall(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*", normalized))
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", str(text)):
        tokens.add(chunk)
        for size in (2, 3, 4):
            for index in range(0, max(len(chunk) - size + 1, 0)):
                tokens.add(chunk[index : index + size])
    return {token for token in tokens if len(token) >= 2}


def _stable_code(prefix: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def _sha256_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bump(values: dict[str, int], key: str) -> None:
    values[key] = values.get(key, 0) + 1


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value).lower())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
