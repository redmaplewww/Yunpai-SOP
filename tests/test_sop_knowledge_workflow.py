from __future__ import annotations

import json
import base64
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from cad_ai.sop_knowledge.models import ProductIdentity, RouteDraft, RouteSectionDraft, RouteStepDraft, UnknownItem
from cad_ai.sop_knowledge.pipeline import RouteValidator, SopRouteWorkflow
from cad_ai.sop_knowledge.renderer import VariableRouteDocxRenderer
from cad_ai.sop_knowledge.store import SopKnowledgeStore
from cad_ai.sop_knowledge.web import create_builtin_server
from cad_ai.sop_agent import SopGenerateRequest, SopRoutingStep, _build_structured_sop_data


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def make_identity(code: str = "TEST-A") -> ProductIdentity:
    return ProductIdentity(
        product_code=code,
        product_name=f"测试产品 {code}",
        aliases=[f"ALIAS-{code}"],
        process_family_code="test_family",
    )


def make_step(index: int, *, parent: str | None = None, action: str | None = None) -> RouteStepDraft:
    return RouteStepDraft(
        step_code=f"S-{index}",
        sequence_no=float(index),
        parent_step_code=parent,
        title=f"实际工序 {index}",
        action=action or f"执行第 {index} 项可验证操作并记录原始值。",
        why="确保该工序的输入、动作和输出可追溯。",
        inputs=["受控输入"],
        materials=["已核对物料"],
        tool_equipment=["资料未给出型号的设备"],
        fixtures=[],
        parameters=[{"name": "目标", "value": str(index), "source": "测试证据"}],
        method=["核对输入。", "执行动作。", "记录结果。"],
        quality_check=["检查动作完成和记录完整。"],
        acceptance_criteria=["结果与受控要求一致。"],
        safety=["执行现场风险识别。"],
        record_output=["工序原始记录"],
        exception=["异常时隔离并提交人工评审。"],
        unknowns=[UnknownItem(
            field_name="equipment_model",
            reason="输入资料没有受控设备型号，不能从模板推定。",
            owner_role="工艺工程师",
            required_evidence="现场设备卡与校准台账",
        )],
    )


def make_route(identity: ProductIdentity, count: int = 7, *, with_child: bool = False) -> RouteDraft:
    steps = [make_step(index) for index in range(1, count + 1)]
    if with_child:
        steps[1] = make_step(2, parent="S-1")
    return RouteDraft(
        product=identity,
        route_name=f"{identity.product_code} 可变工序路线",
        route_summary="用于验证工艺路线知识库行为。",
        source_kind="manual",
        steps=steps,
    )


SECTION_TYPES = (
    "product_identity", "bom_material", "equipment_fixture", "process_parameter",
    "quality_control", "packaging_label", "ie_timing", "release_signoff",
)


class SopKnowledgeWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SopKnowledgeStore(self.root / "knowledge.sqlite3")
        self.store.initialize()
        self.store.ensure_process_family("test_family", "测试工艺族")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_route(self, code: str = "TEST-A", count: int = 7, *, with_child: bool = False) -> int:
        identity = make_identity(code)
        self.store.upsert_product(identity, {"class": "connector", "feature": "same"})
        route_id = self.store.create_route(make_route(identity, count, with_child=with_child))
        for section_type in SECTION_TYPES:
            self.store.create_route_section(
                route_id,
                RouteSectionDraft(
                    section_type=section_type,
                    content={"product_code": code, "section": section_type},
                ),
            )
        return route_id

    def make_formally_approvable(self, route_id: int, reviewer: str = "human_reviewer") -> int:
        payload = self.store.get_route(route_id)
        for step in payload["steps"]:
            self.store.update_step_field(step["id"], "unknowns", [], reviewer=reviewer)
            self.store.update_step_field(step["id"], "review_state", "confirmed", reviewer=reviewer)
        payload = self.store.get_route(route_id)
        for section in payload["sections"]:
            self.store.revise_route_section(
                section["id"],
                content=section["content_json"],
                review_state="confirmed",
                reviewer_comment="人工已核对该section全部字段",
                sources=section["source_json"],
                conflicts=[],
                unknowns=[],
                reviewer=reviewer,
                decision="confirmed",
            )
        return self.store.create_review_session(route_id, reviewer)

    def approve(self, route_id: int, scope: str = "formal_production") -> int:
        session = (
            self.make_formally_approvable(route_id)
            if scope == "formal_production"
            else self.store.create_review_session(route_id, "human_reviewer")
        )
        self.store.submit_review(session)
        return self.store.approve(
            session,
            approved_by="human_reviewer",
            approval_scope=scope,
            confirmation_token=(self.store.formal_confirmation_token(self.store.get_route(route_id)["route"]["product_code"]) if scope == "formal_production" else None),
        )

    def test_legacy_ie_timing_section_exposes_blank_price_and_headcount_fields(self) -> None:
        route_id = self.add_route()

        section = next(
            item for item in self.store.get_route(route_id)["sections"]
            if item["section_type"] == "ie_timing"
        )

        self.assertEqual(section["content_json"]["单价"], "")
        self.assertEqual(section["content_json"]["人数"], "")

    def test_approved_only_reuse_excludes_draft_and_demo_by_default(self) -> None:
        draft_id = self.add_route("TEST-DRAFT")
        demo_id = self.add_route("TEST-DEMO")
        self.approve(demo_id, "demonstration_only")
        target = make_identity("TEST-TARGET")
        self.store.upsert_product(target, {"class": "connector", "feature": "same"})
        default_matches = self.store.retrieve_approved(target.product_code, {"class": "connector", "feature": "same"})
        demo_matches = self.store.retrieve_approved(target.product_code, {"class": "connector", "feature": "same"}, allow_demonstration=True)
        self.assertEqual(default_matches, [])
        self.assertEqual([item.source_route_id for item in demo_matches], [demo_id])
        self.assertNotIn(draft_id, [item.source_route_id for item in demo_matches])

    def test_legacy_router_reproduction_proves_fill_and_truncation_to_six(self) -> None:
        def output_count(count: int) -> int:
            request = SopGenerateRequest(
                product_name="复现产品", part_no="REPRO", document_no="REPRO-001",
                routing_steps=[SopRoutingStep(name=f"真实工序{index}") for index in range(1, count + 1)],
            )
            return len(_build_structured_sop_data(request)["steps"])

        self.assertEqual(output_count(3), 6)
        self.assertEqual(output_count(8), 6)

    def test_approved_version_is_immutable_and_revision_is_editable(self) -> None:
        route_id = self.add_route()
        self.approve(route_id)
        step_id = self.store.get_route(route_id)["steps"][0]["id"]
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.update_step_field(step_id, "action", "试图修改已批准内容", reviewer="reviewer")
        approved_section = self.store.get_route(route_id)["sections"][0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.revise_route_section(
                approved_section["id"], content=approved_section["content_json"], review_state="confirmed",
                reviewer_comment="试图修改已批准section", sources=[], conflicts=[], unknowns=[],
                reviewer="reviewer", decision="confirmed",
            )
        revision = self.store.create_revision(route_id, created_by="revision_test")
        payload = self.store.get_route(revision)
        self.assertEqual(payload["route"]["version"], 2)
        self.assertEqual(len(payload["sections"]), 8)
        self.assertTrue(all(item["review_state"] == "unreviewed" for item in payload["sections"]))
        self.store.update_step_field(payload["steps"][0]["id"], "action", "新修订允许人工修改。", reviewer="reviewer")

    def test_rejected_route_never_enters_approved_index(self) -> None:
        route_id = self.add_route("TEST-REJECT")
        session = self.store.create_review_session(route_id, "human_reviewer")
        self.store.reject(session, reviewer="human_reviewer", comment="工序依据不足")
        target = make_identity("TEST-REJECT-TARGET")
        self.store.upsert_product(target, {"class": "connector", "feature": "same"})
        self.assertEqual(self.store.retrieve_approved(target.product_code, {"class": "connector", "feature": "same"}), [])
        self.assertEqual(self.store.get_route(route_id)["route"]["status"], "deprecated")

    def test_similarity_clone_records_reuse_link_and_field_provenance(self) -> None:
        source_id = self.add_route("TEST-SOURCE")
        self.approve(source_id)
        target = make_identity("TEST-SIMILAR")
        self.store.upsert_product(target, {"class": "connector", "feature": "same"})
        match = self.store.retrieve_approved(target.product_code, {"class": "connector", "feature": "same"})[0]
        cloned_id = self.store.clone_approved_route_as_draft(
            source_id, target, similarity=match.similarity, match_basis=match.match_basis
        )
        cloned = self.store.get_route(cloned_id)
        self.assertEqual(cloned["route"]["source_kind"], "similar_approved")
        self.assertEqual(cloned["reuse_links"][0]["source_route_id"], source_id)
        self.assertTrue(cloned["provenance"])
        self.assertTrue(any("reuse" in item["note"] for item in cloned["provenance"]))

    def test_variable_step_count_and_parent_child_are_preserved(self) -> None:
        route_id = self.add_route("TEST-HIERARCHY", 9, with_child=True)
        payload = self.store.get_route(route_id)
        self.assertEqual(len(payload["steps"]), 9)
        parent = next(item for item in payload["steps"] if item["step_code"] == "S-1")
        child = next(item for item in payload["steps"] if item["step_code"] == "S-2")
        self.assertEqual(child["parent_step_id"], parent["id"])

    def test_hdmi_family_builds_detailed_full_route_with_blank_images(self) -> None:
        self.store.ensure_process_family("hdmi_finished_cable_manufacturing", "HDMI成品线完整制造")
        route_id, draft, validation = SopRouteWorkflow(self.store).build_draft(
            "HDMI-DRAFT-001",
            {
                "HDMI-DRAFT-001": {
                    "product_name": "HDMI 成品线（规格由人工审核）",
                    "profile_type": "hdmi_finished_cable",
                    "process_family_code": "hdmi_finished_cable_manufacturing",
                    "route_scope": "full_manufacturing",
                    "sources": [],
                }
            },
        )
        payload = self.store.get_route(route_id)
        self.assertTrue(validation["valid"])
        self.assertEqual(len(draft.steps), 17)
        self.assertEqual(len(payload["steps"]), 17)
        self.assertEqual(len(payload["sections"]), 8)
        self.assertEqual(
            [step["title"] for step in payload["steps"] if step["parent_step_id"] is None],
            [
                "工单、料号与受控版本核对", "物料齐套与批次防混", "裁线与长度补偿", "外被剥除与端部定长",
                "屏蔽层、排流线与接地准备", "芯线排序、校直与剥皮", "连接器端接总成", "屏蔽壳连接与端部绝缘防护",
                "连接器壳体装配或成型", "19针导通、开短路与屏蔽电测", "音视频与连接稳定性功能测试",
                "成品尺寸、拉力与外观终检", "盘线、扎带与端头防护", "装袋、贴标与装箱复核",
                "记录复核、异常隔离与人工放行闸门",
            ],
        )
        rendered = VariableRouteDocxRenderer(self.store).render(route_id, self.root / "hdmi-rendered")
        with zipfile.ZipFile(rendered.docx_path) as archive:
            self.assertFalse([name for name in archive.namelist() if name.startswith("word/media/")])
        self.assertEqual(rendered.page_count_expected_from_route, 18)

    def test_human_field_edit_creates_review_decision(self) -> None:
        route_id = self.add_route("TEST-EDIT")
        session = self.store.create_review_session(route_id, "reviewer")
        step_id = self.store.get_route(route_id)["steps"][0]["id"]
        self.store.update_step_field(step_id, "action", "人工逐字段修改后的可执行动作。", reviewer="reviewer", comment="已核对证据")
        with self.store.connect() as connection:
            decision = connection.execute("SELECT * FROM review_decision WHERE review_session_id=?", (session,)).fetchone()
        self.assertEqual(decision["field_name"], "action")
        self.assertEqual(decision["decision"], "confirmed")

    def test_renderer_has_no_images_and_keeps_all_steps(self) -> None:
        route_id = self.add_route("TEST-RENDER", 7)
        result = VariableRouteDocxRenderer(self.store).render(route_id, self.root / "rendered")
        with zipfile.ZipFile(result.docx_path) as archive:
            self.assertFalse([name for name in archive.namelist() if name.startswith("word/media/")])
        validation = json.loads(Path(result.validation_path).read_text(encoding="utf-8"))
        self.assertTrue(validation["structural_pass"])
        self.assertEqual(validation["route_step_count"], 7)
        self.assertEqual(result.page_count_expected_from_route, 8)

    def test_unknown_requires_specific_reason_owner_and_evidence(self) -> None:
        with self.assertRaises(ValidationError):
            UnknownItem(field_name="equipment", reason="待确认", owner_role="待确认", required_evidence="待确认")

    def test_cross_model_pollution_is_rejected(self) -> None:
        identity = ProductIdentity(
            product_code="YA.C.06.0017", product_name="目标水晶头", process_family_code="test_family"
        )
        draft = make_route(identity)
        draft.steps[0].action = "误用了 YA.C.06.0022 的工序说明。"
        result = RouteValidator().validate(draft)
        self.assertFalse(result["valid"])
        self.assertIn("YA.C.06.0022", result["wrong_models"])

    def test_unresolved_content_placeholder_is_rejected(self) -> None:
        identity = make_identity("TEST-PLACEHOLDER")
        draft = make_route(identity)
        draft.steps[0].why = "确认 {length}M 产品身份和受控要求。"
        result = RouteValidator().validate(draft)
        self.assertFalse(result["valid"])
        self.assertEqual(result["unresolved_placeholders"], ["{length}"])

    def test_operation_template_is_versioned_and_not_a_route_approval(self) -> None:
        version_id = self.store.create_operation_template_version(
            family_code="test_family", template_code="TEST-TEMPLATE", template_name="测试族模板",
            version=1, status="draft", content={"fixed_step_count": False},
        )
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM operation_template_version WHERE id=?", (version_id,)).fetchone()
        self.assertEqual(row["status"], "draft")
        self.assertFalse(json.loads(row["definition_json"])["fixed_step_count"])

    def test_approved_operation_template_version_is_immutable(self) -> None:
        version_id = self.store.create_operation_template_version(
            family_code="test_family", template_code="TEST-TEMPLATE-LOCK", template_name="不可变模板",
            version=1, status="draft", content={"fixed_step_count": False},
        )
        self.store.approve_operation_template_version(version_id, approved_by="human_reviewer")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as connection:
                connection.execute(
                    "UPDATE operation_template_version SET definition_json='{}' WHERE id=?", (version_id,)
                )

    def test_proofing_and_full_workbenches_are_exposed(self) -> None:
        route_id = self.add_route("TEST-UI")
        server = create_builtin_server(self.store.path, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            simple_page = urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8")
            self.assertIn("DOCX 实时预览", simple_page)
            self.assertIn("发送并更新 DOCX", simple_page)
            self.assertIn("打开完整版", simple_page)
            self.assertIn('id="docPages"', simple_page)
            self.assertIn("doc.page_urls", simple_page)
            self.assertIn("scheduleDocumentRefresh", simple_page)
            self.assertIn("定位并高亮", simple_page)
            self.assertIn('id="returnPreview"', simple_page)
            self.assertIn("编辑工艺路线", simple_page)
            self.assertIn('id="routeMode"', simple_page)
            self.assertIn("拆成独立工序", simple_page)
            self.assertIn("保存并更新 DOCX", simple_page)
            page = urllib.request.urlopen(base + "/workbench", timeout=5).read().decode("utf-8")
            self.assertIn("可变工序树", page)
            payload = json.loads(urllib.request.urlopen(base + f"/api/routes/{route_id}", timeout=5).read())
            self.assertEqual(payload["route"]["product_code"], "TEST-UI")
            self.assertEqual(len(payload["steps"]), 7)
            self.assertEqual({item["section_type"] for item in payload["sections"]}, set(SECTION_TYPES))
            identity_section = next(item for item in payload["sections"] if item["section_type"] == "product_identity")
            request_body = json.dumps({
                "content": {**identity_section["content_json"], "product_name": "HTTP人工编辑名称"},
                "review_state": "confirmed",
                "reviewer_comment": "HTTP UI/API实测",
                "sources": identity_section["source_json"],
                "conflicts": [],
                "unknowns": [],
                "reviewer": "http_reviewer",
                "decision": "confirmed",
            }, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                base + f"/api/sections/{identity_section['id']}", data=request_body, method="PATCH",
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            changed = json.loads(urllib.request.urlopen(request, timeout=5).read())
            self.assertEqual(changed["status"], "version_created")
            updated = json.loads(urllib.request.urlopen(base + f"/api/routes/{route_id}", timeout=5).read())
            latest_identity = next(item for item in updated["sections"] if item["section_type"] == "product_identity")
            self.assertEqual(latest_identity["version"], 2)
            self.assertEqual(latest_identity["content_json"]["product_name"], "HTTP人工编辑名称")
        finally:
            server.shutdown()
            server.server_close()

    def test_builtin_server_route_editor_endpoints_regenerate_documents(self) -> None:
        route_id = self.add_route("TEST-ROUTE-EDITOR")
        document = {
            "route_id": route_id,
            "route_version": 1,
            "product_code": "TEST-ROUTE-EDITOR",
            "generated_at": "2026-08-14T00:00:00+00:00",
            "version_token": "route-editor-test",
            "page_count": 9,
            "media_count": 0,
            "status": "draft_document_generated",
            "preview_source": "generated_docx",
            "template_id": "yunpai.sop.hdmi-cable.multi-page.v2",
            "layout_mode": "portrait_flow_then_repeated_landscape_work_instructions",
            "docx_url": "/latest.docx",
            "preview_url": "/preview.pdf",
            "page_urls": [],
        }
        with patch("cad_ai.sop_knowledge.web.SopDocumentService.generate", return_value=document) as generate:
            server = create_builtin_server(self.store.path, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"

                def post(path: str, body: dict[str, object]) -> dict[str, object]:
                    request = urllib.request.Request(
                        base + path,
                        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                        method="POST",
                        headers={"Content-Type": "application/json; charset=utf-8"},
                    )
                    return json.loads(urllib.request.urlopen(request, timeout=10).read())

                before = self.store.get_route(route_id)["steps"]
                added = post(
                    f"/api/routes/{route_id}/steps/reviewable",
                    {
                        "title": "人工新增工序",
                        "reviewer": "http-route-editor",
                        "before_step_id": before[1]["id"],
                        "note": "执行已经明确的人工步骤",
                    },
                )
                self.assertEqual(added["status"], "added")
                self.assertEqual(added["document"]["version_token"], "route-editor-test")

                route = self.store.get_route(route_id)
                target = next(item for item in route["steps"] if item["id"] == added["step_id"])
                split = post(
                    f"/api/steps/{target['id']}/split/reviewable",
                    {
                        "mode": "actions",
                        "titles": ["动作一", "动作二", "动作三"],
                        "reviewer": "http-route-editor",
                    },
                )
                self.assertEqual(split["status"], "split_actions")

                ordered_ids = [item["id"] for item in reversed(self.store.get_route(route_id)["steps"])]
                reordered = post(
                    f"/api/routes/{route_id}/reorder",
                    {"ordered_step_ids": ordered_ids, "reviewer": "http-route-editor"},
                )
                self.assertEqual(reordered["status"], "reordered")

                current = self.store.get_route(route_id)["steps"]
                merged = post(
                    f"/api/routes/{route_id}/merge",
                    {
                        "target_step_id": current[0]["id"],
                        "source_step_ids": [current[1]["id"]],
                        "reviewer": "http-route-editor",
                        "title": "HTTP 合并工序",
                    },
                )
                self.assertEqual(merged["status"], "merged")
                self.assertEqual(merged["document"]["version_token"], "route-editor-test")
                self.assertEqual(generate.call_count, 4)
            finally:
                server.shutdown()
                server.server_close()

    def test_builtin_server_media_arrangement_regenerates_documents(self) -> None:
        route_id = self.add_route("TEST-MEDIA-ARRANGEMENT")
        step = self.store.get_route(route_id)["steps"][0]
        asset = self.store.upload_media_asset(
            route_id,
            original_name="media-arrangement.png",
            mime_type="image/png",
            data=PNG_1X1,
            uploaded_by="http-media",
        )
        document = {
            "route_id": route_id,
            "route_version": 1,
            "product_code": "TEST-MEDIA-ARRANGEMENT",
            "generated_at": "2026-08-14T00:00:00+00:00",
            "version_token": "media-arrangement-test",
            "page_count": 9,
            "media_count": 1,
            "status": "draft_document_generated",
            "preview_source": "generated_docx",
            "preview_status": "ready",
            "template_id": "yunpai.sop.hdmi-cable.multi-page.v2",
            "layout_mode": "portrait_flow_then_repeated_landscape_work_instructions",
            "docx_url": "/latest.docx",
            "preview_url": "/preview.pdf",
            "page_urls": [],
        }
        with patch("cad_ai.sop_knowledge.web.SopDocumentService.generate", return_value=document) as generate:
            server = create_builtin_server(self.store.path, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"

                def post(path: str, body: dict[str, object]) -> dict[str, object]:
                    request = urllib.request.Request(
                        base + path,
                        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                        method="POST",
                        headers={"Content-Type": "application/json; charset=utf-8"},
                    )
                    return json.loads(urllib.request.urlopen(request, timeout=10).read())

                layout = post(
                    f"/api/routes/{route_id}/media/bindings",
                    {
                        "bindings": [{"step_id": step["id"], "asset_id": asset["id"], "caption": "HTTP image"}],
                        "reviewer": "http-media",
                    },
                )
                self.assertTrue(layout["changed"])
                self.assertEqual(layout["document"]["version_token"], "media-arrangement-test")
                link_id = self.store.get_route(route_id)["media"][0]["link_id"]
                confirmed = post(f"/api/media/links/{link_id}/confirm", {"reviewer": "http-media"})
                self.assertTrue(confirmed["changed"])
                self.assertEqual(confirmed["document"]["version_token"], "media-arrangement-test")
                self.assertEqual(generate.call_count, 2)

                page = urllib.request.urlopen(base + f"/media-arrangement?route={route_id}", timeout=5).read().decode("utf-8")
                self.assertIn("项目图片统一调整", page)
                self.assertIn("/api/routes/${state.route.route.id}/media/bindings", page)
            finally:
                server.shutdown()
                server.server_close()

    def test_route_section_edit_creates_version_and_review_decision(self) -> None:
        route_id = self.add_route("TEST-SECTION")
        section = next(item for item in self.store.get_route(route_id)["sections"] if item["section_type"] == "product_identity")
        new_id = self.store.revise_route_section(
            section["id"],
            content={"product_code": "TEST-SECTION", "product_name": "人工修改后的产品名称"},
            review_state="confirmed",
            reviewer_comment="产品身份逐字段核对完成",
            sources=[], conflicts=[], unknowns=[], reviewer="identity_reviewer", decision="confirmed",
        )
        history = [item for item in self.store.list_route_sections(route_id, include_history=True) if item["section_type"] == "product_identity"]
        self.assertEqual([item["version"] for item in history], [1, 2])
        self.assertEqual(history[-1]["id"], new_id)
        with self.store.connect() as connection:
            decision = connection.execute("SELECT * FROM review_decision WHERE entity_id=?", (new_id,)).fetchone()
        self.assertEqual(decision["entity_type"], "product")
        self.assertEqual(decision["decision"], "confirmed")
        rejected_id = self.store.revise_route_section(
            new_id,
            content={"product_code": "TEST-SECTION", "product_name": "需返工名称"},
            review_state="rejected",
            reviewer_comment="身份资料冲突，驳回",
            sources=[], conflicts=["名称与受控订单不一致"], unknowns=[],
            reviewer="identity_reviewer", decision="rejected",
        )
        rejected = next(item for item in self.store.list_route_sections(route_id) if item["id"] == rejected_id)
        self.assertEqual(rejected["version"], 3)
        self.assertEqual(rejected["review_state"], "rejected")
        with self.store.connect() as connection:
            rejected_decision = connection.execute("SELECT decision FROM review_decision WHERE entity_id=?", (rejected_id,)).fetchone()
        self.assertEqual(rejected_decision["decision"], "rejected")

    def test_formal_approval_with_blocking_unknown_is_rejected_without_side_effects(self) -> None:
        route_id = self.add_route("TEST-BLOCKER")
        payload = self.store.get_route(route_id)
        for step in payload["steps"]:
            self.store.update_step_field(step["id"], "review_state", "confirmed", reviewer="reviewer")
        for section in self.store.get_route(route_id)["sections"]:
            self.store.revise_route_section(
                section["id"], content=section["content_json"], review_state="confirmed",
                reviewer_comment="confirmed", sources=[], conflicts=[], unknowns=[],
                reviewer="reviewer", decision="confirmed",
            )
        session = self.store.create_review_session(route_id, "reviewer")
        self.store.submit_review(session)
        with self.assertRaisesRegex(ValueError, "blocking_unknowns_zero"):
            self.store.approve(
                session, approved_by="reviewer", approval_scope="formal_production",
                confirmation_token=self.store.formal_confirmation_token("TEST-BLOCKER"),
            )
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM approval_snapshot WHERE route_id=?", (route_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM route_fts WHERE route_id=?", (str(route_id),)).fetchone()[0], 0)
        self.assertEqual(self.store.get_route(route_id)["route"]["status"], "under_review")

    def test_formal_approval_rejects_unconfirmed_step_and_section(self) -> None:
        route_id = self.add_route("TEST-UNCONFIRMED")
        session = self.store.create_review_session(route_id, "reviewer")
        self.store.submit_review(session)
        with self.assertRaisesRegex(ValueError, "steps_confirmed") as caught:
            self.store.approve(
                session, approved_by="reviewer", approval_scope="formal_production",
                confirmation_token=self.store.formal_confirmation_token("TEST-UNCONFIRMED"),
            )
        self.assertIn("sections_confirmed", str(caught.exception))

    def test_formal_approval_rejects_wrong_product_token(self) -> None:
        route_id = self.add_route("TEST-TOKEN")
        session = self.make_formally_approvable(route_id)
        self.store.submit_review(session)
        with self.assertRaisesRegex(ValueError, "confirmation_token"):
            self.store.approve(
                session, approved_by="reviewer", approval_scope="formal_production",
                confirmation_token="FORMAL_APPROVE:ANOTHER-PRODUCT",
            )
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM approval_snapshot WHERE route_id=?", (route_id,)).fetchone()[0], 0)

    def test_formal_approval_succeeds_only_after_every_gate_is_closed(self) -> None:
        route_id = self.add_route("TEST-FORMAL-SUCCESS")
        session = self.make_formally_approvable(route_id, reviewer="formal_reviewer")
        self.store.submit_review(session)
        self.store.approve(
            session,
            approved_by="formal_reviewer",
            approval_scope="formal_production",
            confirmation_token=self.store.formal_confirmation_token("TEST-FORMAL-SUCCESS"),
        )
        payload = self.store.get_route(route_id)
        self.assertEqual(payload["route"]["status"], "approved")
        self.assertEqual(payload["route"]["approval_scope"], "formal_production")
        with self.store.connect() as connection:
            snapshot = connection.execute("SELECT * FROM approval_snapshot WHERE route_id=?", (route_id,)).fetchone()
            indexed = connection.execute("SELECT COUNT(*) FROM route_fts WHERE route_id=?", (str(route_id),)).fetchone()[0]
        self.assertEqual(snapshot["approved_by"], "formal_reviewer")
        self.assertEqual(len(json.loads(snapshot["snapshot_json"])["sections"]), 8)
        self.assertEqual(indexed, 1)

    def test_formal_approval_requires_nonempty_approver_identity(self) -> None:
        route_id = self.add_route("TEST-APPROVER")
        session = self.make_formally_approvable(route_id)
        self.store.submit_review(session)
        with self.assertRaisesRegex(ValueError, "approved_by identity is required"):
            self.store.approve(
                session, approved_by="   ", approval_scope="formal_production",
                confirmation_token=self.store.formal_confirmation_token("TEST-APPROVER"),
            )

    def test_formal_gate_rejects_placeholder_and_cross_model_content(self) -> None:
        route_id = self.add_route("YA.C.06.0017")
        session = self.make_formally_approvable(route_id)
        step_id = self.store.get_route(route_id)["steps"][0]["id"]
        self.store.update_step_field(
            step_id, "action", "错误内容 {length} 且引用 YA.C.06.0022。", reviewer="reviewer"
        )
        self.store.submit_review(session)
        with self.assertRaisesRegex(ValueError, "unresolved_placeholders_zero") as caught:
            self.store.approve(
                session, approved_by="reviewer", approval_scope="formal_production",
                confirmation_token=self.store.formal_confirmation_token("YA.C.06.0017"),
            )
        self.assertIn("model_conflicts_zero", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
