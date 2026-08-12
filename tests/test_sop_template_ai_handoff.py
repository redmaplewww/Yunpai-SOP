from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from docx import Document

from scripts.generate_sop_template_ai_handoff import (
    CENTER_FLOWCHART_NAME,
    CONTENT_PROFILE_HDMI,
    FINAL_DOCX_NAME,
    FORMAT_CHECK_NAME,
    HDMI_FINAL_DOCX_NAME,
    HDMI_TEMPLATE_ID,
    MANIFEST_NAME,
    TEMPLATE_ID,
    VALIDATION_NAME,
    generate_package,
    generate_route_package,
    validate_document,
)
from cad_ai.sop_knowledge.store import SopKnowledgeStore
from tests.test_sop_knowledge_workflow import make_identity, make_route


class SopTemplateAiHandoffTests(unittest.TestCase):
    def test_frozen_handoff_entrypoint_generates_exact_two_section_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = generate_package(directory, document_date="2026-08-11")
            root = Path(directory)

            expected_files = {
                FINAL_DOCX_NAME,
                CENTER_FLOWCHART_NAME,
                MANIFEST_NAME,
                FORMAT_CHECK_NAME,
                VALIDATION_NAME,
            }
            self.assertEqual({path.name for path in root.iterdir()}, expected_files)
            self.assertEqual(result["template_id"], TEMPLATE_ID)
            self.assertTrue(result["structural_pass"])
            self.assertTrue(result["visual_qa_required"])

            document = Document(root / FINAL_DOCX_NAME)
            self.assertEqual(len(document.sections), 2)
            self.assertEqual(len(document.tables), 8)
            self.assertEqual(document.tables[0].cell(2, 3).text.strip(), "DRAFT")
            self.assertEqual(document.tables[4].cell(2, 3).text.strip(), "DRAFT")
            self.assertEqual(document.tables[0].cell(2, 5).text.strip(), "2026/8/11")
            self.assertEqual(document.tables[4].cell(1, 7).text.strip(), "2026/8/11")
            self.assertEqual(
                [document.tables[7].cell(1, index).text.strip() for index in range(3)],
                ["", "", ""],
            )

            validation = json.loads((root / VALIDATION_NAME).read_text(encoding="utf-8"))
            self.assertTrue(validation["structural_pass"])
            self.assertEqual(validation["visual_qa"]["expected_page_count"], 2)

            manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(manifest["template_id"], TEMPLATE_ID)
            self.assertTrue(manifest["tables_filled_before_flowchart"])
            self.assertEqual(manifest["fixed_template_profile"]["step_order"], "1,2,3 / 6,5,4")
            self.assertTrue(manifest["guardrails"]["no_auto_release"])

    def test_hdmi_profile_rejects_the_obsolete_two_page_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "route-backed multi-page"):
                generate_package(
                    directory,
                    document_date="2026-08-11",
                    content_profile=CONTENT_PROFILE_HDMI,
                )

    def test_route_backed_hdmi_generates_one_flow_page_and_repeated_instruction_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SopKnowledgeStore(root / "knowledge.sqlite3")
            store.initialize()
            store.ensure_process_family("test_family", "测试工艺族")
            identity = make_identity("HDMI-ROUTE-TEST")
            store.upsert_product(identity, {"class": "cable"})
            route_id = store.create_route(make_route(identity, 3))
            result = generate_route_package(
                root / "package",
                document_date="2026-08-12",
                db_path=store.path,
                route_id=route_id,
            )

            self.assertEqual(result["template_id"], HDMI_TEMPLATE_ID)
            self.assertEqual(result["instruction_page_count"], 3)
            self.assertEqual(result["expected_page_count"], 4)
            document = Document(result["document_docx"])
            self.assertEqual(len(document.sections), 2)
            self.assertEqual(len(document.tables), 16)
            for page_index in range(3):
                base = 4 + page_index * 4
                self.assertEqual(document.tables[base].cell(2, 3).text.strip(), "DRAFT")
                self.assertEqual(
                    [document.tables[base + 3].cell(1, index).text.strip() for index in range(3)],
                    ["", "", ""],
                )

    def test_check_only_validation_rejects_missing_document(self) -> None:
        result = validate_document(Path("does-not-exist.docx"))
        self.assertFalse(result["structural_pass"])
        self.assertEqual(result["errors"], ["document_not_found"])


if __name__ == "__main__":
    unittest.main()
