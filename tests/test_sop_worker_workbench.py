from __future__ import annotations

import base64
import http.client
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from cad_ai.sop_knowledge.nl_assistant import NaturalLanguageSopAssistant
from cad_ai.sop_knowledge.llm_wire import request_json_object
from cad_ai.sop_knowledge.models import RouteSectionDraft, RouteStepDraft
from cad_ai.sop_knowledge.project_creation import NaturalLanguageProjectService
from cad_ai.sop_knowledge.renderer import VariableRouteDocxRenderer
from cad_ai.sop_knowledge.store import ROUTE_SECTION_TYPES, SopKnowledgeStore
from cad_ai.sop_knowledge.web import (
    ProjectImageRequest,
    _confirm_media_and_regenerate,
    _decode_project_images,
    _regenerate_document_after_route_change,
    _save_media_layout_and_regenerate,
)
from tests.test_sop_knowledge_workflow import make_identity, make_route


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class SopWorkerWorkbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SopKnowledgeStore(self.root / "knowledge.sqlite3")
        self.store.initialize()
        self.store.ensure_process_family("test_family", "测试工艺族")
        identity = make_identity("WORKER-TEST")
        self.store.upsert_product(identity, {"class": "cable", "feature": "worker-ui"})
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

    def test_offline_natural_language_preview_maps_process_instruction_and_image(self) -> None:
        route = self.store.get_route(self.route_id)
        proposal, parser_kind = NaturalLanguageSopAssistant(use_llm=False).preview(
            "工序是实际工序 1、实际工序 2；对应作业指导是先核对物料再执行动作、执行后记录结果；图片是步骤1.png、步骤2.png",
            route,
        )
        self.assertEqual(parser_kind, "deterministic")
        self.assertEqual(len(proposal["changes"]), 2)
        self.assertEqual([item["field_name"] for item in proposal["changes"]], ["method", "method"])
        self.assertEqual(len(proposal["image_refs"]), 2)
        self.assertTrue(proposal["requires_human_confirmation"])

    def test_offline_ambiguous_step_comment_never_creates_steps(self) -> None:
        route = self.store.get_route(self.route_id)

        proposal, parser_kind = NaturalLanguageSopAssistant(use_llm=False).preview(
            "工序3我觉得不够齐全，需要补充一下",
            route,
        )

        self.assertEqual(parser_kind, "deterministic")
        self.assertEqual(proposal["changes"], [])
        self.assertEqual(proposal["new_steps"], [])
        self.assertIn("未识别出可安全写入的字段", " ".join(proposal["warnings"]))

    def test_offline_explicit_new_step_request_can_create_one_step(self) -> None:
        route = self.store.get_route(self.route_id)

        proposal, _ = NaturalLanguageSopAssistant(use_llm=False).preview(
            "新增工序：激光打标",
            route,
        )

        self.assertEqual([item["title"] for item in proposal["new_steps"]], ["激光打标"])

    def test_ai_timeout_retries_once_before_using_offline_fallback(self) -> None:
        route = self.store.get_route(self.route_id)
        assistant = NaturalLanguageSopAssistant(use_llm=True, timeout=1, max_llm_attempts=2)
        llm_proposal = {
            "assistant_message": "已理解，但需要补充具体字段。",
            "judgement": ["用户未提供可安全写入的字段。"],
            "summary": "仅回答，不写入草稿。",
            "changes": [], "new_steps": [], "section_changes": [], "image_refs": [], "warnings": [],
        }
        with (
            patch.object(assistant, "_llm_config", return_value={"api_key": "test", "base_url": "https://example.test/v1", "model": "test"}),
            patch.object(assistant, "_llm_preview", side_effect=[TimeoutError("slow"), llm_proposal]) as preview,
            patch("cad_ai.sop_knowledge.nl_assistant.time.sleep"),
        ):
            proposal, parser_kind = assistant.preview("工序3我觉得不够齐全，需要补充一下", route)

        self.assertEqual(parser_kind, "llm")
        self.assertEqual(preview.call_count, 2)
        self.assertEqual(proposal["changes"], [])
        self.assertNotIn("离线规则", " ".join(proposal["warnings"]))

    def test_ai_incomplete_read_retries_once_before_using_offline_fallback(self) -> None:
        route = self.store.get_route(self.route_id)
        assistant = NaturalLanguageSopAssistant(use_llm=True, timeout=1, max_llm_attempts=2)
        llm_proposal = {
            "assistant_message": "已理解，等待人工确认。",
            "judgement": ["用户未提供可安全写入的字段。"],
            "summary": "仅回答，不写入草稿。",
            "changes": [], "new_steps": [], "section_changes": [], "image_refs": [], "warnings": [],
        }
        with (
            patch.object(assistant, "_llm_config", return_value={"api_key": "test", "base_url": "https://example.test/v1", "model": "test"}),
            patch.object(assistant, "_llm_preview", side_effect=[http.client.IncompleteRead(b"x", 2), llm_proposal]) as preview,
            patch("cad_ai.sop_knowledge.nl_assistant.time.sleep"),
        ):
            _, parser_kind = assistant.preview("工序3我觉得不够齐全，需要补充一下", route)

        self.assertEqual(parser_kind, "llm")
        self.assertEqual(preview.call_count, 2)

    def test_ai_prompt_requires_plain_customer_facing_language(self) -> None:
        source = Path(NaturalLanguageSopAssistant.__module__.replace(".", "/") + ".py")
        if not source.is_file():
            source = Path(__file__).resolve().parents[1] / "cad_ai" / "sop_knowledge" / "nl_assistant.py"
        prompt_source = source.read_text(encoding="utf-8")

        self.assertIn("面向客户的表达规则", prompt_source)
        self.assertIn("避免长段落、书面腔、学术化解释", prompt_source)
        self.assertIn("严禁出现 route_steps、JSON、step_code、字段名、数据库", prompt_source)

    def test_responses_wire_uses_structured_input_reasoning_and_disabled_storage(self) -> None:
        config = {
            "api_key": "test-key",
            "base_url": "https://example.test/api",
            "model": "gpt-5.5",
            "wire_api": "responses",
            "reasoning_effort": "xhigh",
            "disable_response_storage": True,
        }
        with patch("cad_ai.sop_knowledge.llm_wire.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
                {"output_text": '{"answer": "ok"}'}, ensure_ascii=False
            ).encode("utf-8")
            result = request_json_object(
                system="Return JSON.", user="Describe the SOP.", config=config, timeout=12
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://example.test/api/responses")
        self.assertEqual(payload["input"][0]["content"][0], {"type": "input_text", "text": "Return JSON."})
        self.assertEqual(payload["input"][1]["content"][0], {"type": "input_text", "text": "Describe the SOP."})
        self.assertEqual(payload["text"], {"format": {"type": "json_object"}})
        self.assertEqual(payload["reasoning"], {"effort": "xhigh"})
        self.assertFalse(payload["store"])
        self.assertNotIn("temperature", payload)
        self.assertEqual(result, {"answer": "ok"})

    def test_responses_wire_includes_uploaded_image_with_source_metadata(self) -> None:
        config = {
            "api_key": "test-key",
            "base_url": "https://example.test/api",
            "model": "gpt-5.5",
            "wire_api": "responses",
        }
        images = [{
            "source_id": "image-01",
            "original_name": "裁线动作.jpg",
            "data_url": "data:image/jpeg;base64,/9j/2Q==",
        }]
        with patch("cad_ai.sop_knowledge.llm_wire.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"output_text":"{\\"answer\\":\\"ok\\"}"}'
            request_json_object(
                system="Return JSON.",
                user="Build the SOP.",
                config=config,
                timeout=12,
                images=images,
            )

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        content = payload["input"][1]["content"]
        self.assertEqual(content[0], {"type": "input_text", "text": "Build the SOP."})
        self.assertIn("source_id=image-01", content[1]["text"])
        self.assertIn("original_name=裁线动作.jpg", content[1]["text"])
        self.assertEqual(content[2], {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,/9j/2Q==",
            "detail": "auto",
        })

    def test_chat_completions_wire_remains_compatible(self) -> None:
        config = {"api_key": "test-key", "base_url": "https://example.test/v1", "model": "legacy-model"}
        with patch("cad_ai.sop_knowledge.llm_wire.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
                {"choices": [{"message": {"content": '{"answer": "legacy"}'}}]}, ensure_ascii=False
            ).encode("utf-8")
            result = request_json_object(system="Return JSON.", user="Describe the SOP.", config=config, timeout=12)

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(result, {"answer": "legacy"})

    def test_chat_completions_wire_accepts_uploaded_images(self) -> None:
        config = {"api_key": "test-key", "base_url": "https://example.test/v1", "model": "vision-model"}
        with patch("cad_ai.sop_knowledge.llm_wire.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"choices":[{"message":{"content":"{\\"answer\\":\\"ok\\"}"}}]}'
            request_json_object(
                system="Return JSON.",
                user="Build the SOP.",
                config=config,
                timeout=12,
                images=[{
                    "source_id": "image-01",
                    "original_name": "inspection.png",
                    "data_url": "data:image/png;base64,iVBORw0KGgo=",
                }],
            )

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        content = payload["messages"][1]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "Build the SOP."})
        self.assertEqual(content[2]["type"], "image_url")
        self.assertEqual(content[2]["image_url"]["url"], "data:image/png;base64,iVBORw0KGgo=")

    def test_new_project_preview_refuses_to_guess_when_ai_is_unavailable(self) -> None:
        service = NaturalLanguageProjectService(self.store, documents=Mock())
        before = len(self.store.list_products())

        with patch.object(NaturalLanguageSopAssistant, "_llm_config", return_value=None):
            result = service.preview("这个产品先裁线，再焊接，最后检查和包装。")

        self.assertEqual(result["parser_kind"], "unavailable")
        self.assertFalse(result["can_create"])
        self.assertEqual(result["steps"], [])
        self.assertIsNone(result["draft_id"])
        self.assertEqual(len(self.store.list_products()), before)

    def test_new_project_image_payload_is_bounded_and_decoded_before_preview(self) -> None:
        decoded = _decode_project_images([ProjectImageRequest(
            source_id="image-01",
            original_name="来料检查.png",
            mime_type="image/png",
            data_base64=base64.b64encode(PNG_1X1).decode("ascii"),
        )])
        self.assertEqual(decoded[0]["data"], PNG_1X1)

        with self.assertRaisesRegex(ValueError, "数据无效"):
            _decode_project_images([ProjectImageRequest(
                source_id="image-bad",
                original_name="损坏图片.png",
                mime_type="image/png",
                data_base64="not-base64!",
            )])

    def test_new_project_rejects_invalid_image_before_ai_or_database_write(self) -> None:
        service = NaturalLanguageProjectService(self.store, documents=Mock())
        before = len(self.store.list_products())
        with self.assertRaisesRegex(ValueError, "不是有效的 PNG"):
            service.preview(
                "产品是图片校验转接线，先检查来料。",
                images=[{
                    "source_id": "image-01",
                    "original_name": "伪造图片.png",
                    "mime_type": "image/png",
                    "data": b"not-a-png",
                }],
            )
        self.assertEqual(len(self.store.list_products()), before)

    def test_new_project_preview_is_memory_only_and_removes_ungrounded_facts(self) -> None:
        service = NaturalLanguageProjectService(self.store, documents=Mock())
        raw = {
            "intent": "create_project",
            "assistant_message": "已整理，请核对。",
            "product_name": "USB-C 转接线",
            "product_code": "",
            "route_name": "USB-C 转接线工艺路线",
            "route_summary": "根据现场描述整理。",
            "steps": [
                {
                    "title": "裁线",
                    "action": "准备线材",
                    "why": "进入后续加工",
                    "method": ["按工单裁线"],
                    "tool_equipment": ["X-100 自动裁线机"],
                    "parameters": [{"name": "长度", "value": "100 mm"}],
                    "quality_check": [],
                    "acceptance_criteria": [],
                    "record_output": [],
                    "exception": [],
                },
                {
                    "title": "包装",
                    "action": "完成包装",
                    "why": "保护成品",
                    "method": ["装入包装袋"],
                    "quality_check": [],
                    "acceptance_criteria": [],
                    "record_output": [],
                    "exception": [],
                },
            ],
            "unknowns": [],
            "warnings": [],
        }
        before = len(self.store.list_products())
        with (
            patch.object(NaturalLanguageSopAssistant, "_llm_config", return_value={"api_key": "x", "base_url": "https://example.test/v1", "model": "test"}),
            patch.object(service, "_request_with_retry", return_value=raw),
        ):
            result = service.preview("这个项目是 USB-C 转接线，先裁线，最后包装。")

        self.assertEqual(result["parser_kind"], "llm")
        self.assertTrue(result["draft_id"])
        self.assertTrue(result["can_create"])
        self.assertEqual(result["steps"][0]["tool_equipment"], [])
        self.assertEqual(result["steps"][0]["parameters"], [])
        self.assertIn("待补充", " ".join(item["label"] for item in result["unknowns"]))
        self.assertEqual(len(self.store.list_products()), before)

    def test_new_project_question_never_becomes_a_creatable_route(self) -> None:
        service = NaturalLanguageProjectService(self.store, documents=Mock())
        raw = {
            "intent": "question",
            "assistant_message": "可以先说明产品和工序。",
            "product_name": "",
            "product_code": "",
            "route_name": "",
            "route_summary": "",
            "steps": [],
            "unknowns": [],
            "warnings": [],
        }
        with (
            patch.object(NaturalLanguageSopAssistant, "_llm_config", return_value={"api_key": "x", "base_url": "https://example.test/v1", "model": "test"}),
            patch.object(service, "_request_with_retry", return_value=raw),
        ):
            result = service.preview("我应该怎么描述一个新项目？")

        self.assertEqual(result["intent"], "question")
        self.assertFalse(result["can_create"])
        self.assertEqual(result["steps"], [])

    def test_new_project_duplicate_requires_a_user_choice(self) -> None:
        service = NaturalLanguageProjectService(self.store, documents=Mock())
        raw = {
            "intent": "create_project",
            "assistant_message": "已整理。",
            "product_name": "测试产品",
            "product_code": "WORKER-TEST",
            "route_name": "测试路线",
            "route_summary": "",
            "steps": [{"title": "检查", "action": "检查来料", "why": "确认来料", "method": ["核对标签"]}],
            "unknowns": [],
            "warnings": [],
        }
        with (
            patch.object(NaturalLanguageSopAssistant, "_llm_config", return_value={"api_key": "x", "base_url": "https://example.test/v1", "model": "test"}),
            patch.object(service, "_request_with_retry", return_value=raw),
        ):
            result = service.preview("产品编号 WORKER-TEST，产品是测试产品，先检查。")

        self.assertFalse(result["can_create"])
        self.assertEqual(result["duplicate_matches"][0]["latest_route_id"], self.route_id)

    def test_confirm_new_project_creates_draft_route_and_regenerates_docx(self) -> None:
        documents = Mock()
        documents.generate.return_value = {"page_count": 3, "preview_status": "ready"}
        service = NaturalLanguageProjectService(self.store, documents=documents)
        raw = {
            "intent": "create_project",
            "assistant_message": "已整理，请核对。",
            "product_name": "新测试转接线",
            "product_code": "NEW-CABLE-01",
            "route_name": "新测试转接线工艺路线",
            "route_summary": "现场人员描述的新项目。",
            "steps": [
                {"title": "来料检查", "action": "检查来料", "why": "确认来料", "method": ["核对标签"]},
                {"title": "成品包装", "action": "包装成品", "why": "保护成品", "method": ["装入包装袋"]},
            ],
            "unknowns": [],
            "warnings": [],
        }
        description = "产品是新测试转接线，编号 NEW-CABLE-01，先来料检查，再成品包装。"
        with (
            patch.object(NaturalLanguageSopAssistant, "_llm_config", return_value={"api_key": "x", "base_url": "https://example.test/v1", "model": "test"}),
            patch.object(service, "_request_with_retry", return_value=raw),
        ):
            preview = service.preview(description)
        result = service.confirm(preview["draft_id"], worker="worker-new-project")

        route = self.store.get_route(result["route_id"])
        self.assertEqual(route["route"]["status"], "draft")
        self.assertEqual(route["route"]["approval_scope"], "none")
        self.assertEqual([item["title"] for item in route["steps"]], ["来料检查", "成品包装"])
        self.assertTrue(all(item["review_state"] == "needs_revision" for item in route["steps"]))
        self.assertEqual({item["section_type"] for item in route["sections"]}, set(ROUTE_SECTION_TYPES))
        self.assertTrue(all(item["review_state"] == "needs_revision" for item in route["sections"]))
        self.assertEqual(result["document"]["page_count"], 1 + len(route["steps"]))
        documents.generate.assert_called_once_with(result["route_id"])

    def test_confirm_new_project_stores_images_as_draft_links_and_grounded_ie_timing(self) -> None:
        documents = Mock()
        documents.generate.return_value = {"page_count": 3, "preview_status": "ready"}
        service = NaturalLanguageProjectService(self.store, documents=documents)
        raw = {
            "intent": "create_project",
            "assistant_message": "已经整理并建立草稿。",
            "product_name": "图文测试转接线",
            "product_code": "IMAGE-INTAKE-01",
            "route_name": "图文测试转接线工艺路线",
            "route_summary": "",
            "steps": [
                {"title": "来料检查", "action": "检查来料", "why": "确认来料", "method": ["核对标签"]},
                {"title": "裁线", "action": "完成裁线", "why": "准备线材", "method": ["按工单裁线"]},
            ],
            "ie_timing": [{
                "step_title": "裁线",
                "duration": "35 秒",
                "source_text": "IE 工时：裁线 35 秒。",
            }],
            "image_assignments": [
                {
                    "source_id": "image-01",
                    "target_step_title": "来料检查",
                    "caption": "核对来料标签",
                    "reason": "图片显示来料核对动作",
                },
                {
                    "source_id": "image-02",
                    "target_step_title": "不存在的工序",
                    "caption": "不能写入",
                },
            ],
            "unknowns": [],
            "warnings": [],
        }
        images = [
            {"source_id": "image-01", "original_name": "来料.png", "mime_type": "image/png", "data": PNG_1X1},
            {"source_id": "image-02", "original_name": "待分配.png", "mime_type": "image/png", "data": PNG_1X1 + b"second"},
        ]
        description = "产品是图文测试转接线，编号 IMAGE-INTAKE-01，先来料检查，再裁线。IE 工时：裁线 35 秒。"
        with (
            patch.object(NaturalLanguageSopAssistant, "_llm_config", return_value={"api_key": "x", "base_url": "https://example.test/v1", "model": "test"}),
            patch.object(service, "_request_with_retry", return_value=raw),
        ):
            preview = service.preview(description, images=images)

        self.assertEqual(len(preview["image_assignments"]), 1)
        self.assertEqual(preview["unassigned_images"][0]["source_id"], "image-02")
        self.assertEqual(preview["ie_timing"][0]["工时"], "35 秒")
        result = service.confirm(preview["draft_id"], worker="worker-image-intake")

        route = self.store.get_route(result["route_id"])
        self.assertEqual(len(route["media_assets"]), 2)
        self.assertEqual(len(route["media"]), 1)
        self.assertEqual(route["media"][0]["link_state"], "draft")
        self.assertEqual(route["media"][0]["caption"], "核对来料标签")
        ie_section = next(item for item in route["sections"] if item["section_type"] == "ie_timing")
        self.assertEqual(ie_section["content_json"]["工序工时"][0]["工时"], "35 秒")
        self.assertEqual(result["image_count"], 2)
        self.assertEqual(result["linked_image_count"], 1)
        self.assertEqual(result["unmatched_image_count"], 1)
        self.assertEqual(result["ie_timing_count"], 1)
        documents.generate.assert_called_once_with(result["route_id"])

    def test_route_reference_file_is_reviewable_and_isolated_from_step_images(self) -> None:
        content = b"%PDF-1.7\nroute reference\n"
        asset = self.store.upload_route_reference_file(
            self.route_id,
            original_name="受控BOM.pdf",
            mime_type="application/pdf",
            data=content,
            uploaded_by="worker-reference",
            source_note="客户提供的路线资料",
        )

        route = self.store.get_route(self.route_id)
        self.assertEqual(len(route["reference_files"]), 1)
        self.assertEqual(route["reference_files"][0]["id"], asset["id"])
        self.assertEqual(route["reference_files"][0]["review_state"], "needs_revision")
        self.assertEqual(route["media_assets"], [])
        self.assertTrue(Path(asset["storage_path"]).is_file())

        confirmed = self.store.confirm_route_reference_file(asset["id"], reviewer="worker-reference")
        self.assertEqual(confirmed["status"], "confirmed")
        duplicate = self.store.upload_route_reference_file(
            self.route_id,
            original_name="受控BOM-副本.pdf",
            mime_type="application/pdf",
            data=content,
            uploaded_by="worker-reference",
            source_note="不应覆盖已确认资料的备注",
        )
        self.assertEqual(duplicate["id"], asset["id"])
        self.assertEqual(duplicate["original_name"], "受控BOM.pdf")
        self.assertEqual(duplicate["source_note"], "客户提供的路线资料")
        self.assertEqual(duplicate["review_state"], "confirmed")
        self.assertEqual(len(self.store.get_route(self.route_id)["reference_files"]), 1)

        deleted = self.store.delete_route_reference_file(asset["id"])
        self.assertEqual(deleted["status"], "deleted")
        self.assertFalse(Path(asset["storage_path"]).exists())

    def test_approved_route_reference_files_are_immutable(self) -> None:
        asset = self.store.upload_route_reference_file(
            self.route_id,
            original_name="approved-route-reference.pdf",
            mime_type="application/pdf",
            data=b"%PDF-1.7\napproved route reference\n",
            uploaded_by="worker-reference",
        )
        with self.store.connect() as connection:
            connection.execute("UPDATE product_route SET status='approved' WHERE id=?", (self.route_id,))

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.upload_route_reference_file(
                self.route_id,
                original_name="blocked-reference.pdf",
                mime_type="application/pdf",
                data=b"%PDF-1.7\nblocked\n",
                uploaded_by="worker-reference",
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.confirm_route_reference_file(asset["id"], reviewer="worker-reference")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.delete_route_reference_file(asset["id"])
        self.assertTrue(Path(asset["storage_path"]).is_file())

    def test_confirm_new_project_accepts_short_shop_floor_actions(self) -> None:
        documents = Mock()
        documents.generate.return_value = {"page_count": 2, "preview_status": "ready"}
        service = NaturalLanguageProjectService(self.store, documents=documents)
        raw = {
            "intent": "create_project",
            "assistant_message": "已整理，请核对。",
            "product_name": "短动作转接线",
            "product_code": "SHORT-ACTION-01",
            "route_name": "短动作转接线工艺路线",
            "route_summary": "",
            "steps": [{"title": "裁线", "action": "裁线", "why": "备料", "method": ["裁线"]}],
            "unknowns": [],
            "warnings": [],
        }
        description = "短动作转接线，编号 SHORT-ACTION-01，先裁线。"
        with (
            patch.object(NaturalLanguageSopAssistant, "_llm_config", return_value={"api_key": "x", "base_url": "https://example.test/v1", "model": "test"}),
            patch.object(service, "_request_with_retry", return_value=raw),
        ):
            preview = service.preview(description)

        self.assertTrue(preview["can_create"])
        result = service.confirm(preview["draft_id"], worker="worker-short-action")

        step = self.store.get_route(result["route_id"])["steps"][0]
        self.assertEqual(step["action"], "完成“裁线”这项操作")
        self.assertEqual(step["why"], "本工序用于备料")
        self.assertEqual(step["review_state"], "needs_revision")

    def test_new_project_preview_blocks_unexpected_step_validation_errors(self) -> None:
        service = NaturalLanguageProjectService(self.store, documents=Mock())
        raw = {
            "intent": "create_project",
            "assistant_message": "已整理，请核对。",
            "product_name": "预检转接线",
            "product_code": "PREFLIGHT-01",
            "route_name": "预检转接线工艺路线",
            "route_summary": "",
            "steps": [{"title": "裁线", "action": "完成裁线", "why": "准备线材", "method": ["执行裁线"]}],
            "unknowns": [],
            "warnings": [],
        }
        with (
            patch.object(NaturalLanguageSopAssistant, "_llm_config", return_value={"api_key": "x", "base_url": "https://example.test/v1", "model": "test"}),
            patch.object(service, "_request_with_retry", return_value=raw),
            patch.object(service, "_to_route_step", side_effect=ValueError("internal validation detail")),
        ):
            preview = service.preview("预检转接线，编号 PREFLIGHT-01，先完成裁线。")

        self.assertFalse(preview["can_create"])
        self.assertTrue(any(item["blocking"] for item in preview["unknowns"]))
        self.assertIn("第 1 道工序“裁线”内容不完整", " ".join(preview["warnings"]))
        self.assertNotIn("internal validation detail", " ".join(preview["warnings"]))

    def test_sanitize_wraps_a_single_warning_string_as_one_warning(self) -> None:
        route = self.store.get_route(self.route_id)
        proposal = {
            "assistant_message": "需要人工确认。",
            "judgement": [], "summary": "未改动。", "changes": [], "new_steps": [],
            "section_changes": [], "image_refs": [], "warnings": "设备型号尚未提供，请人工确认。",
        }

        clean = NaturalLanguageSopAssistant(use_llm=False)._sanitize(proposal, route)

        self.assertEqual(clean["warnings"], ["设备型号尚未提供，请人工确认。"])

    def test_sanitize_wraps_a_single_judgement_string_as_one_item(self) -> None:
        route = self.store.get_route(self.route_id)
        proposal = {
            "assistant_message": "已定位。",
            "judgement": "已定位到成品检验工序。", "summary": "未改动。",
            "changes": [], "new_steps": [], "section_changes": [], "image_refs": [], "warnings": [],
        }

        clean = NaturalLanguageSopAssistant(use_llm=False)._sanitize(proposal, route)

        self.assertEqual(clean["judgement"], ["已定位到成品检验工序。"])

    def test_proposal_apply_stays_unconfirmed_until_explicit_step_confirmation(self) -> None:
        route = self.store.get_route(self.route_id)
        proposal, parser_kind = NaturalLanguageSopAssistant(use_llm=False).preview(
            "工序是实际工序 1；对应作业指导是核对来料标签、执行动作并记录",
            route,
        )
        proposal_id = self.store.create_nl_proposal(
            self.route_id, "修改第一步", proposal, parser_kind=parser_kind, requested_by="worker-01"
        )
        result = self.store.apply_nl_proposal(proposal_id, reviewer="worker-01")
        step_id = route["steps"][0]["id"]
        self.assertEqual(result["status"], "draft_needs_human_review")
        self.assertEqual(self.store.get_route(self.route_id)["steps"][0]["review_state"], "needs_revision")
        self.assertEqual(self.store.search_confirmed_knowledge("核对来料标签"), [])
        confirmed = self.store.confirm_step(step_id, reviewer="worker-01")
        self.assertEqual(confirmed["review_state"], "confirmed")
        matches = self.store.search_confirmed_knowledge("核对来料标签")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["reuse_eligible"], 0)

    def test_knowledge_search_falls_back_to_confirmed_steps_in_current_route(self) -> None:
        steps = self.store.get_route(self.route_id)["steps"]
        confirmed_step = steps[0]
        self.store.update_step_field(
            confirmed_step["id"], "title", "HD-01 成品核对", reviewer="worker-01"
        )
        self.store.confirm_step(confirmed_step["id"], reviewer="worker-01")

        by_code = self.store.search_confirmed_knowledge("HD-01", route_id=self.route_id)
        by_chinese = self.store.search_confirmed_knowledge("成品核对", route_id=self.route_id)

        self.assertEqual([item["step_code"] for item in by_code], [confirmed_step["step_code"]])
        self.assertEqual(by_code[0]["source_scope"], "current_route")
        self.assertEqual([item["step_code"] for item in by_chinese], [confirmed_step["step_code"]])
        self.assertNotIn(steps[1]["id"], [item["route_step_id"] for item in by_code])

    def test_knowledge_search_prioritizes_other_routes_before_current_route(self) -> None:
        current_step = self.store.get_route(self.route_id)["steps"][0]
        self.store.update_step_field(current_step["id"], "title", "共同核对动作", reviewer="worker-01")
        self.store.confirm_step(current_step["id"], reviewer="worker-01")

        other_identity = make_identity("WORKER-HISTORY")
        self.store.upsert_product(other_identity, {"class": "cable", "feature": "history"})
        other_route_id = self.store.create_route(make_route(other_identity, 1))
        other_step = self.store.get_route(other_route_id)["steps"][0]
        self.store.update_step_field(other_step["id"], "title", "共同核对动作", reviewer="worker-02")
        self.store.confirm_step(other_step["id"], reviewer="worker-02")

        matches = self.store.search_confirmed_knowledge("共同核对", route_id=self.route_id)

        self.assertEqual([item["route_id"] for item in matches], [other_route_id, self.route_id])
        self.assertEqual(
            [item["source_scope"] for item in matches], ["other_route", "current_route"]
        )

    def test_uploaded_image_is_draft_then_confirmed_and_rendered(self) -> None:
        step_id = self.store.get_route(self.route_id)["steps"][0]["id"]
        asset = self.store.upload_media_asset(
            self.route_id,
            original_name="步骤1.png",
            mime_type="image/png",
            data=PNG_1X1,
            uploaded_by="worker-02",
            source_note="测试图片",
        )
        self.store.link_media_asset(step_id, asset["id"], caption="人工提供的步骤图片")
        self.assertEqual(self.store.get_route(self.route_id)["media"][0]["link_state"], "draft")
        self.store.confirm_step(step_id, reviewer="worker-02")
        rendered = VariableRouteDocxRenderer(self.store).render(self.route_id, self.root / "rendered")
        self.assertEqual(rendered.media_count, 1)
        with zipfile.ZipFile(rendered.docx_path) as archive:
            self.assertEqual(len([name for name in archive.namelist() if name.startswith("word/media/")]), 1)

    def test_route_wide_media_layout_keeps_confirmed_images_and_marks_new_bindings_draft(self) -> None:
        steps = self.store.get_route(self.route_id)["steps"]
        first_asset = self.store.upload_media_asset(
            self.route_id, original_name="confirmed-image.png", mime_type="image/png",
            data=PNG_1X1, uploaded_by="worker-media",
        )
        second_asset = self.store.upload_media_asset(
            self.route_id, original_name="new-image.png", mime_type="image/png",
            data=PNG_1X1 + b"second", uploaded_by="worker-media",
        )
        self.store.link_media_asset(steps[0]["id"], first_asset["id"], caption="confirmed image")
        self.store.confirm_step(steps[0]["id"], reviewer="worker-media")

        saved = self.store.replace_route_media_bindings(
            self.route_id,
            [
                {"step_id": steps[0]["id"], "asset_id": first_asset["id"], "caption": "confirmed image"},
                {"step_id": steps[0]["id"], "asset_id": second_asset["id"], "caption": "new image"},
            ],
            reviewer="worker-media",
        )

        self.assertTrue(saved["changed"])
        self.assertEqual(saved["added_links"], 1)
        route = self.store.get_route(self.route_id)
        self.assertEqual([item["asset_id"] for item in route["media"]], [first_asset["id"], second_asset["id"]])
        self.assertEqual([item["link_state"] for item in route["media"]], ["confirmed", "draft"])
        self.assertEqual(route["steps"][0]["review_state"], "needs_revision")

        new_link = next(item for item in route["media"] if item["asset_id"] == second_asset["id"])
        confirmed = self.store.confirm_media_link(new_link["link_id"], reviewer="worker-media")
        self.assertTrue(confirmed["changed"])
        self.assertEqual(confirmed["route_id"], self.route_id)

        rendered = VariableRouteDocxRenderer(self.store).render(self.route_id, self.root / "route-media-layout")
        self.assertEqual(rendered.media_count, 2)

    def test_media_layout_and_confirmation_regenerate_latest_document(self) -> None:
        step = self.store.get_route(self.route_id)["steps"][0]
        asset = self.store.upload_media_asset(
            self.route_id, original_name="pending-image.png", mime_type="image/png",
            data=PNG_1X1, uploaded_by="worker-document",
        )
        documents = Mock()
        documents.generate.return_value = {
            "route_id": self.route_id,
            "version_token": "media-version-token",
            "page_count": 4,
            "preview_status": "ready",
        }

        saved = _save_media_layout_and_regenerate(
            self.store,
            documents,
            route_id=self.route_id,
            bindings=[{"step_id": step["id"], "asset_id": asset["id"], "caption": "pending image"}],
            reviewer="worker-document",
        )
        self.assertTrue(saved["changed"])
        self.assertEqual(saved["document"]["version_token"], "media-version-token")
        documents.generate.assert_called_once_with(self.route_id)

        link_id = self.store.get_route(self.route_id)["media"][0]["link_id"]
        documents.generate.reset_mock()
        confirmed = _confirm_media_and_regenerate(
            self.store,
            documents,
            link_id=link_id,
            reviewer="worker-document",
        )
        self.assertTrue(confirmed["changed"])
        self.assertEqual(confirmed["document"]["version_token"], "media-version-token")
        documents.generate.assert_called_once_with(self.route_id)

    def test_deleting_uploaded_image_removes_all_step_links_and_file(self) -> None:
        step_id = self.store.get_route(self.route_id)["steps"][0]["id"]
        asset = self.store.upload_media_asset(
            self.route_id,
            original_name="待删除.png",
            mime_type="image/png",
            data=PNG_1X1,
            uploaded_by="worker-03",
        )
        storage_path = Path(asset["storage_path"])
        self.store.link_media_asset(step_id, asset["id"], caption="待删除绑定")

        result = self.store.delete_media_asset(asset["id"])

        self.assertEqual(result["removed_links"], 1)
        route = self.store.get_route(self.route_id)
        self.assertEqual(route["media_assets"], [])
        self.assertEqual(route["media"], [])
        self.assertFalse(storage_path.exists())

    def test_route_editor_adds_and_reorders_reviewable_steps(self) -> None:
        original = self.store.get_route(self.route_id)["steps"]
        result = self.store.add_reviewable_step(
            self.route_id,
            title="剥皮与芯线整理",
            reviewer="worker-route",
            before_step_id=original[1]["id"],
        )

        added_route = self.store.get_route(self.route_id)
        self.assertEqual(
            [step["title"] for step in added_route["steps"]],
            [original[0]["title"], "剥皮与芯线整理", original[1]["title"], original[2]["title"]],
        )
        added = next(step for step in added_route["steps"] if step["id"] == result["step_id"])
        self.assertEqual(added["review_state"], "needs_revision")
        self.assertTrue(added["unknowns_json"][0]["blocking"])
        self.assertEqual(result["page_number"], 3)

        ordered_ids = [step["id"] for step in added_route["steps"]]
        reordered = self.store.reorder_steps(
            self.route_id,
            [ordered_ids[-1], *ordered_ids[:-1]],
            reviewer="worker-route",
        )
        final = self.store.get_route(self.route_id)["steps"]
        self.assertTrue(reordered["changed"])
        self.assertEqual(final[0]["id"], ordered_ids[-1])
        self.assertEqual([step["sequence_no"] for step in final], [1.0, 2.0, 3.0, 4.0])
        self.assertTrue(all(step["review_state"] == "needs_revision" for step in final))

    def test_route_editor_splits_actions_without_pages_or_into_independent_steps(self) -> None:
        step = self.store.get_route(self.route_id)["steps"][1]
        before_count = len(self.store.get_route(self.route_id)["steps"])

        actions = self.store.split_step_actions(
            step["id"],
            titles=["检查设备状态", "连接测试线", "执行电测并记录"],
            reviewer="worker-route",
        )
        after_actions = self.store.get_route(self.route_id)["steps"]
        changed = next(item for item in after_actions if item["id"] == step["id"])
        self.assertEqual(len(after_actions), before_count)
        self.assertEqual(changed["method_json"], ["检查设备状态", "连接测试线", "执行电测并记录"])
        self.assertEqual(actions["page_number"], 3)

        independent = self.store.split_step_independent(
            step["id"],
            titles=["设备状态检查", "测试线连接", "执行电测与记录"],
            reviewer="worker-route",
        )
        after_independent = self.store.get_route(self.route_id)["steps"]
        self.assertEqual(len(after_independent), before_count + 2)
        self.assertEqual(
            [item["title"] for item in after_independent[1:4]],
            ["设备状态检查", "测试线连接", "执行电测与记录"],
        )
        self.assertEqual(len(independent["page_numbers"]), 3)
        self.assertTrue(all(
            next(item for item in after_independent if item["id"] == step_id)["review_state"] == "needs_revision"
            for step_id in independent["affected_step_ids"]
        ))

    def test_route_editor_merge_preserves_all_fields_media_and_provenance(self) -> None:
        steps = self.store.get_route(self.route_id)["steps"]
        target, source = steps[0], steps[1]
        self.store.update_step_field(
            source["id"], "safety", ["来源工序安全要求"], reviewer="setup"
        )
        asset = self.store.upload_media_asset(
            self.route_id,
            original_name="合并来源.png",
            mime_type="image/png",
            data=PNG_1X1,
            uploaded_by="worker-route",
        )
        self.store.link_media_asset(source["id"], asset["id"], caption="来源图片")
        with self.store.connect() as connection:
            connection.execute(
                """INSERT INTO field_provenance(
                       route_step_id,route_id,field_name,confidence,conflict_status,note
                   ) VALUES(?,?,?,1.0,'clear',?)""",
                (source["id"], self.route_id, "safety", "来源工序证据"),
            )

        merged = self.store.merge_steps(
            self.route_id,
            target["id"],
            [source["id"]],
            reviewer="worker-route",
            title="合并后的受控工序",
        )

        route = self.store.get_route(self.route_id)
        self.assertEqual(len(route["steps"]), 2)
        target_after = next(item for item in route["steps"] if item["id"] == target["id"])
        self.assertEqual(target_after["title"], "合并后的受控工序")
        self.assertIn("来源工序安全要求", target_after["safety_json"])
        self.assertEqual(target_after["review_state"], "needs_revision")
        self.assertEqual(route["media"][0]["route_step_id"], target["id"])
        self.assertTrue(any(item["route_step_id"] == target["id"] for item in route["provenance"]))
        self.assertEqual(merged["removed_step_id"], source["id"])

    def test_route_editor_mutations_reject_approved_routes(self) -> None:
        steps = self.store.get_route(self.route_id)["steps"]
        with self.store.connect() as connection:
            connection.execute("UPDATE product_route SET status='approved' WHERE id=?", (self.route_id,))

        operations = (
            lambda: self.store.add_reviewable_step(
                self.route_id, title="不可新增", reviewer="worker-route"
            ),
            lambda: self.store.reorder_steps(
                self.route_id, [item["id"] for item in reversed(steps)], reviewer="worker-route"
            ),
            lambda: self.store.split_step_actions(
                steps[0]["id"], titles=["动作一", "动作二"], reviewer="worker-route"
            ),
            lambda: self.store.merge_steps(
                self.route_id, steps[0]["id"], [steps[1]["id"]], reviewer="worker-route"
            ),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaisesRegex(
                Exception, "approved route is immutable"
            ):
                operation()

    def test_step_delete_can_be_undone_with_original_position_and_children(self) -> None:
        original = self.store.get_route(self.route_id)["steps"]
        parent = original[1]
        child_id = self.store.add_step(
            self.route_id,
            RouteStepDraft(
                step_code="UNDO-CHILD",
                sequence_no=2.5,
                parent_step_code=parent["step_code"],
                title="待恢复子步骤",
                action="执行待恢复子步骤动作",
                why="验证父子工序能够一起恢复",
                method=["执行子步骤"],
                quality_check=["检查子步骤"],
                acceptance_criteria=["子步骤符合要求"],
                record_output=["记录子步骤"],
                exception=["异常时停止"],
            ),
        )

        deleted = self.store.delete_step(parent["id"], deleted_by="worker-undo")

        visible_ids = [step["id"] for step in self.store.get_route(self.route_id)["steps"]]
        self.assertNotIn(parent["id"], visible_ids)
        self.assertNotIn(child_id, visible_ids)
        self.assertEqual(deleted["affected_step_count"], 2)
        self.assertEqual(len(self.store.list_recent_step_deletions(self.route_id)), 1)

        restored = self.store.restore_step_deletion(deleted["deletion_token"], reviewer="worker-undo")
        restored_route = self.store.get_route(self.route_id)
        restored_ids = [step["id"] for step in restored_route["steps"]]
        self.assertIn(parent["id"], restored_ids)
        self.assertIn(child_id, restored_ids)
        self.assertEqual(restored["restored_step_count"], 2)
        restored_child = next(step for step in restored_route["steps"] if step["id"] == child_id)
        self.assertEqual(restored_child["parent_step_id"], parent["id"])
        self.assertEqual(self.store.list_recent_step_deletions(self.route_id), [])

    def test_deleted_step_media_and_knowledge_are_hidden_then_restored(self) -> None:
        step_id = self.store.get_route(self.route_id)["steps"][0]["id"]
        self.store.update_step_field(
            step_id,
            "title",
            "可恢复知识工序",
            reviewer="worker-undo",
            decision="needs_revision",
        )
        asset = self.store.upload_media_asset(
            self.route_id,
            original_name="可恢复图片.png",
            mime_type="image/png",
            data=PNG_1X1,
            uploaded_by="worker-undo",
        )
        self.store.link_media_asset(step_id, asset["id"], caption="可恢复绑定")
        self.store.confirm_step(step_id, reviewer="worker-undo")
        self.assertEqual(len(self.store.search_confirmed_knowledge("可恢复知识工序")), 1)

        deleted = self.store.delete_step(step_id, deleted_by="worker-undo")

        self.assertEqual(self.store.get_route(self.route_id)["media"], [])
        self.assertEqual(self.store.search_confirmed_knowledge("可恢复知识工序"), [])

        self.store.restore_step_deletion(deleted["deletion_token"], reviewer="worker-undo")

        self.assertEqual(len(self.store.get_route(self.route_id)["media"]), 1)
        self.assertEqual(len(self.store.search_confirmed_knowledge("可恢复知识工序")), 1)

    def test_step_delete_preserves_approved_routes(self) -> None:
        approved_step = self.store.get_route(self.route_id)["steps"][0]["id"]

        with self.store.connect() as connection:
            connection.execute("UPDATE product_route SET status='approved' WHERE id=?", (self.route_id,))
        with self.assertRaisesRegex(Exception, "approved route is immutable"):
            self.store.delete_step(approved_step, deleted_by="worker-undo")

    def test_completed_delete_is_reported_when_preview_regeneration_fails(self) -> None:
        documents = Mock()
        documents.generate.side_effect = RuntimeError("Word/PDF 转换不可用")

        result = _regenerate_document_after_route_change(
            documents,
            {"status": "deleted", "route_id": self.route_id, "deletion_token": "undo-token"},
        )

        self.assertEqual(result["status"], "deleted")
        self.assertEqual(result["deletion_token"], "undo-token")
        self.assertIsNone(result["document"])
        self.assertEqual(result["document_status"], "generation_failed")
        self.assertIn("Word/PDF 转换不可用", result["document_error"])

        documents.generate.side_effect = RuntimeError("DOCX 已生成，但预览转换失败：Word 不可用")
        preview_failure = _regenerate_document_after_route_change(
            documents,
            {"status": "restored", "route_id": self.route_id},
        )
        self.assertEqual(preview_failure["document_status"], "preview_failed")

    def test_worker_page_exposes_simple_natural_language_flow(self) -> None:
        from cad_ai.sop_knowledge.web import MEDIA_ARRANGEMENT_HTML, REVIEW_HTML

        for text in (
            "直接说哪里要改", "理解并预览", "确认本工序", "相似历史内容",
            "项目图片上传", "删除图片", "确认当前资料", "要求修改",
            "本路线已确认", "历史人工确认", "正式可复用",
        ):
            self.assertIn(text, REVIEW_HTML)
        self.assertIn("data-delete-step", REVIEW_HTML)
        self.assertIn("项目图片统一调整", REVIEW_HTML)
        self.assertIn("/media-arrangement?route=", REVIEW_HTML)
        self.assertIn("完整工序图", MEDIA_ARRANGEMENT_HTML)
        self.assertIn("/media/bindings", MEDIA_ARRANGEMENT_HTML)
        self.assertIn("/api/media/links/${linkId}/confirm", MEDIA_ARRANGEMENT_HTML)
        self.assertIn("每道工序最多绑定 6 张图片", MEDIA_ARRANGEMENT_HTML)
        self.assertIn("renderInspectorOrderControls", MEDIA_ARRANGEMENT_HTML)
        self.assertIn("data-move-link", MEDIA_ARRANGEMENT_HTML)
        self.assertNotIn("scrollIntoView", MEDIA_ARRANGEMENT_HTML)
        self.assertIn("已删除", REVIEW_HTML)
        self.assertIn("最近删除", REVIEW_HTML)
        self.assertIn("撤销", REVIEW_HTML)
        simple_html = (
            Path(__file__).resolve().parents[1]
            / "cad_ai"
            / "sop_knowledge"
            / "simple_workbench.html"
        ).read_text(encoding="utf-8")
        self.assertIn("const asList", simple_html)
        self.assertIn("target-option", simple_html)
        self.assertIn("data-candidate-step", simple_html)
        self.assertIn("selected_step_id", simple_html)
        self.assertIn("pending_message_id", simple_html)
        self.assertIn("parts.every", simple_html)
        self.assertIn("查看其他候选", simple_html)
        self.assertIn("locate-change", simple_html)
        self.assertIn('id="returnPreview"', simple_html)
        self.assertIn("changePageNumber", simple_html)
        self.assertIn("page_number", simple_html)
        self.assertIn("field_key", simple_html)
        self.assertIn("scrollPreviewTo", simple_html)
        self.assertIn('id="routeMode"', simple_html)
        self.assertIn('id="routeEditor"', simple_html)
        self.assertIn(".chat[hidden]{display:none}", simple_html)
        self.assertIn('id="retryPreview"', simple_html)
        self.assertIn('id="newProject"', simple_html)
        self.assertIn('id="projectBackdrop"', simple_html)
        self.assertIn("/api/projects/preview", simple_html)
        self.assertIn("/api/projects/confirm", simple_html)
        self.assertIn('id="projectImageInput"', simple_html)
        self.assertIn(".project-intake-grid{display:grid", simple_html)
        self.assertIn(".project-intake-step{width:32px;height:32px", simple_html)
        self.assertIn(".project-upload-zone{min-height:112px", simple_html)
        self.assertIn(".project-image-input{position:fixed;left:-9999px", simple_html)
        self.assertIn("开始整理并生成 SOP", simple_html)
        self.assertIn("data_base64", simple_html)
        self.assertIn("images})", simple_html)
        self.assertIn("renderProjectComplete(result,preview)", simple_html)
        self.assertIn("loadProducts(result.route_id)", simple_html)
        self.assertIn("创建项目草稿", simple_html)
        self.assertIn("AI 暂时没有连接上", simple_html)
        self.assertIn("retryDocumentPreview", simple_html)
        self.assertIn("documentInfo?.preview_status==='failed'", simple_html)
        self.assertIn("预览暂时不可用", simple_html)
        self.assertIn("openAddRouteStep", simple_html)
        self.assertIn("openSplitRouteStep", simple_html)
        self.assertIn("openMergeRouteStep", simple_html)
        self.assertIn("openDeleteRouteStep", simple_html)
        self.assertIn("saveRouteOrder", simple_html)
        self.assertIn("createRouteRevision", simple_html)
        self.assertIn("route-layout-button", simple_html)
        self.assertIn("openWorkImageLayout", simple_html)
        self.assertIn("/work-image-layout", simple_html)
        self.assertIn("调整指导书图片格数", simple_html)
        self.assertIn("/steps/reviewable", simple_html)
        self.assertIn("/split/reviewable", simple_html)
        self.assertNotIn("scrollIntoView", simple_html)
        self.assertNotIn("content_json</label>", REVIEW_HTML)


if __name__ == "__main__":
    unittest.main()
