from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from cad_ai.sop_knowledge.nl_assistant import NaturalLanguageSopAssistant
from cad_ai.sop_knowledge.models import RouteSectionDraft
from cad_ai.sop_knowledge.renderer import VariableRouteDocxRenderer
from cad_ai.sop_knowledge.store import SopKnowledgeStore
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

    def test_openai_compatible_llm_path_returns_reviewable_proposal(self) -> None:
        route = self.store.get_route(self.route_id)
        step = route["steps"][0]

        class ModelHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                request_payload = json.loads(self.rfile.read(length).decode("utf-8"))
                self.server.request_payload = request_payload  # type: ignore[attr-defined]
                proposal = {
                    "assistant_message": "已由模型定位第一道工序。",
                    "judgement": ["工序名称与上下文一致。"],
                    "changes": [{
                        "step_ref": step["step_code"],
                        "field_name": "method",
                        "value": ["模型解析后的受控测试动作"],
                        "reason": "验证真实模型协议路径",
                    }],
                    "new_steps": [],
                    "section_changes": [],
                    "image_refs": [],
                    "summary": "识别到 1 项修改。",
                    "warnings": [],
                }
                body = json.dumps({
                    "choices": [{"message": {"content": json.dumps(proposal, ensure_ascii=False)}}]
                }, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ, {
                "OPENAI_API_KEY": "test-only-key",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
                "OPENAI_MODEL": "test-model",
            }):
                proposal, parser_kind = NaturalLanguageSopAssistant().preview(
                    "请把第一道工序改成工作人员可执行的动作。", route
                )
            self.assertEqual(parser_kind, "llm")
            self.assertEqual(proposal["changes"][0]["step_id"], step["id"])
            self.assertEqual(proposal["changes"][0]["value"], ["模型解析后的受控测试动作"])
            request_payload = server.request_payload  # type: ignore[attr-defined]
            self.assertEqual(request_payload["model"], "test-model")
            self.assertEqual(request_payload["response_format"], {"type": "json_object"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

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

    def test_only_conversational_docx_page_is_packaged(self) -> None:
        from cad_ai.sop_knowledge.web import SIMPLE_REVIEW_HTML

        for text in ("DOCX 实时预览", "直接告诉 AI 哪里要改", "发送并更新 DOCX", "下载 DOCX", "AI 模型已连接"):
            self.assertIn(text, SIMPLE_REVIEW_HTML)
        self.assertIn("use_ai:true", SIMPLE_REVIEW_HTML)
        for obsolete in ("打开完整版", "可变工序树", "content_json</label>"):
            self.assertNotIn(obsolete, SIMPLE_REVIEW_HTML)


if __name__ == "__main__":
    unittest.main()
