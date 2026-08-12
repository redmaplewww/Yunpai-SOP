from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .store import SopKnowledgeStore


MULTI_PAGE_TEMPLATE_ID = "yunpai.sop.hdmi-cable.multi-page.v1"
MULTI_PAGE_LAYOUT_MODE = "portrait_flow_then_repeated_landscape_work_instructions"
MULTI_PAGE_DOCX_NAME = "SOP完整模板_HDMI线制作_草案.docx"


class SopDocumentService:
    """Generate the final DOCX and a browser preview from the same artifact."""

    _locks: dict[int, threading.Lock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, store: SopKnowledgeStore) -> None:
        self.store = store
        self.root = store.path.parent / "generated_documents"
        self.root.mkdir(parents=True, exist_ok=True)
        self.project_root = Path(__file__).resolve().parents[2]
        self.preview_script = self.project_root / "scripts" / "render_docx_preview.ps1"
        self.template_script = self.project_root / "scripts" / "generate_sop_template_ai_handoff.py"

    def generate(self, route_id: int) -> dict[str, Any]:
        lock = self._lock_for(route_id)
        with lock:
            output_dir = self.root / f"route_{route_id}"
            preview_dir = output_dir / "preview"
            template_dir = output_dir / "template_package"
            output_dir.mkdir(parents=True, exist_ok=True)
            preview_dir.mkdir(parents=True, exist_ok=True)
            template_dir.mkdir(parents=True, exist_ok=True)
            rendered = self._generate_template_package(route_id, template_dir)
            docx_path = Path(rendered["document_docx"])
            preview = self._render_preview(docx_path, preview_dir)
            version_token = hashlib.sha256(docx_path.read_bytes()).hexdigest()[:16]
            route_fingerprint = self._route_fingerprint(route_id)
            route_payload = self.store.get_route(route_id)
            route = route_payload["route"]
            generated_at = datetime.now(timezone.utc).isoformat()
            manifest = {
                "route_id": route_id,
                "route_version": route["version"],
                "product_code": route["product_code"],
                "generated_at": generated_at,
                "version_token": version_token,
                "route_fingerprint": route_fingerprint,
                "template_id": rendered["template_id"],
                "layout_mode": MULTI_PAGE_LAYOUT_MODE,
                "docx_path": str(docx_path.resolve()),
                "pdf_path": preview["pdf_path"],
                "page_paths": preview["page_paths"],
                "page_count": preview["page_count"],
                "expected_page_count": rendered["expected_page_count"],
                "validation_path": rendered["validation_json"],
                "media_count": len(route_payload.get("media") or []),
                "status": "draft_document_generated",
                "preview_source": "generated_docx",
            }
            if preview["page_count"] != rendered["expected_page_count"]:
                raise RuntimeError(
                    f"SOP 页数不符合模板：应为 {rendered['expected_page_count']} 页，实际为 {preview['page_count']} 页"
                )
            (output_dir / "document_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return self.public_manifest(manifest)

    def latest(self, route_id: int, *, generate_if_missing: bool = True) -> dict[str, Any]:
        manifest_path = self.root / f"route_{route_id}" / "document_manifest.json"
        if not manifest_path.exists():
            if not generate_if_missing:
                raise FileNotFoundError("该路线尚未生成 DOCX")
            return self.generate(route_id)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("template_id") != MULTI_PAGE_TEMPLATE_ID or manifest.get("layout_mode") != MULTI_PAGE_LAYOUT_MODE:
            return self.generate(route_id)
        if not Path(manifest["docx_path"]).is_file() or not Path(manifest["pdf_path"]).is_file():
            return self.generate(route_id)
        if manifest.get("route_fingerprint") != self._route_fingerprint(route_id):
            return self.generate(route_id)
        return self.public_manifest(manifest)

    def resolve_file(self, route_id: int, kind: str, *, page_no: int | None = None) -> tuple[Path, str, str]:
        manifest_path = self.root / f"route_{route_id}" / "document_manifest.json"
        if not manifest_path.exists():
            self.generate(route_id)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if kind == "docx":
            return Path(manifest["docx_path"]), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", f"SOP_{manifest['product_code']}.docx"
        if kind == "pdf":
            return Path(manifest["pdf_path"]), "application/pdf", f"SOP_{manifest['product_code']}_preview.pdf"
        if kind == "page" and page_no is not None:
            paths = manifest["page_paths"]
            if page_no < 1 or page_no > len(paths):
                raise KeyError(page_no)
            return Path(paths[page_no - 1]), "image/png", f"page-{page_no}.png"
        raise ValueError("unsupported document asset")

    @staticmethod
    def public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
        route_id = manifest["route_id"]
        token = manifest["version_token"]
        return {
            key: manifest[key]
            for key in (
                "route_id", "route_version", "product_code", "generated_at", "version_token",
                "page_count", "media_count", "status", "preview_source", "template_id", "layout_mode",
            )
        } | {
            "docx_url": f"/api/routes/{route_id}/documents/latest.docx?v={token}",
            "preview_url": f"/api/routes/{route_id}/documents/preview.pdf?v={token}",
            "page_urls": [f"/api/routes/{route_id}/documents/pages/{index}.png?v={token}" for index in range(1, int(manifest["page_count"]) + 1)],
        }

    def _render_preview(self, docx_path: Path, output_dir: Path) -> dict[str, Any]:
        if not self.preview_script.is_file():
            raise FileNotFoundError(f"DOCX preview script is missing: {self.preview_script}")
        for artifact in [*output_dir.glob("*.pdf"), *output_dir.glob("page-*.png")]:
            artifact.unlink()
        with tempfile.TemporaryDirectory(prefix="sop_docx_preview_") as staging_value:
            staging_dir = Path(staging_value)
            staging_docx = staging_dir / "source.docx"
            staging_output = staging_dir / "rendered"
            shutil.copy2(docx_path, staging_docx)
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File",
                    str(self.preview_script), "-InputPath", str(staging_docx),
                    "-OutputDirectory", str(staging_output),
                ],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            if result.returncode == 0:
                staging_pdfs = list(staging_output.glob("*.pdf"))
                staging_pages = sorted(staging_output.glob("page-*.png"))
                if len(staging_pdfs) == 1 and staging_pages:
                    shutil.copy2(staging_pdfs[0], output_dir / f"{docx_path.stem}.pdf")
                    for page in staging_pages:
                        shutil.copy2(page, output_dir / page.name)
        if result.returncode != 0:
            raise RuntimeError("DOCX 已生成，但预览转换失败：" + (result.stderr.strip() or "Word/PDF 转换不可用"))
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("DOCX 预览转换没有返回有效结果") from exc
        # PowerShell 5.1 can encode an absolute directory containing Chinese
        # characters with the active console code page. Resolve the artifacts
        # from the known output directory instead of trusting those path bytes.
        pdf_files = sorted(output_dir.glob("*.pdf"))
        page_files = sorted(output_dir.glob("page-*.png"))
        if len(pdf_files) != 1 or not page_files:
            raise RuntimeError("DOCX 预览转换产物不完整")
        return {
            "pdf_path": str(pdf_files[0].resolve()),
            "page_paths": [str(item.resolve()) for item in page_files],
            "page_count": len(page_files),
        }

    def _generate_template_package(self, route_id: int, output_dir: Path) -> dict[str, Any]:
        if not self.template_script.is_file():
            raise FileNotFoundError(f"SOP template generator is missing: {self.template_script}")
        result = subprocess.run(
            [
                sys.executable,
                str(self.template_script),
                "--out-dir", str(output_dir),
                "--document-date", date.today().isoformat(),
                "--route-db", str(self.store.path),
                "--route-id", str(route_id),
            ],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "SOP 模板生成失败：" + (result.stderr.strip() or result.stdout.strip() or "未知错误")
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("SOP 模板生成器没有返回有效结果") from exc
        if payload.get("template_id") != MULTI_PAGE_TEMPLATE_ID or not payload.get("structural_pass"):
            raise RuntimeError("SOP 模板生成结果未通过结构校验")
        # The child Python process may emit its absolute Chinese paths using the
        # active Windows code page. Resolve governed artifacts from their known
        # package names instead of trusting those serialized path bytes.
        payload["document_docx"] = str((output_dir / MULTI_PAGE_DOCX_NAME).resolve())
        payload["validation_json"] = str((output_dir / "sop_template_validation.json").resolve())
        return payload

    def _route_fingerprint(self, route_id: int) -> str:
        payload = self.store.get_route(route_id)
        source = {
            "route": payload["route"],
            "steps": payload["steps"],
            "sections": payload["sections"],
            "media": payload["media"],
        }
        encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _lock_for(cls, route_id: int) -> threading.Lock:
        with cls._locks_guard:
            return cls._locks.setdefault(route_id, threading.Lock())
