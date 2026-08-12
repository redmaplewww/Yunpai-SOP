from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cad_ai.sop_knowledge.web import create_builtin_server


def request_json(url: str, *, method: str = "GET", payload: dict | None = None) -> tuple[int, dict | list]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the dependency-free SOP review workbench.")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    smoke_db = args.output.parent / "workbench_smoke.sqlite3"
    with sqlite3.connect(args.db) as source, sqlite3.connect(smoke_db) as target:
        source.backup(target)

    server = create_builtin_server(smoke_db, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/", timeout=10) as response:
            html = response.read().decode("utf-8")
        products_status, products = request_json(base + "/api/products")
        target_product = next(item for item in products if item["product_code"] == "YA.C.06.0017")
        route_status, route = request_json(base + f"/api/routes/{target_product['latest_route_id']}")
        identity = next(item for item in route["sections"] if item["section_type"] == "product_identity")
        edit_status, edit = request_json(
            base + f"/api/sections/{identity['id']}",
            method="PATCH",
            payload={
                "content": {**identity["content_json"], "smoke_review_note": "API/UI smoke edit on isolated database copy"},
                "review_state": "confirmed",
                "reviewer_comment": "smoke reviewer confirmed product identity section",
                "sources": identity["source_json"],
                "conflicts": identity["conflicts_json"],
                "unknowns": identity["unknowns_json"],
                "reviewer": "smoke_reviewer",
                "decision": "confirmed",
            },
        )
        review_status, review = request_json(
            base + f"/api/routes/{target_product['latest_route_id']}/reviews",
            method="POST", payload={"reviewer": "smoke_reviewer", "comment": "formal gate smoke"},
        )
        submit_status, submit = request_json(
            base + f"/api/reviews/{review['review_session_id']}/submit", method="POST", payload={},
        )
        approve_status, approve = request_json(
            base + f"/api/reviews/{review['review_session_id']}/approve",
            method="POST",
            payload={
                "approved_by": "smoke_approver",
                "approval_scope": "formal_production",
                "confirmation_token": "FORMAL_APPROVE:YA.C.06.0017",
            },
        )
        with sqlite3.connect(smoke_db) as connection:
            snapshot_count = connection.execute(
                "SELECT COUNT(*) FROM approval_snapshot WHERE route_id=?", (target_product["latest_route_id"],)
            ).fetchone()[0]
            fts_count = connection.execute(
                "SELECT COUNT(*) FROM route_fts WHERE route_id=?", (str(target_product["latest_route_id"]),)
            ).fetchone()[0]
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        result = {
            "server": "dependency_free_ThreadingHTTPServer",
            "ui_checks": {
                "route_sections_visible": "路线级审核 Section" in html,
                "formal_approval_action_visible": "正式生产批准" in html,
                "demo_risk_visible": "演示批准风险隔离" in html,
                "product_identity_editable": "content_json" in html,
            },
            "api": {
                "products_status": products_status,
                "route_status": route_status,
                "section_patch_status": edit_status,
                "section_new_version_id": edit.get("section_id"),
                "review_status": review_status,
                "submit_status": submit_status,
                "formal_approval_status": approve_status,
                "formal_approval_error": approve,
            },
            "failed_approval_side_effects": {
                "snapshot_count": snapshot_count,
                "formal_fts_count": fts_count,
            },
            "database_integrity": integrity,
            "pass": (
                products_status == route_status == edit_status == review_status == submit_status == 200
                and approve_status == 400
                and snapshot_count == 0
                and fts_count == 0
                and integrity == "ok"
                and all(("路线级审核 Section" in html, "正式生产批准" in html, "演示批准风险隔离" in html))
            ),
        }
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["pass"] else 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
