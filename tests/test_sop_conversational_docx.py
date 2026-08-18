from __future__ import annotations

import tempfile
import unittest
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from cad_ai.sop_knowledge.conversation import SopConversationService
from cad_ai.sop_knowledge.documents import CURRENT_PREVIEW_DIR_NAME, MULTI_PAGE_TEMPLATE_ID, SopDocumentService
from cad_ai.sop_knowledge.models import RouteSectionDraft
from cad_ai.sop_knowledge.nl_assistant import NaturalLanguageSopAssistant
from cad_ai.sop_knowledge.store import SopKnowledgeStore
from tests.test_sop_knowledge_workflow import make_identity, make_route


class FakeAssistant:
    def preview(self, instruction: str, route: dict[str, Any], *, history=None):
        step = route["steps"][1]
        return ({
            "assistant_message": "我定位到第二道工序和质量控制章节，已经按描述修改草稿。",
            "judgement": ["“第二步”对应当前路线排序中的第二道工序。"],
            "summary": "修改 1 个工序字段和 1 个章节。",
            "changes": [{
                "step_id": step["id"], "step_code": step["step_code"], "step_title": step["title"],
                "field_name": "method", "value": ["先核对方向", "插入到位", "轻拉确认"],
                "reason": "用户要求拆解动作",
            }],
            "new_steps": [],
            "section_changes": [{
                "section_type": "quality_control", "patch": {"inspection_note": "逐件轻拉确认"},
                "reason": "用户补充检查要求",
            }],
            "image_refs": [], "warnings": [], "requires_human_confirmation": True,
        }, "llm")


class UnsafeFallbackNewStepAssistant:
    def preview(self, instruction: str, route: dict[str, Any], *, history=None):
        return ({
            "assistant_message": "已识别到两个待新增工序。",
            "judgement": [],
            "summary": "新增两个工序。",
            "changes": [],
            "new_steps": [
                {"title": "3我觉得不够齐全", "method": []},
                {"title": "需要补充一下", "method": []},
            ],
            "section_changes": [],
            "image_refs": [],
            "warnings": ["AI 服务暂时不可用，已使用离线规则解析。"],
            "requires_human_confirmation": True,
        }, "deterministic_fallback")


class LockedTargetAssistant:
    def __init__(self) -> None:
        self.calls = 0

    def preview(self, instruction: str, route: dict[str, Any], *, history=None):
        self.calls += 1
        step_id = int(route["_locked_target_step_id"])
        step = next(item for item in route["steps"] if int(item["id"]) == step_id)
        return ({
            "assistant_message": f"已定位到{step['title']}，修改内容已写入草稿。",
            "judgement": [f"本次只修改{step['title']}。"],
            "summary": "修改 1 项安全要求。",
            "changes": [{
                "step_id": step["id"], "step_code": step["step_code"], "step_title": step["title"],
                "field_name": "safety", "value": ["操作前确认设备状态"],
                "reason": "用户明确补充安全要求",
            }],
            "new_steps": [], "section_changes": [], "image_refs": [], "warnings": [],
            "requires_human_confirmation": True,
        }, "llm")


class WrongTargetAssistant:
    def __init__(self) -> None:
        self.calls = 0

    def preview(self, instruction: str, route: dict[str, Any], *, history=None):
        self.calls += 1
        step = route["steps"][0]
        return ({
            "assistant_message": "已完成修改。",
            "judgement": "已定位工序。",
            "summary": "修改 1 项安全要求。",
            "changes": [{
                "step_id": step["id"], "step_code": step["step_code"], "step_title": step["title"],
                "field_name": "safety", "value": ["操作前确认设备状态"],
                "reason": "错误沿用了旧目标",
            }],
            "new_steps": [], "section_changes": [], "image_refs": [], "warnings": [],
            "requires_human_confirmation": True,
        }, "llm")


class NeverCalledAssistant:
    def preview(self, instruction: str, route: dict[str, Any], *, history=None):
        raise AssertionError("ambiguous target must be confirmed before calling the assistant")


class FakeDocuments:
    def __init__(self) -> None:
        self.generated: list[int] = []

    def generate(self, route_id: int) -> dict[str, Any]:
        self.generated.append(route_id)
        return {
            "route_id": route_id, "route_version": 1, "product_code": "CHAT-TEST",
            "generated_at": "2026-08-12T00:00:00+00:00", "version_token": "test",
            "page_count": 3, "media_count": 0, "status": "draft_document_generated",
            "preview_source": "generated_docx", "docx_url": "/latest.docx",
            "preview_url": "/preview.pdf", "page_urls": [],
        }

    def latest(self, route_id: int, *, generate_if_missing: bool = True) -> dict[str, Any]:
        return self.generate(route_id)


class StableDocuments(FakeDocuments):
    def latest(self, route_id: int, *, generate_if_missing: bool = True) -> dict[str, Any]:
        return {
            "route_id": route_id, "route_version": 1, "product_code": "CHAT-TEST",
            "generated_at": "2026-08-12T00:00:00+00:00", "version_token": "existing",
            "page_count": 4, "media_count": 0, "status": "draft_document_generated",
            "preview_source": "generated_docx", "docx_url": "/latest.docx",
            "preview_url": "/preview.pdf", "page_urls": [],
        }


class SopConversationalDocxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SopKnowledgeStore(Path(self.temp.name) / "knowledge.sqlite3")
        self.store.initialize()
        self.store.ensure_process_family("test_family", "测试工艺族")
        identity = make_identity("CHAT-TEST")
        self.store.upsert_product(identity, {"class": "cable"})
        self.route_id = self.store.create_route(make_route(identity, 3))
        for section_type in (
            "product_identity", "bom_material", "equipment_fixture", "process_parameter",
            "quality_control", "packaging_label", "ie_timing", "release_signoff",
        ):
            self.store.create_route_section(
                self.route_id,
                RouteSectionDraft(section_type=section_type, content={"section": section_type}),
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_chat_applies_ai_routing_regenerates_docx_and_records_work(self) -> None:
        documents = FakeDocuments()
        service = SopConversationService(
            self.store, documents, assistant=FakeAssistant()  # type: ignore[arg-type]
        )
        result = service.chat(self.route_id, "把第二步拆详细，并补上质检说明", worker="worker-01")
        self.assertTrue(result["docx_regenerated"])
        self.assertEqual(documents.generated, [self.route_id])
        self.assertEqual(len(result["changes"]), 2)
        route = self.store.get_route(self.route_id)
        self.assertEqual(route["steps"][1]["method_json"], ["先核对方向", "插入到位", "轻拉确认"])
        quality = next(item for item in route["sections"] if item["section_type"] == "quality_control")
        self.assertEqual(quality["version"], 2)
        self.assertEqual(quality["content_json"]["inspection_note"], "逐件轻拉确认")
        history = self.store.list_chat_messages(self.route_id)
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])
        self.assertTrue(history[-1]["metadata_json"]["docx_regenerated"])
        self.assertIn("judgement", history[-1]["metadata_json"])
        self.assertEqual(result["changes"][0]["field"], "作业步骤")
        self.assertEqual(result["changes"][0]["location"], "DOCX 第 3 页 > 作业步骤")
        self.assertEqual(result["changes"][0]["page_number"], 3)
        self.assertEqual(result["changes"][0]["field_key"], "method")

    def test_explicit_read_only_question_never_applies_model_changes(self) -> None:
        documents = FakeDocuments()
        service = SopConversationService(
            self.store, documents, assistant=FakeAssistant()  # type: ignore[arg-type]
        )

        result = service.chat(
            self.route_id,
            "请说明第 2 道工序当前内容，不要修改 SOP。",
            worker="worker-01",
        )

        self.assertFalse(result["docx_regenerated"])
        self.assertEqual(result["changes"], [])
        self.assertEqual(documents.generated, [self.route_id])
        self.assertIn("未写入 SOP 草稿", result["message"])
        self.assertEqual(self.store.get_route(self.route_id)["steps"][1]["review_state"], "unreviewed")

    def test_fallback_never_auto_adds_steps_without_explicit_new_step_intent(self) -> None:
        documents = FakeDocuments()
        service = SopConversationService(
            self.store,
            documents,
            assistant=UnsafeFallbackNewStepAssistant(),  # type: ignore[arg-type]
        )
        before_count = len(self.store.get_route(self.route_id)["steps"])

        result = service.chat(
            self.route_id,
            "工序3我觉得不够齐全，需要补充一下",
            worker="worker-01",
        )

        self.assertEqual(result["parser_kind"], "deterministic_fallback")
        self.assertFalse(result["docx_regenerated"])
        self.assertEqual(len(self.store.get_route(self.route_id)["steps"]), before_count)
        self.assertIn("没有明确要求新增工序", " ".join(result["warnings"]))

    def test_offline_explicit_new_step_intent_still_adds_one_reviewable_step(self) -> None:
        documents = FakeDocuments()
        service = SopConversationService(
            self.store,
            documents,
            assistant=NaturalLanguageSopAssistant(use_llm=False),
        )
        before_count = len(self.store.get_route(self.route_id)["steps"])

        result = service.chat(
            self.route_id,
            "新增工序：激光打标",
            worker="worker-01",
        )

        route = self.store.get_route(self.route_id)
        self.assertTrue(result["docx_regenerated"])
        self.assertEqual(len(route["steps"]), before_count + 1)
        self.assertEqual(route["steps"][-1]["title"], "激光打标")
        self.assertEqual(route["steps"][-1]["review_state"], "needs_revision")

    def test_location_query_returns_last_change_docx_page_without_writing(self) -> None:
        documents = FakeDocuments()
        service = SopConversationService(
            self.store, documents, assistant=FakeAssistant()  # type: ignore[arg-type]
        )
        service.chat(self.route_id, "把第 2 道工序补充完整", worker="worker-01")

        result = service.chat(self.route_id, "刚才具体改在哪里查看？", worker="worker-01")

        self.assertEqual(result["parser_kind"], "reference")
        self.assertFalse(result["docx_regenerated"])
        self.assertIn("第 2 道", result["message"])
        self.assertIn("DOCX 第 3 页 > 作业步骤", result["message"])

    def test_ambiguous_target_returns_candidates_without_writing_or_regenerating(self) -> None:
        steps = self.store.get_route(self.route_id)["steps"]
        self.store.update_step_field(steps[0]["id"], "title", "电气性能检验", reviewer="setup")
        self.store.update_step_field(steps[1]["id"], "title", "成品外观检验", reviewer="setup")
        documents = StableDocuments()
        service = SopConversationService(
            self.store, documents, assistant=NeverCalledAssistant()  # type: ignore[arg-type]
        )

        result = service.chat(
            self.route_id,
            "把检验工序的记录要求补充为登记工单号。",
            worker="worker-01",
        )

        self.assertEqual(result["target_resolution"]["status"], "needs_choice")
        self.assertEqual(len(result["target_resolution"]["candidates"]), 2)
        self.assertFalse(result["docx_regenerated"])
        self.assertEqual(documents.generated, [])
        self.assertIsNone(result["proposal_id"])
        history = self.store.list_chat_messages(self.route_id)
        self.assertEqual(history[-1]["metadata_json"]["pending_instruction"], "把检验工序的记录要求补充为登记工单号。")

    def test_candidate_selection_applies_only_to_selected_step(self) -> None:
        steps = self.store.get_route(self.route_id)["steps"]
        self.store.update_step_field(steps[0]["id"], "title", "电气性能检验", reviewer="setup")
        self.store.update_step_field(steps[1]["id"], "title", "成品外观检验", reviewer="setup")
        documents = StableDocuments()
        assistant = LockedTargetAssistant()
        service = SopConversationService(self.store, documents, assistant=assistant)  # type: ignore[arg-type]
        pending = service.chat(
            self.route_id,
            "把检验工序的安全要求补充为操作前确认设备状态。",
            worker="worker-01",
        )

        result = service.chat(
            self.route_id,
            "选择成品外观检验",
            worker="worker-01",
            selected_step_id=steps[1]["id"],
            pending_message_id=pending["assistant_message_id"],
        )

        route = self.store.get_route(self.route_id)
        self.assertTrue(result["docx_regenerated"])
        self.assertEqual(route["steps"][1]["safety_json"], ["操作前确认设备状态"])
        self.assertNotEqual(route["steps"][0]["safety_json"], ["操作前确认设备状态"])
        self.assertEqual(result["target_resolution"]["selected_step_id"], steps[1]["id"])
        self.assertEqual(assistant.calls, 1)

    def test_model_target_outside_locked_step_is_retried_then_blocked(self) -> None:
        steps = self.store.get_route(self.route_id)["steps"]
        self.store.update_step_field(steps[0]["id"], "title", "裁线与长度补偿", reviewer="setup")
        self.store.update_step_field(steps[1]["id"], "title", "异常隔离与人工放行", reviewer="setup")
        documents = StableDocuments()
        assistant = WrongTargetAssistant()
        service = SopConversationService(self.store, documents, assistant=assistant)  # type: ignore[arg-type]

        result = service.chat(
            self.route_id,
            "把隔离步骤的安全要求补充为操作前确认设备状态。",
            worker="worker-01",
        )

        route = self.store.get_route(self.route_id)
        self.assertFalse(result["docx_regenerated"])
        self.assertEqual(documents.generated, [])
        self.assertEqual(assistant.calls, 2)
        self.assertNotEqual(route["steps"][0]["safety_json"], ["操作前确认设备状态"])
        self.assertIn("目标不一致", " ".join(result["warnings"]))

    def test_document_fingerprint_detects_backend_route_edits(self) -> None:
        documents = SopDocumentService(self.store)
        before = documents._route_fingerprint(self.route_id)
        step = self.store.get_route(self.route_id)["steps"][0]
        self.store.update_step_field(
            step["id"], "method", ["人工通过受控接口修改后的新动作"], reviewer="worker-02",
            decision="needs_revision", comment="测试文档失效检测",
        )
        after = documents._route_fingerprint(self.route_id)
        self.assertNotEqual(before, after)

    def test_preview_failure_keeps_existing_pdf_and_pages(self) -> None:
        documents = SopDocumentService(self.store)
        output_dir = documents.root / f"route_{self.route_id}" / "preview"
        output_dir.mkdir(parents=True)
        old_pdf = output_dir / "existing.pdf"
        old_page = output_dir / "page-001.png"
        old_pdf.write_bytes(b"existing-pdf")
        old_page.write_bytes(b"existing-page")
        docx_path = documents.root / f"route_{self.route_id}" / "source.docx"
        docx_path.write_bytes(b"new-docx")

        failed = SimpleNamespace(returncode=1, stdout="", stderr="Word COM unavailable")
        with patch("cad_ai.sop_knowledge.documents.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "Word COM unavailable"):
                documents._render_preview(docx_path, output_dir)

        self.assertEqual(old_pdf.read_bytes(), b"existing-pdf")
        self.assertEqual(old_page.read_bytes(), b"existing-page")

    def test_preview_publish_uses_versioned_directory_when_current_dir_is_locked(self) -> None:
        documents = SopDocumentService(self.store)
        route_dir = documents.root / f"route_{self.route_id}"
        current_preview = route_dir / CURRENT_PREVIEW_DIR_NAME
        candidate_preview = route_dir / "candidate-preview"
        current_preview.mkdir(parents=True)
        candidate_preview.mkdir()
        (current_preview / "old.pdf").write_bytes(b"old-preview")
        (candidate_preview / "new.pdf").write_bytes(b"new-preview")

        actual_replace = os.replace

        def replace_with_locked_current(source, target):
            if Path(source) == current_preview:
                raise PermissionError(13, "Access denied", str(source), str(target))
            return actual_replace(source, target)

        with patch("cad_ai.sop_knowledge.documents.os.replace", side_effect=replace_with_locked_current):
            published_preview = documents._publish_preview_directory(candidate_preview, current_preview)

        self.assertTrue(published_preview.name.startswith("preview-version-"))
        self.assertTrue((published_preview / "new.pdf").is_file())
        self.assertTrue((current_preview / "old.pdf").is_file())

    def test_latest_accepts_versioned_preview_directory(self) -> None:
        documents = SopDocumentService(self.store)
        route_dir = documents.root / f"route_{self.route_id}"
        versioned_preview = route_dir / "preview-version-live"
        route_dir.mkdir(parents=True)
        versioned_preview.mkdir()
        docx_path = route_dir / "current.docx"
        pdf_path = versioned_preview / "current.pdf"
        page_path = versioned_preview / "page-001.png"
        docx_path.write_bytes(b"current-docx")
        pdf_path.write_bytes(b"current-pdf")
        page_path.write_bytes(b"current-page")
        manifest = {
            "route_id": self.route_id,
            "route_version": 1,
            "product_code": "CHAT-TEST",
            "generated_at": "2026-08-12T00:00:00+00:00",
            "version_token": "versioned-preview",
            "route_fingerprint": documents._route_fingerprint(self.route_id),
            "template_id": MULTI_PAGE_TEMPLATE_ID,
            "layout_mode": "portrait_flow_then_repeated_landscape_work_instructions",
            "docx_path": str(docx_path),
            "pdf_path": str(pdf_path),
            "page_paths": [str(page_path)],
            "page_count": 1,
            "expected_page_count": 1,
            "validation_path": "",
            "media_count": 0,
            "status": "draft_document_generated",
            "preview_source": "generated_docx",
        }
        (route_dir / "document_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        with patch.object(documents, "generate") as generate:
            result = documents.latest(self.route_id)

        generate.assert_not_called()
        self.assertEqual(result["version_token"], "versioned-preview")

    def test_preview_file_checks_treat_permission_errors_as_unreadable(self) -> None:
        documents = SopDocumentService(self.store)
        locked = documents.root / "locked-preview.pdf"

        with patch.object(Path, "is_file", side_effect=PermissionError(13, "Access denied", str(locked))):
            self.assertFalse(documents._is_readable_file(locked))
            self.assertFalse(documents._is_existing_file(locked))

    def test_latest_regenerates_instead_of_repairing_an_unreadable_pdf(self) -> None:
        documents = SopDocumentService(self.store)
        route_dir = documents.root / f"route_{self.route_id}"
        preview_dir = route_dir / "preview-version-locked"
        route_dir.mkdir(parents=True)
        preview_dir.mkdir()
        docx_path = route_dir / "current.docx"
        pdf_path = preview_dir / "current.pdf"
        docx_path.write_bytes(b"current-docx")
        pdf_path.write_bytes(b"locked-pdf")
        manifest = {
            "route_id": self.route_id,
            "route_version": 1,
            "product_code": "CHAT-TEST",
            "generated_at": "2026-08-12T00:00:00+00:00",
            "version_token": "locked-preview",
            "route_fingerprint": documents._route_fingerprint(self.route_id),
            "template_id": MULTI_PAGE_TEMPLATE_ID,
            "layout_mode": "portrait_flow_then_repeated_landscape_work_instructions",
            "docx_path": str(docx_path),
            "pdf_path": str(pdf_path),
            "page_paths": [str(preview_dir / "page-001.png")],
            "page_count": 1,
            "expected_page_count": 1,
            "validation_path": "",
            "media_count": 0,
            "status": "draft_document_generated",
            "preview_source": "generated_docx",
        }
        (route_dir / "document_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        with (
            patch.object(documents, "_is_readable_file", side_effect=lambda path: Path(path) != pdf_path),
            patch.object(documents, "_is_existing_file", return_value=False),
            patch.object(documents, "_render_pdf_pages") as render_pages,
            patch.object(documents, "generate", return_value={"status": "regenerated"}) as generate,
        ):
            result = documents.latest(self.route_id)

        self.assertEqual(result["status"], "regenerated")
        generate.assert_called_once_with(self.route_id)
        render_pages.assert_not_called()

    def test_latest_returns_persisted_preview_failure_without_retrying(self) -> None:
        documents = SopDocumentService(self.store)
        output_dir = documents.root / f"route_{self.route_id}"
        output_dir.mkdir(parents=True)
        docx_path = output_dir / "current.docx"
        docx_path.write_bytes(b"current-docx")
        rendered = {
            "document_docx": str(docx_path),
            "template_id": MULTI_PAGE_TEMPLATE_ID,
            "expected_page_count": 3,
            "validation_json": str(output_dir / "validation.json"),
        }

        with (
            patch.object(documents, "_generate_template_package", return_value=rendered) as generate_template,
            patch.object(
                documents,
                "_render_preview",
                side_effect=RuntimeError("DOCX 已生成，但预览转换失败：Word COM unavailable"),
            ) as render_preview,
        ):
            first = documents.latest(self.route_id)
            second = documents.latest(self.route_id)

        self.assertEqual(generate_template.call_count, 1)
        self.assertEqual(render_preview.call_count, 1)
        self.assertEqual(first["preview_status"], "failed")
        self.assertEqual(second["version_token"], first["version_token"])
        self.assertEqual(first["page_urls"], [])
        self.assertIn("可下载", first["preview_error"])
        resolved, mime_type, _ = documents.resolve_file(self.route_id, "docx")
        self.assertEqual(resolved.resolve(), docx_path.resolve())
        self.assertIn("wordprocessingml", mime_type)

    def test_latest_regenerates_when_manifest_uses_legacy_preview_directory(self) -> None:
        documents = SopDocumentService(self.store)
        output_dir = documents.root / f"route_{self.route_id}"
        output_dir.mkdir(parents=True)
        docx_path = output_dir / "current.docx"
        pdf_path = output_dir / "preview" / "current.pdf"
        docx_path.write_bytes(b"current-docx")
        pdf_path.parent.mkdir()
        pdf_path.write_bytes(b"current-pdf")
        manifest = {
            "route_id": self.route_id,
            "route_version": 1,
            "product_code": "CHAT-TEST",
            "generated_at": "2026-08-12T00:00:00+00:00",
            "version_token": "unreadable-preview",
            "route_fingerprint": documents._route_fingerprint(self.route_id),
            "template_id": MULTI_PAGE_TEMPLATE_ID,
            "layout_mode": "portrait_flow_then_repeated_landscape_work_instructions",
            "docx_path": str(docx_path),
            "pdf_path": str(pdf_path),
            "page_paths": [],
            "page_count": 0,
            "expected_page_count": 1,
            "validation_path": "",
            "media_count": 0,
            "status": "draft_document_generated",
            "preview_source": "generated_docx",
        }
        (output_dir / "document_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        regenerated = {"version_token": "rebuilt"}

        with patch.object(documents, "generate", return_value=regenerated) as generate:
            result = documents.latest(self.route_id)

        self.assertEqual(result, regenerated)
        generate.assert_called_once_with(self.route_id)

    def test_latest_repairs_missing_preview_pages_from_existing_pdf(self) -> None:
        import pymupdf

        documents = SopDocumentService(self.store)
        output_dir = documents.root / f"route_{self.route_id}"
        preview_dir = output_dir / CURRENT_PREVIEW_DIR_NAME
        output_dir.mkdir(parents=True)
        preview_dir.mkdir(parents=True)
        docx_path = output_dir / "test.docx"
        pdf_path = preview_dir / "test.pdf"
        docx_path.write_bytes(b"test-docx")
        pdf = pymupdf.open()
        pdf.new_page(width=595, height=842).insert_text((72, 72), "preview page")
        pdf.save(pdf_path)
        pdf.close()
        manifest = {
            "route_id": self.route_id,
            "route_version": 1,
            "product_code": "CHAT-TEST",
            "generated_at": "2026-08-12T00:00:00+00:00",
            "version_token": "test",
            "route_fingerprint": documents._route_fingerprint(self.route_id),
            "template_id": MULTI_PAGE_TEMPLATE_ID,
            "layout_mode": "portrait_flow_then_repeated_landscape_work_instructions",
            "docx_path": str(docx_path),
            "pdf_path": str(pdf_path),
            "page_paths": [],
            "page_count": 1,
            "expected_page_count": 1,
            "validation_path": "",
            "media_count": 0,
            "status": "draft_document_generated",
            "preview_source": "generated_docx",
        }
        (output_dir / "document_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        result = documents.latest(self.route_id)

        self.assertEqual(len(result["page_urls"]), 1)
        page_path, mime_type, _ = documents.resolve_file(self.route_id, "page", page_no=1)
        self.assertEqual(mime_type, "image/png")
        self.assertTrue(page_path.is_file())


if __name__ == "__main__":
    unittest.main()
