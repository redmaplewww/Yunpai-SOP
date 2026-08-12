from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from docx import Document

from cad_ai.sop_visual_template import (
    IE_TIME_STUDY_FIELDS,
    SOP_FLOWCHART_SHAPE_POLICY,
    _build_sop_word_document,
    _render_center_flowchart_shape_image,
    _write_word_format_check_json,
    build_process_flow_page,
    build_work_instruction_page,
    classify_sop_flow_node_shape,
)


GENERATION_SEQUENCE = [
    "parse_requirement",
    "fill_word_tables",
    "build_structured_flowchart",
    "render_center_flowchart_png",
    "insert_png_into_process_flow_body_cell",
    "validate_docx",
]


SAMPLE_REQUIREMENTS: list[dict[str, Any]] = [
    {
        "slug": "bluetooth_mouse_assembly",
        "product_name": "蓝牙鼠标装配 DEMO",
        "part_no": "DEMO-MOUSE-001",
        "document_no": "SOP-DEMO-MOUSE-001",
        "drawing_no": "DWG-DEMO-MOUSE-001",
        "station": "总装测试",
        "operation_order": "来料确认 -> PCBA装配 -> 上下盖组装 -> 功能测试 -> 外观检查 -> 包装入库",
        "machine_models": ["MAT-DEMO", "ESD-BENCH-DEMO", "SCREW-DRIVER-DEMO", "BT-TEST-DEMO", "VISUAL-STD-DEMO", "PACK-BENCH-DEMO"],
        "steps": [
            ("来料确认", "核对外壳、PCBA、微动、滚轮和螺丝包，缺料进入待确认清单。", "materials"),
            ("PCBA装配", "将 PCBA 放入下盖定位柱，确认按键和滚轮活动顺畅。", "assembly"),
            ("上下盖组装", "合盖后检查卡扣闭合状态，不得强压变形。", "assembly"),
            ("功能测试", "连接测试治具确认蓝牙配对、左右键、滚轮和指示灯。", "test"),
            ("外观检查", "检查表面划伤、毛边、色差和铭牌方向。", "inspect"),
            ("包装入库", "贴标、装袋、装盒并放入待入库区。", "pack"),
        ],
        "flow_nodes": [
            ("来料确认", "inspection"),
            ("PCBA装配", "process"),
            ("上下盖组装", "process"),
            ("锁螺丝", "process"),
            ("功能测试", "test"),
            ("合格判定", "quality"),
            ("外观检查", "inspection"),
            ("清洁", "process"),
            ("包装入库", "process"),
        ],
    },
    {
        "slug": "usb_c_cable_packaging",
        "product_name": "USB-C 数据线包装 DEMO",
        "part_no": "DEMO-USBC-002",
        "document_no": "SOP-DEMO-USBC-002",
        "drawing_no": "DWG-DEMO-USBC-002",
        "station": "包装工站",
        "operation_order": "备料 -> 外观检查 -> 盘线 -> 扎线 -> 装袋 -> 装箱",
        "machine_models": ["MAT-DEMO", "VISUAL-STD-DEMO", "COIL-JIG-DEMO", "TIE-JIG-DEMO", "PACK-BENCH-DEMO", "CARTON-BENCH-DEMO"],
        "steps": [
            ("备料", "确认数据线、扎带、PE袋、标签和箱唛数量与工单一致。", "materials"),
            ("外观检查", "检查接头、电镀面、线身和端口无破损、脏污、露铜。", "inspect"),
            ("盘线", "按标准圈径盘线，线身自然不扭结。", "coil"),
            ("扎线", "用扎带固定线圈，扎带位置居中且不可勒伤线材。", "tie"),
            ("装袋", "数据线与标签同向放入 PE 袋，袋口方向一致。", "bag"),
            ("装箱", "按装箱数量摆放，箱唛与工单一致。", "carton"),
        ],
        "flow_nodes": [
            ("备料", "process"),
            ("外观检查", "inspection"),
            ("盘线", "process"),
            ("扎线", "process"),
            ("标签扫描检查", "inspection"),
            ("装袋", "process"),
            ("装箱入库", "process"),
        ],
    },
    {
        "slug": "sensor_module_assembly",
        "product_name": "传感器模块装配 DEMO",
        "part_no": "DEMO-SENSOR-003",
        "document_no": "SOP-DEMO-SENSOR-003",
        "drawing_no": "DWG-DEMO-SENSOR-003",
        "station": "模块装配测试",
        "operation_order": "来料检验 -> 焊点检查 -> 外壳装配 -> 标定测试 -> 清洁包装",
        "machine_models": ["IQC-DEMO", "AOI-DEMO", "PRESS-JIG-DEMO", "CAL-TEST-DEMO", "PACK-BENCH-DEMO", "LABEL-DEMO"],
        "steps": [
            ("来料检验", "核对传感器板、外壳、线束和标签版本，异常隔离。", "inspect"),
            ("焊点检查", "确认焊点饱满、无连锡、虚焊和污染。", "inspect"),
            ("外壳装配", "按定位方向装入外壳，避免压伤线束。", "assembly"),
            ("标定测试", "接入测试治具进行零点、量程和通讯测试。", "test"),
            ("清洁", "清洁外壳表面和端子区域，不得残留异物。", "clean"),
            ("包装", "贴版本标签并装入防静电袋。", "pack"),
        ],
        "flow_nodes": [
            ("来料检验", "inspection"),
            ("焊点检查", "inspection"),
            ("外壳装配", "process"),
            ("标定测试", "test"),
            ("通讯测试", "test"),
            ("合格判定", "quality"),
            ("清洁", "process"),
            ("包装入库", "process"),
        ],
    },
    {
        "slug": "power_adapter_assembly",
        "product_name": "小型电源适配器装配 DEMO",
        "part_no": "DEMO-ADAPTER-004",
        "document_no": "SOP-DEMO-ADAPTER-004",
        "drawing_no": "DWG-DEMO-ADAPTER-004",
        "station": "适配器总装",
        "operation_order": "外壳检查 -> PCBA入壳 -> 超声焊接 -> 电测 -> 老化抽检 -> 包装",
        "machine_models": ["VISUAL-STD-DEMO", "ESD-BENCH-DEMO", "ULTRASONIC-DEMO", "ATE-DEMO", "AGING-RACK-DEMO", "PACK-BENCH-DEMO"],
        "steps": [
            ("外壳检查", "检查外壳无变形、缩水、裂纹和明显色差。", "inspect"),
            ("PCBA入壳", "按定位方向放入 PCBA，导线和端子不得受压。", "assembly"),
            ("超声焊接", "使用 DEMO 超声焊机示例参数，正式参数需工程锁定。", "weld"),
            ("电测", "进行空载电压、负载电压和保护功能测试。", "test"),
            ("老化抽检", "按抽检规则记录老化结果，演示样例不代表现场记录。", "test"),
            ("包装", "贴标签、放说明书、装盒并入待检区。", "pack"),
        ],
        "flow_nodes": [
            ("外壳检查", "inspection"),
            ("PCBA入壳", "process"),
            ("超声焊接", "process"),
            ("电测", "test"),
            ("老化抽检", "test"),
            ("合格判定", "quality"),
            ("贴标包装", "process"),
        ],
    },
    {
        "slug": "plastic_shell_inspection_pack",
        "product_name": "塑胶外壳检包 DEMO",
        "part_no": "DEMO-SHELL-005",
        "document_no": "SOP-DEMO-SHELL-005",
        "drawing_no": "DWG-DEMO-SHELL-005",
        "station": "外观检包",
        "operation_order": "取件 -> 尺寸量测 -> 外观检查 -> 修毛边 -> 清洁 -> 包装",
        "machine_models": ["PICK-BENCH-DEMO", "CALIPER-DEMO", "VISUAL-STD-DEMO", "TRIM-KNIFE-DEMO", "CLEAN-BENCH-DEMO", "PACK-BENCH-DEMO"],
        "steps": [
            ("取件", "从周转箱取出外壳，确认批次和箱标一致。", "materials"),
            ("尺寸量测", "用 DEMO 量具检查关键尺寸，正式数据需现场实测。", "measure"),
            ("外观检查", "检查披锋、刮伤、缩水、黑点和色差。", "inspect"),
            ("修毛边", "沿分型线轻修毛边，不得伤及外观面。", "trim"),
            ("清洁", "清洁粉尘和碎屑，确认无异物残留。", "clean"),
            ("包装", "按数量分层摆放并贴 DEMO 箱标。", "pack"),
        ],
        "flow_nodes": [
            ("取件", "process"),
            ("尺寸量测", "measurement"),
            ("外观检查", "inspection"),
            ("修毛边", "process"),
            ("复检", "inspection"),
            ("清洁", "process"),
            ("包装入库", "process"),
        ],
    },
]


def generate_sop_batch_samples(out_dir: str | Path, *, sample_count: int = 5) -> dict[str, Any]:
    if sample_count < 3 or sample_count > 5:
        raise ValueError("sample_count must be between 3 and 5")

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    selected_requirements = SAMPLE_REQUIREMENTS[:sample_count]
    sample_results: list[dict[str, str]] = []

    for requirement in selected_requirements:
        sample_results.append(_generate_one_sample(output, requirement))

    manifest_path = output / "sop_batch_closing_tests_manifest.json"
    manifest = {
        "status": "demo_not_for_release",
        "sample_count": len(sample_results),
        "shape_policy": SOP_FLOWCHART_SHAPE_POLICY,
        "generation_sequence": GENERATION_SEQUENCE,
        "samples": sample_results,
        "ai_boundary": "demo_not_for_release; no real site IE time, signoff, equipment status, EHS record, trial result, or released SOP conclusion fabricated",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"manifest_json": str(manifest_path), "samples": sample_results}


def _generate_one_sample(root: Path, requirement: dict[str, Any]) -> dict[str, str]:
    sample_dir = root / str(requirement["slug"])
    sample_dir.mkdir(parents=True, exist_ok=True)

    flow_page, work_page = _fill_word_tables_from_requirement(requirement)
    center_flowchart = _build_structured_center_flowchart(requirement)
    flow_page["render_center_flowchart"] = True
    flow_page["center_flowchart_style"] = "pdf_reference_shape_blocks"
    flow_page["center_flowchart"] = center_flowchart

    preview_png = sample_dir / f"{requirement['slug']}_center_flowchart.png"
    preview_png.write_bytes(_render_center_flowchart_shape_image(flow_page))

    document_docx = sample_dir / f"{requirement['slug']}_sop.docx"
    document = _build_sop_word_document(flow_page, work_page)
    document.save(document_docx)

    format_check_json = sample_dir / f"{requirement['slug']}_format_check.json"
    _write_word_format_check_json(format_check_json, flow_page, work_page)

    validation = _validate_sample_docx(document_docx)
    manifest_json = sample_dir / f"{requirement['slug']}_manifest.json"
    sample_manifest = {
        "status": "demo_not_for_release",
        "slug": requirement["slug"],
        "product_name": requirement["product_name"],
        "part_no": requirement["part_no"],
        "document_no": requirement["document_no"],
        "station": requirement["station"],
        "shape_policy": SOP_FLOWCHART_SHAPE_POLICY,
        "generation_sequence": GENERATION_SEQUENCE,
        "tables_filled_before_flowchart": True,
        "center_flowchart_target": "process_flow_body_table_cell_0_0",
        "center_flowchart_style": "pdf_reference_shape_blocks",
        "center_flowchart": center_flowchart,
        "word_tables_filled": {
            "process_flow_header": True,
            "process_flow_ie_time": True,
            "work_instruction_header": True,
            "work_instruction_body": True,
            "work_instruction_ie_time": True,
            "bottom_signoff_blank": True,
        },
        "validation": validation,
        "artifacts": {
            "document_docx": document_docx.name,
            "preview_png": preview_png.name,
            "format_check_json": format_check_json.name,
        },
        "ai_boundary": "demo_not_for_release; generated content is format and workflow test data only",
    }
    manifest_json.write_text(json.dumps(sample_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "slug": str(requirement["slug"]),
        "document_docx": str(document_docx),
        "preview_png": str(preview_png),
        "format_check_json": str(format_check_json),
        "manifest_json": str(manifest_json),
    }


def _fill_word_tables_from_requirement(requirement: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    operation_names = [str(name) for name, _kind in requirement["flow_nodes"]]
    flow_page = build_process_flow_page(
        product_name=str(requirement["product_name"]),
        part_no=str(requirement["part_no"]),
        document_no=str(requirement["document_no"]),
        drawing_no=str(requirement["drawing_no"]),
        operations=operation_names,
    )
    work_page = build_work_instruction_page(
        product_name=str(requirement["product_name"]),
        part_no=str(requirement["part_no"]),
        station=str(requirement["station"]),
        document_no=str(requirement["document_no"]),
        drawing_no=str(requirement["drawing_no"]),
        version="DEMO",
        page_no=1,
        page_total=1,
    )

    work_page["operation_order"] = str(requirement["operation_order"])
    work_page["step_slots"] = [
        _sample_step(slot_no, title, description, visual_type)
        for slot_no, (title, description, visual_type) in enumerate(requirement["steps"], start=1)
    ]
    work_page["side_sections"] = _sample_side_sections(requirement)
    flow_page["ie_time_study"] = _process_ie_time_study(requirement)
    work_page["ie_time_study"] = _work_ie_time_study(requirement)
    return flow_page, work_page


def _build_structured_center_flowchart(requirement: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for index, (name, node_type) in enumerate(requirement["flow_nodes"], start=1):
        node = {
            "seq": index,
            "id": f"OP{index:02d}",
            "label": f"工序{index}",
            "name": name,
            "type": node_type,
            "station": requirement["station"],
            "note": "demo_not_for_release",
        }
        node["shape"] = classify_sop_flow_node_shape(node)
        nodes.append(node)

    edges = [
        {"from": nodes[index]["id"], "to": nodes[index + 1]["id"], "label": "next"}
        for index in range(len(nodes) - 1)
    ]
    quality_node = next((node for node in nodes if classify_sop_flow_node_shape(node) == "diamond"), None)
    rework_target = next((node for node in nodes if node["shape"] == "ellipse"), None)
    if quality_node and rework_target and quality_node["id"] != rework_target["id"]:
        edges.append({"from": quality_node["id"], "to": rework_target["id"], "label": "不合格返工"})
    return {
        "flowchart_title": f"{requirement['product_name']} 中心工艺流程",
        "nodes": nodes,
        "edges": edges,
    }


def _sample_step(slot_no: int, title: str, description: str, visual_type: str) -> dict[str, Any]:
    return {
        "slot_no": slot_no,
        "image_placeholder": False,
        "text_placeholder": f"{slot_no}. {title}: {description}",
        "visual": {"type": visual_type, "title": title},
    }


def _sample_side_sections(requirement: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "title": "浣滀笟鏍囧噯",
            "lines": [
                "1. 按需求/BOM/工艺路线生成草案，正式参数待工程确认。",
                "2. 异常品隔离并记录，不自动关闭异常。",
                "3. 本样例仅用于 SOP 格式测试。",
            ],
        },
        {
            "title": "璁惧/宸ュ叿",
            "lines": [f"{index}. {model}" for index, model in enumerate(requirement["machine_models"], start=1)],
        },
        {"title": "杈呭姪鏉愭枡", "lines": ["BOM物料按需求草案列示", "标签/包装材料按工单确认"]},
        {
            "title": "娉ㄦ剰浜嬮」",
            "lines": [
                "1. DEMO 工时不可作为现场发布依据。",
                "2. 现场图片、设备状态、EHS 和签核需人工或系统记录。",
            ],
        },
        {"title": "鍙樻洿鍐呭", "lines": ["DEMO / 格式回归测试 / 未发布"]},
        {"title": "鐗╂枡琛?", "lines": [f"DEMO / {requirement['part_no']} / 需求导入后待 BOM 锁定"]},
    ]


def _process_ie_time_study(requirement: dict[str, Any]) -> dict[str, Any]:
    row = _ie_row(
        action="流程工时汇总",
        machine_model="; ".join(str(item) for item in requirement["machine_models"][:3]) + "; ...",
        average="",
        standard="详见工站页",
    )
    return {
        "title": "IE工时记录",
        "fields": list(IE_TIME_STUDY_FIELDS),
        "rows": [row],
        "policy": {"release_requirement": "site_measured_or_human_locked"},
    }


def _work_ie_time_study(requirement: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for index, (title, _description, _visual_type) in enumerate(requirement["steps"], start=1):
        machine_model = str(requirement["machine_models"][index - 1])
        average = f"{4 + index * 1.5:.1f}"
        standard = f"{(4 + index * 1.5) * 1.10:.1f}"
        rows.append(_ie_row(action=title, machine_model=machine_model, average=average, standard=standard))
    return {
        "title": "IE工时记录",
        "fields": list(IE_TIME_STUDY_FIELDS),
        "rows": rows,
        "policy": {
            "measurement_basis": "demo estimate only",
            "release_requirement": "site_measured_or_human_locked",
        },
    }


def _ie_row(action: str, machine_model: str, average: str, standard: str) -> dict[str, str]:
    fields = list(IE_TIME_STUDY_FIELDS)
    return {
        fields[0]: action,
        fields[1]: machine_model,
        fields[2]: "DEMO秒表估算",
        fields[3]: "3",
        fields[4]: average,
        fields[5]: "1.00",
        fields[6]: "10%",
        fields[7]: standard,
        fields[8]: "demo_not_for_release",
        fields[9]: "MES实绩/IE复测后动态调整",
    }


def _validate_sample_docx(path: Path) -> dict[str, Any]:
    document = Document(path)
    with zipfile.ZipFile(path) as package:
        names = set(package.namelist())
        document_xml = package.read("word/document.xml").decode("utf-8")
    return {
        "sections": len(document.sections),
        "top_level_tables": len(document.tables),
        "has_png_media": any(name.startswith("word/media/") and name.endswith(".png") for name in names),
        "has_picture_reference": "pic:pic" in document_xml,
        "has_svg": "<svg" in document_xml.lower(),
        "has_vml_shape": "<v:shape" in document_xml.lower(),
    }


def main() -> None:
    result = generate_sop_batch_samples(Path("outputs/sop_batch_closing_tests"), sample_count=5)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
