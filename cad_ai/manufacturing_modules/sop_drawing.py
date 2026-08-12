from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from .contracts import ModuleDescriptor, ModuleResult


class SopDrawingModuleRequest(BaseModel):
    """Standalone input kept dependency-light for plug-in discovery."""

    product_name: str
    part_no: str
    document_no: str
    drawing_no: str = ""
    station: str = ""
    requirement_text: str = ""
    bom_items: list[dict[str, Any]] = Field(default_factory=list)
    routing_steps: list[dict[str, Any]] = Field(default_factory=list)
    machine_hints: list[str] = Field(default_factory=list)
    route_mode: Literal["process_knowledge", "legacy_two_page"] = "process_knowledge"
    process_family_code: str = ""
    route_scope: Literal["full_manufacturing", "final_inspection_packaging"] = "full_manufacturing"
    product_features: dict[str, str] = Field(default_factory=dict)
    source_profile: dict[str, Any] = Field(default_factory=dict)
    knowledge_db_path: Path = Path("outputs/sop_process_knowledge/sop_knowledge.sqlite3")
    run_id: str | None = None
    out_dir: Path = Path("outputs/sop_agent_api")
    use_model: bool = False
    strict_model: bool = False
    model_base_url: str = "http://127.0.0.1:8081/v1"
    model_name: str = "qwen3.6-35b"
    api_key: str = "local"
    model_timeout_sec: float = 240.0
    model_max_tokens: int = 1900


class SopDrawingModule:
    descriptor = ModuleDescriptor(
        module_id="sop_engineering_drawing",
        display_name="SOP 图生成",
        description="先理解/复用可变工艺路线并写入独立知识库，再生成空图片区的模块化 SOP；旧两页模板仅显式兼容。",
        input_model="cad_ai.manufacturing_modules.sop_drawing.SopDrawingModuleRequest",
        side_effects=["write_output_artifacts"],
    )

    def __init__(self, *, generator: Callable[[Any], Any] | None = None) -> None:
        self._generator = generator

    def generate(self, payload: SopDrawingModuleRequest | dict[str, Any]) -> Any:
        if isinstance(payload, BaseModel) and not isinstance(payload, SopDrawingModuleRequest):
            payload = payload.model_dump(mode="python")
        request = payload if isinstance(payload, SopDrawingModuleRequest) else SopDrawingModuleRequest.model_validate(payload)
        if request.route_mode == "process_knowledge":
            return self._execute_process_knowledge(request)
        return self._generate_legacy(request)

    def _generate_legacy(self, request: SopDrawingModuleRequest) -> Any:
        generator = self._generator
        if generator is None:
            from ..sop_agent import generate_sop_package

            generator = generate_sop_package
        return generator(request.model_dump(mode="python"))

    def execute(self, payload: SopDrawingModuleRequest | dict[str, Any]) -> ModuleResult:
        if isinstance(payload, BaseModel) and not isinstance(payload, SopDrawingModuleRequest):
            payload = payload.model_dump(mode="python")
        request = payload if isinstance(payload, SopDrawingModuleRequest) else SopDrawingModuleRequest.model_validate(payload)
        if request.route_mode == "process_knowledge":
            return self._execute_process_knowledge(request)
        response = self._generate_legacy(request)
        data = response.model_dump(mode="json")
        artifacts = {
            key: str(value)
            for key, value in data.get("artifacts", {}).items()
            if value
        }
        return ModuleResult(
            module_id=self.descriptor.module_id,
            module_version=self.descriptor.version,
            status=response.status,
            data=data,
            artifacts=artifacts,
            warnings=list(response.warnings),
            guardrails={
                "draft_only": True,
                "approval_cells_require_real_signoff": True,
                "site_ie_ehs_oee_yield_trial_data_must_not_be_invented": True,
                "automatic_shopfloor_release": False,
            },
            trace={
                "routing_mode": "legacy_two_page",
                "run_id": response.run_id,
                "generation_sequence": response.generation_sequence,
                "center_flowchart_target": response.center_flowchart_target,
            },
        )

    def _execute_process_knowledge(self, request: SopDrawingModuleRequest) -> ModuleResult:
        from ..sop_knowledge.models import ProductFeatureSet, ProductIdentity, RouteDraft, RouteStepDraft, UnknownItem
        from ..sop_knowledge.pipeline import RouteValidator, SopRouteWorkflow, build_route_sections
        from ..sop_knowledge.renderer import VariableRouteDocxRenderer
        from ..sop_knowledge.store import SopKnowledgeStore

        store = SopKnowledgeStore(request.knowledge_db_path)
        store.initialize()
        family_code = request.process_family_code or _infer_process_family(request)
        family_name = {
            "hdmi_finished_cable_manufacturing": "HDMI 成品线完整制造",
            "active_optical_cable_final_assembly_packaging": "主动光纤 HDMI 成品装配检验包装",
            "manual_routing": "人工输入可变工艺路线",
        }.get(family_code, family_code)
        store.ensure_process_family(family_code, family_name)

        existing = _latest_route(store, request.part_no)
        warnings: list[str] = []
        resumed = False
        if existing and existing["status"] in {"draft", "under_review"}:
            route_id = int(existing["id"])
            resumed = True
            if request.routing_steps:
                warnings.append("检测到同料号未完成草案；本次不覆盖已有工序，请在人工审核工作台逐项修改或创建修订。")
            route_payload = store.get_route(route_id)
            validation = {
                "valid": True,
                "resumed_existing_draft": True,
                "step_count": len(route_payload["steps"]),
                "image_policy": "human_uploaded_and_confirmed_only",
            }
        elif existing and existing["status"] == "approved":
            route_id = store.create_revision(int(existing["id"]), created_by="sop_process_knowledge_module")
            route_payload = store.get_route(route_id)
            validation = {
                "valid": True,
                "created_revision_from_approved": int(existing["id"]),
                "step_count": len(route_payload["steps"]),
                "image_policy": "human_uploaded_and_confirmed_only",
            }
        elif request.routing_steps:
            identity = ProductIdentity(
                product_code=request.part_no,
                product_name=request.product_name,
                aliases=[],
                process_family_code=family_code,
                description=request.requirement_text,
            )
            features = ProductFeatureSet(
                product_code=request.part_no,
                process_family_code=family_code,
                features={"station": request.station or "source_not_supplied", **request.product_features},
            )
            store.upsert_product(identity, features.features)
            steps = [_manual_route_step(item, index, UnknownItem, RouteStepDraft) for index, item in enumerate(request.routing_steps, start=1)]
            draft = RouteDraft(
                product=identity,
                route_name=f"{request.part_no} 人工输入可变工艺路线",
                route_summary="调用方提供工序名称/说明后形成逐字段审核草案；步骤数量保持原样，不补齐、不截断。",
                source_kind="manual",
                steps=steps,
            )
            validation = RouteValidator().validate(draft)
            if not validation["valid"]:
                raise ValueError(f"process route validation failed: {validation}")
            route_id = store.create_route(draft, created_by="sop_process_knowledge_module")
            ingested = {
                "bom_scope": "仅采用本次请求明确提供的BOM条目，不从模板推定单件用量。",
                "bom_items": request.bom_items,
            }
            for section in build_route_sections(draft, features, ingested):
                store.create_route_section(route_id, section, created_by="sop_process_knowledge_module")
            route_payload = store.get_route(route_id)
        else:
            source = {
                "product_name": request.product_name,
                "profile_type": "hdmi_finished_cable" if family_code == "hdmi_finished_cable_manufacturing" else "active_optical_hdmi",
                "process_family_code": family_code,
                "route_scope": request.route_scope,
                "sources": list(request.source_profile.get("sources") or []),
                "features": request.product_features,
                "length_m": request.product_features.get("length_m") or request.source_profile.get("length_m") or "",
                "signal_medium": request.product_features.get("signal_medium") or request.source_profile.get("signal_medium") or "",
                "hdmi_version": request.product_features.get("hdmi_version") or request.source_profile.get("hdmi_version") or "",
                "termination_method": request.product_features.get("termination_method") or request.source_profile.get("termination_method") or "",
                "package": request.source_profile.get("package") or {},
                **{key: value for key, value in request.source_profile.items() if key != "sources"},
            }
            route_id, _draft, validation = SopRouteWorkflow(store).build_draft(request.part_no, {request.part_no: source})
            route_payload = store.get_route(route_id)

        run_name = request.run_id or _safe_name(request.part_no)
        run_dir = Path(request.out_dir) / run_name
        rendered = VariableRouteDocxRenderer(store).render(route_id, run_dir)
        process_flow_path = _write_process_flow_mermaid(
            route_payload,
            run_dir / f"SOP_{_safe_name(request.part_no)}_process_flow.md",
        )
        open_questions = _open_questions(route_payload)
        if not request.source_profile.get("sources"):
            warnings.append("未提供受控源文件；当前详细工序是工艺族草案，所有参数/判据必须逐项人工核对。")
        data = {
            "routing_mode": "process_knowledge",
            "route_id": route_id,
            "route": route_payload["route"],
            "steps": route_payload["steps"],
            "sections": route_payload.get("sections", []),
            "reuse_links": route_payload.get("reuse_links", []),
            "validation": validation,
            "knowledge_db_path": str(Path(request.knowledge_db_path).resolve()),
            "human_review": {
                "workbench_command": f"python -m cad_ai.sop_knowledge --db \"{Path(request.knowledge_db_path).resolve()}\" --host 127.0.0.1 --port 8787",
                "route_api": f"/api/routes/{route_id}",
                "editable_scopes": ["product", "variable_steps", "step_fields", "bom_material", "equipment_fixture", "process_parameter", "quality_control", "packaging_label", "ie_timing", "release_signoff"],
            },
        }
        return ModuleResult(
            module_id=self.descriptor.module_id,
            module_version=self.descriptor.version,
            status="draft_process_knowledge_not_for_release",
            data=data,
            artifacts={
                "document_docx": rendered.docx_path,
                "route_json": rendered.route_json_path,
                "validation_json": rendered.validation_path,
                "process_flow_mermaid": str(process_flow_path.resolve()),
                "knowledge_sqlite": str(Path(request.knowledge_db_path).resolve()),
            },
            open_questions=open_questions,
            warnings=warnings,
            guardrails={
                "draft_only": True,
                "images_auto_fill": False,
                "variable_step_count_preserved": True,
                "approved_route_reuse_only": True,
                "human_field_confirmation_required": True,
                "automatic_shopfloor_release": False,
            },
            trace={
                "routing_mode": "process_knowledge",
                "route_id": route_id,
                "route_version": route_payload["route"]["version"],
                "source_kind": route_payload["route"]["source_kind"],
                "step_count": len(route_payload["steps"]),
                "flow_operation_nodes": len(route_payload["steps"]),
                "flow_quality_gate_nodes": len(route_payload["steps"]),
                "resumed_existing_draft": resumed,
                "image_policy": rendered.image_policy,
            },
        )


def _infer_process_family(request: SopDrawingModuleRequest) -> str:
    text = " ".join([request.product_name, request.part_no, request.requirement_text]).upper()
    if "HDMI" in text:
        return "hdmi_finished_cable_manufacturing"
    if request.routing_steps:
        return "manual_routing"
    raise ValueError("process_family_code is required when the product family cannot be inferred")


def _write_process_flow_mermaid(route_payload: dict[str, Any], path: Path) -> Path:
    """Write an editable detailed flow; it is not a work-instruction photo."""
    route = route_payload["route"]
    steps = route_payload["steps"]
    lines = [
        f"# {route['product_code']} 详细工艺流程图（草案）",
        "",
        "> 路线真源为 SQLite；本图仅用于人工审核与导航，不代表生产批准。",
        "",
        "```mermaid",
        "flowchart TD",
        f'    START(["开始：{_mermaid_text(route["product_code"])}"])',
        '    HOLD["不合格隔离区 / 人工评审"]',
        '    END(["草案结束：等待全部人工闸门关闭"])',
    ]
    previous_gate = "START"
    operation_nodes: list[str] = []
    gate_nodes: list[str] = []
    exception_nodes: list[str] = []
    for index, step in enumerate(steps, start=1):
        operation_id = f"OP{index:02d}"
        gate_id = f"QC{index:02d}"
        exception_id = f"EX{index:02d}"
        operation_nodes.append(operation_id)
        gate_nodes.append(gate_id)
        exception_nodes.append(exception_id)
        check = _first_text(step.get("quality_check_json"), "按受控检验规范检查")
        exception = _first_text(step.get("exception_json"), "停止流转并人工判定")
        hierarchy = "子步骤" if step.get("parent_step_id") else "顶层工序"
        lines.extend([
            f'    {operation_id}["{_mermaid_text(step["step_code"])} · {_mermaid_text(step["title"])}\\n{hierarchy}"]',
            f'    {gate_id}{{"{_mermaid_text(check, 54)}"}}',
            f'    {exception_id}["{_mermaid_text(exception, 54)}"]',
            f"    {previous_gate} -- 合格/进入 --> {operation_id}",
            f"    {operation_id} --> {gate_id}",
            f"    {gate_id} -- 不合格 --> {exception_id}",
            f"    {exception_id} --> HOLD",
        ])
        previous_gate = gate_id
    lines.extend([
        f"    {previous_gate} -- 全部确认 --> END",
        "    classDef operation fill:#DCE6F1,stroke:#1F4E78,color:#1F1F1F;",
        "    classDef gate fill:#FFF2CC,stroke:#B7791F,color:#1F1F1F;",
        "    classDef exception fill:#FCE8E6,stroke:#B42318,color:#1F1F1F;",
        f"    class {','.join(operation_nodes)} operation;",
        f"    class {','.join(gate_nodes)} gate;",
        f"    class {','.join(exception_nodes + ['HOLD'])} exception;",
        "```",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _first_text(value: Any, fallback: str) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    if value:
        return str(value)
    return fallback


def _mermaid_text(value: Any, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace('"', "'").replace("[", "（").replace("]", "）").replace("{", "（").replace("}", "）")


def _latest_route(store: Any, product_code: str) -> dict[str, Any] | None:
    with store.connect() as connection:
        row = connection.execute(
            """
            SELECT r.* FROM product_route r
            JOIN product p ON p.id = r.product_id
            WHERE p.product_code=?
            ORDER BY r.version DESC LIMIT 1
            """,
            (product_code,),
        ).fetchone()
    return dict(row) if row else None


def _manual_route_step(raw: dict[str, Any], index: int, unknown_model: Any, step_model: Any) -> Any:
    title = str(raw.get("title") or raw.get("name") or f"工序{index}")
    action = str(raw.get("action") or raw.get("description") or f"执行“{title}”，核对输入、完成动作并记录原始结果。")
    unknowns = raw.get("unknowns") or [
        unknown_model(
            field_name="manual_step_evidence",
            reason=f"工序“{title}”由调用方提供，但尚未绑定受控工艺卡、设备参数和现场确认记录。",
            owner_role="工艺工程师/品质工程师",
            required_evidence="受控工艺卡、设备/治具记录、检查规范和人工审核决定",
        )
    ]
    return step_model(
        step_code=str(raw.get("step_code") or f"OP-{index:02d}"),
        sequence_no=float(raw.get("sequence_no") or index),
        parent_step_code=raw.get("parent_step_code"),
        title=title,
        action=action,
        why=str(raw.get("why") or f"确保“{title}”的输入、执行、检查和输出可追溯。"),
        inputs=list(raw.get("inputs") or ["调用方提供的工序输入", "受控工艺资料"]),
        materials=list(raw.get("materials") or []),
        tool_equipment=list(raw.get("tool_equipment") or []),
        fixtures=list(raw.get("fixtures") or []),
        parameters=list(raw.get("parameters") or []),
        method=list(raw.get("method") or ["核对本工序输入和版本。", f"执行“{title}”具体动作。", "完成检查并记录原始结果。"]),
        quality_check=list(raw.get("quality_check") or [f"检查“{title}”动作完成、记录完整且无未处置异常。"]),
        acceptance_criteria=list(raw.get("acceptance_criteria") or ["满足受控工艺卡和检查规范；资料未绑定前保持草案。"]),
        safety=list(raw.get("safety") or ["按现场风险评估和设备作业规范执行。"]),
        record_output=list(raw.get("record_output") or [f"{title}原始记录"]),
        exception=list(raw.get("exception") or ["异常时隔离并提交人工评审，不自动关闭。"]),
        unknowns=unknowns,
    )


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "sop-route"


def _open_questions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for step in payload.get("steps", []):
        for item in step.get("unknowns_json", []):
            questions.append({
                "scope": "step",
                "step_code": step["step_code"],
                "field": item["field_name"],
                "reason": item["reason"],
                "owner_role": item["owner_role"],
                "required_evidence": item["required_evidence"],
                "blocking": item.get("blocking", True),
            })
    return questions
