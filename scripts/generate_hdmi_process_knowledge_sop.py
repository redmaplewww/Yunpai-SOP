from __future__ import annotations

import argparse
import json
from pathlib import Path

from cad_ai.manufacturing_modules.sop_drawing import SopDrawingModule


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成工艺知识优先、图片留白的 HDMI 成品线 SOP 草案")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("outputs/deliverables/hdmi_process_knowledge_sop_20260812"),
    )
    parser.add_argument("--product-code", default="HDMI-DRAFT-001")
    parser.add_argument("--product-name", default="HDMI 成品线（铜缆，具体规格待人工确认）")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root.resolve()
    payload = {
        "product_name": args.product_name,
        "part_no": args.product_code,
        "document_no": "SOP-DRAFT-HDMI-PROCESS-001",
        "requirement_text": (
            "建立完整制造、检验与包装工艺路线；每个工序可独立人工审核；"
            "图片全部留空；不得填充或截断工序。"
        ),
        "route_mode": "process_knowledge",
        "process_family_code": "hdmi_finished_cable_manufacturing",
        "route_scope": "full_manufacturing",
        "product_features": {
            "product_type": "HDMI finished cable",
            "conductor_medium": "copper",
            "connector_ends": "HDMI male to HDMI male",
            "image_policy": "blank_pending_human_selection",
        },
        "knowledge_db_path": root / "knowledge" / "sop_knowledge.sqlite3",
        "out_dir": root / "deliverables",
        "run_id": args.product_code,
    }
    result = SopDrawingModule().execute(payload)
    result_path = root / "generation_result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(json.dumps({
        "status": result.status,
        "route_id": result.data["route_id"],
        "step_count": result.trace["step_count"],
        "artifacts": result.artifacts,
        "result": str(result_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
