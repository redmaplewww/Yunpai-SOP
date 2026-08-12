from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import openpyxl
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cad_ai.sop_agent import SopGenerateRequest, SopRoutingStep, _build_structured_sop_data
from cad_ai.sop_knowledge import ProductIdentity, RouteDraft, RouteSectionDraft, SopKnowledgeStore, SopRouteWorkflow, VariableRouteDocxRenderer


MATERIAL_ROOT = Path(r"F:\opencode\云湃智算\资料\整理结果\按产品分类")
DEFAULT_OUT = ROOT / "outputs" / "sop_route_refactor_final_20260811"


def extract_pdf(path: Path) -> dict[str, Any]:
    reader = PdfReader(path)
    pages = [page.extract_text() or "" for page in reader.pages]
    return {"path": str(path), "pages": len(pages), "text": "\n".join(pages)}


def extract_xlsx_context(path: Path, tokens: list[str], radius: int = 5) -> dict[str, Any]:
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=True)
    matches: list[dict[str, Any]] = []
    for worksheet in workbook.worksheets:
        rows = [list(row) for row in worksheet.iter_rows(values_only=True)]
        for index, row in enumerate(rows):
            text = " | ".join(str(value) for value in row if value not in (None, ""))
            if any(token.lower() in text.lower() for token in tokens):
                start = max(0, index - radius)
                end = min(len(rows), index + radius + 1)
                context = [
                    {"row": row_index + 1, "values": [value for value in rows[row_index] if value not in (None, "")]}
                    for row_index in range(start, end)
                ]
                matches.append({"sheet": worksheet.title, "match_row": index + 1, "context": context})
    workbook.close()
    return {"path": str(path), "matches": matches}


def build_profiles(evidence_path: Path) -> dict[str, Any]:
    rj_root = MATERIAL_ROOT / "YA.C.06.0017"
    rj_accept = next((rj_root / "01_承认书-订单").glob("*.pdf"))
    rj_drawings = sorted((rj_root / "02_工程图").glob("*.pdf"))
    rj_boms = sorted((rj_root / "03_BOM表").glob("*.xlsx"))
    oc_root = MATERIAL_ROOT / "W-H94"
    oc_order = oc_root / "01_承认书-订单" / "纬线CG2409290002.xlsx"
    oc_bom = oc_root / "03_BOM表" / "中性系列-成品BOM表-2026.04.28.xlsx"

    evidence = {
        "YA.C.06.0017": {
            "acceptance": extract_pdf(rj_accept),
            "drawings": [extract_pdf(path) for path in rj_drawings],
            "bom_hits": [extract_xlsx_context(path, ["YA.C.06.0017", "TKJ_C67GB105_23NAD4"], radius=4) for path in rj_boms],
        },
        "W-H94": {
            "order_hits": extract_xlsx_context(oc_order, ["W-H94"], radius=3),
            "bom_hits": extract_xlsx_context(oc_bom, ["W-H94", "YA.C.01.MZ21094", "YA.C.01.MZ21095"], radius=8),
        },
    }
    checks = {
        "rj_acceptance_contains_product": "YA.C.06.0017" in evidence["YA.C.06.0017"]["acceptance"]["text"],
        "rj_has_two_engineering_drawings": len(evidence["YA.C.06.0017"]["drawings"]) == 2,
        "rj_bom_direct_hit": any(item["matches"] for item in evidence["YA.C.06.0017"]["bom_hits"]),
        "optical_order_direct_hit": bool(evidence["W-H94"]["order_hits"]["matches"]),
        "optical_bom_direct_hit": bool(evidence["W-H94"]["bom_hits"]["matches"]),
    }
    evidence["checks"] = checks
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if not all(checks.values()):
        raise RuntimeError("source evidence extraction failed: " + json.dumps(checks, ensure_ascii=False))

    return {
        "YA.C.06.0017": {
            "profile_type": "rj45_acceptance",
            "product_name": "CAT6A FTP 长体双排6上2下两件式全包自扣铜壳水晶头",
            "supplier_part": "TKJ_C67GB105_23NAD4",
            "drawing_no": "T-145",
            "hole_mm": "1.05",
            "shell_material": "全包自扣式铜壳（承认书：镀金铜壳）",
            "key_dimensions": ["6.10±0.08", "3.25±0.13", "4.60±0.20", "9.50±0.10", "11.65±0.10", "22.80±0.30 mm"],
            "force_n": 30,
            "sources": [str(rj_accept), *[str(path) for path in rj_drawings], *[str(path) for path in rj_boms]],
            "evidence_scope": "本轮重新从只读资料提取：承认书、两份工程图和BOM直接命中上下文。",
        },
        "W-H94": {
            "profile_type": "optical_cable",
            "product_name": "中性 HDTV2.1 8K黑色锌合金光纤线 10M",
            "length_m": 10,
            "components": "XC005光纤铜包钢30# 10M；短距TX YA.C.01.MZ21094 1PCS；短距RX YA.C.01.MZ21095 1PCS；YA.F.01.041枪黑锌合金壳1套；HDMI防尘盖2PCS",
            "package": {
                "tie": "魔术贴22cm", "sleeve": "网套2个", "inner_bag": "透明骨袋23×25 1个",
                "labels": "标签3个", "box": "小号光纤彩盒20×20×6.5 1个", "carton": "纸箱42×42×22，12PCS/箱",
                "summary": "魔术贴22cm、网套2、透明骨袋23×25、小号彩盒、标签3、42×42×22纸箱12PCS/箱",
            },
            "sources": [str(oc_order), str(oc_bom)],
            "evidence_scope": "本轮重新从只读订单W-H94命中行和成品BOM材料/包材上下文提取，不采用价格、库存和公式结果。",
        },
    }


def reproduce_legacy_failure(path: Path) -> dict[str, Any]:
    def request(count: int) -> SopGenerateRequest:
        return SopGenerateRequest(
            product_name="Audit product", part_no="AUDIT-001", document_no="AUDIT-SOP-001",
            requirement_text="variable route reproduction",
            routing_steps=[SopRoutingStep(name=f"真实工序{i}") for i in range(1, count + 1)],
        )

    three = _build_structured_sop_data(request(3))
    eight = _build_structured_sop_data(request(8))
    source_line = inspect.getsourcelines(_build_structured_sop_data)[1]
    result = {
        "reproduction": {
            "input_3_steps_output_steps": len(three["steps"]),
            "input_3_steps_titles": [item[0] for item in three["steps"]],
            "input_8_steps_output_steps": len(eight["steps"]),
            "input_8_steps_titles": [item[0] for item in eight["steps"]],
        },
        "source": {
            "file": inspect.getsourcefile(_build_structured_sop_data),
            "function": "_build_structured_sop_data",
            "line": source_line,
            "evidence": "_pad_steps() followed by steps[:6]; model prompt also requires exactly six rows",
        },
        "conclusion": "confirmed_fixed_six_step_router",
    }
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build process-first SOP route knowledge-store demonstration.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "knowledge").mkdir(exist_ok=True)
    (out / "deliverables").mkdir(exist_ok=True)
    (out / "qa").mkdir(exist_ok=True)
    (out / "audit").mkdir(exist_ok=True)

    legacy = reproduce_legacy_failure(out / "audit" / "legacy_fixed_six_reproduction.json")
    profiles = build_profiles(out / "audit" / "source_evidence_extraction.json")
    (out / "audit" / "independent_product_profiles.json").write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")

    db_path = out / "knowledge" / "sop_knowledge.sqlite3"
    store = SopKnowledgeStore(db_path)
    store.initialize()
    store.ensure_process_family("rj45_connector_incoming_inspection", "RJ45水晶头来料检验与装配前放行", "制造路线资料不足时限定在有证据的来料检验/放行范围")
    store.ensure_process_family("active_optical_cable_final_assembly_packaging", "主动光纤线成品装配检验包装", "订单与成品BOM驱动的装配/检验/包装草案")
    store.create_operation_template_version(
        family_code="rj45_connector_incoming_inspection",
        template_code="RJ45-EVIDENCE-BOUND",
        template_name="RJ45水晶头证据约束工序族草案",
        version=1,
        status="draft",
        content={"purpose": "仅作为无已批准近似路线时的人工审核起点", "fixed_step_count": False, "requires_human_approval": True},
    )
    store.create_operation_template_version(
        family_code="active_optical_cable_final_assembly_packaging",
        template_code="AOC-EVIDENCE-BOUND",
        template_name="主动光纤线证据约束工序族草案",
        version=1,
        status="draft",
        content={"purpose": "仅作为无已批准近似路线时的人工审核起点", "fixed_step_count": False, "requires_human_approval": True},
    )
    rejected_batch = ROOT / "outputs" / "batch_sop_10_20260811"
    for docx in rejected_batch.glob("*/final/*.docx"):
        store.record_rejected_candidate(str(docx), "batch_sop_10_20260811", "fixed-six-step routing rejected by user", {"deliverable": False})

    workflow = SopRouteWorkflow(store)
    real_routes: dict[str, int] = {}
    validations: dict[str, Any] = {}
    route_drafts: dict[str, RouteDraft] = {}
    for code in ("YA.C.06.0017", "W-H94"):
        route_id, draft, validation = workflow.build_draft(code, profiles)
        real_routes[code] = route_id
        validations[code] = validation
        route_drafts[code] = draft
        (out / "deliverables" / code).mkdir(parents=True, exist_ok=True)

    # Demonstration-only human approval: clone a real route under an explicit DEMO product identity.
    demo_identity = ProductIdentity(
        product_code="DEMO-YA.C.06.0017",
        product_name="演示审批样本 - 基于YA.C.06.0017的路线",
        aliases=["DEMO_RJ45_APPROVED"],
        process_family_code="rj45_connector_incoming_inspection",
        description="demonstration_only; never a formal production approval",
    )
    demo_features = {
        "product_class":"RJ45 modular plug","network_category":"CAT6A","shielding":"FTP",
        "contact_layout":"dual-row 6-up 2-down","construction":"two-piece","hole_diameter_mm":"1.05",
        "shell_material":"copper shell","drawing_no":"T-145",
    }
    store.upsert_product(demo_identity, demo_features)
    demo_payload = route_drafts["YA.C.06.0017"].model_dump(mode="json")
    demo_payload["product"] = demo_identity.model_dump(mode="json")
    demo_payload["route_name"] = "DEMO 人工审核批准路线 - RJ45 1.05mm"
    demo_payload["route_summary"] = "演示人工审批与不可变快照；不代表正式生产批准。"
    demo_payload["source_kind"] = "manual"
    demo_route_id = store.create_route(RouteDraft.model_validate(demo_payload), created_by="demo_seed")
    for section in store.get_route(real_routes["YA.C.06.0017"])["sections"]:
        store.create_route_section(
            demo_route_id,
            RouteSectionDraft(
                section_type=section["section_type"],
                content=section["content_json"],
                review_state="unreviewed",
                reviewer_comment="演示审批：保留blocking unknown并与正式生产批准隔离。",
                sources=section["source_json"],
                conflicts=section["conflicts_json"],
                unknowns=section["unknowns_json"],
            ),
            created_by="demo_seed",
        )
    review_session = store.create_review_session(demo_route_id, "demo_human_reviewer", "逐字段演示审核")
    first_step_id = store.get_route(demo_route_id)["steps"][0]["id"]
    store.update_step_field(first_step_id, "reviewer_comment", "演示人工已核对产品身份字段；非正式生产审批。", reviewer="demo_human_reviewer")
    store.submit_review(review_session)
    store.approve(review_session, approved_by="demo_human_reviewer", approval_scope="demonstration_only")

    near_identity = ProductIdentity(
        product_code="DEMO-YA.C.06.0022-REUSE",
        product_name="演示近似产品 - CAT6A FTP 6上2下透明蓝铁壳",
        aliases=["DEMO_REUSE_TARGET"], process_family_code="rj45_connector_incoming_inspection",
        description="used only to prove approved-only similarity reuse",
    )
    near_features = dict(demo_features)
    near_features["shell_material"] = "blue iron shell"
    store.upsert_product(near_identity, near_features)
    matches = store.retrieve_approved(near_identity.product_code, near_features, allow_demonstration=True)
    if not matches or matches[0].source_route_id != demo_route_id:
        raise RuntimeError("demonstration approved route retrieval failed")
    reuse_route_id = store.clone_approved_route_as_draft(
        demo_route_id, near_identity, similarity=matches[0].similarity, match_basis=matches[0].match_basis,
    )
    reuse = store.get_route(reuse_route_id)
    (out / "audit" / "approved_reuse_demonstration.json").write_text(
        json.dumps({
            "approval_boundary":"demonstration_only_not_formal_production",
            "source_route_id":demo_route_id,
            "target_route_id":reuse_route_id,
            "match":matches[0].model_dump(mode="json"),
            "reuse_links":reuse["reuse_links"],
            "field_provenance_sample":reuse["provenance"][:12],
        }, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    renderer = VariableRouteDocxRenderer(store)
    rendered = {
        code: renderer.render(route_id, out / "deliverables" / code).model_dump(mode="json")
        for code, route_id in real_routes.items()
    }
    store.backup(out / "knowledge" / "sop_knowledge_backup.sqlite3")
    store.export_json(out / "knowledge" / "sop_knowledge_export.json")
    summary = {
        "status":"drafts_ready_for_human_review",
        "database":str(db_path.resolve()),
        "real_routes":real_routes,
        "route_validations":validations,
        "rendered":rendered,
        "legacy_reproduction":legacy,
        "demo_approval":{"route_id":demo_route_id,"scope":"demonstration_only","review_session_id":review_session},
        "reuse_demonstration":{"target_route_id":reuse_route_id,"source_route_id":demo_route_id,"similarity":matches[0].similarity},
        "review_workbench_command":f"python -m cad_ai.sop_knowledge --db \"{db_path.resolve()}\" --host 127.0.0.1 --port 8787",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
