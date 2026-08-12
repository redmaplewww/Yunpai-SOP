from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from typing import Any

from cad_ai.sop_knowledge.conversation import SopConversationService
from cad_ai.sop_knowledge.documents import MULTI_PAGE_TEMPLATE_ID, SopDocumentService
from cad_ai.sop_knowledge.models import RouteSectionDraft
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

    def test_latest_repairs_missing_preview_pages_from_existing_pdf(self) -> None:
        import pymupdf

        documents = SopDocumentService(self.store)
        output_dir = documents.root / f"route_{self.route_id}"
        preview_dir = output_dir / "preview"
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
