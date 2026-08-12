from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .sop_agent import (
    SOP_AGENT_VERSION,
    SOP_GENERATION_SEQUENCE,
    SOP_STATUS_DRAFT,
    SopGenerateRequest,
    SopGenerateResponse,
    generate_sop_package,
)


SOP_API_ROUTES = [
    {
        "method": "POST",
        "path": "/api/sop/generate",
        "summary": "Generate a draft 80806-129-style SOP Word package from BOM, routing, and process requirements.",
    },
    {
        "method": "GET",
        "path": "/api/sop/runs/{run_id}",
        "summary": "Inspect a generated SOP run manifest.",
    },
    {
        "method": "GET",
        "path": "/api/sop/runs/{run_id}/artifacts/{artifact_key}",
        "summary": "Download a generated SOP artifact such as docx, flowchart png, manifest, or format check.",
    },
    {
        "method": "GET",
        "path": "/api/sop/health",
        "summary": "Inspect SOP agent API readiness and route contract.",
    },
]


class SopHealthResponse(BaseModel):
    status: str = "ok"
    agent_version: str = SOP_AGENT_VERSION
    draft_status: str = SOP_STATUS_DRAFT
    generation_sequence: list[str] = Field(default_factory=lambda: list(SOP_GENERATION_SEQUENCE))
    routes: list[dict[str, str]] = Field(default_factory=lambda: list(SOP_API_ROUTES))
    ai_boundary: str = (
        "draft-only SOP package generation; no automatic SOP release, signoff, "
        "site IE measurement, EHS approval, OEE/yield, or trial result fabrication"
    )


class SopManifestResponse(BaseModel):
    run_id: str
    manifest: dict[str, Any] = Field(default_factory=dict)


def build_sop_generate_response(request: SopGenerateRequest | dict[str, Any]) -> SopGenerateResponse:
    return generate_sop_package(request)


def load_sop_manifest(run_id: str, *, default_out_dir: Path | str = Path("outputs/sop_agent_api")) -> SopManifestResponse:
    safe_run_id = _safe_path_segment(run_id)
    manifest_path = Path(default_out_dir) / safe_run_id / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"SOP run manifest not found: {safe_run_id}")
    return SopManifestResponse(run_id=safe_run_id, manifest=json.loads(manifest_path.read_text(encoding="utf-8")))


def resolve_sop_artifact(
    run_id: str,
    artifact_key: str,
    *,
    default_out_dir: Path | str = Path("outputs/sop_agent_api"),
) -> tuple[Path, str]:
    safe_run_id = _safe_path_segment(run_id)
    safe_key = _safe_path_segment(artifact_key)
    run_dir = (Path(default_out_dir) / safe_run_id).resolve()
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"SOP run manifest not found: {safe_run_id}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = manifest.get("artifacts") or {}
    path_value = artifacts.get(safe_key)
    if not path_value:
        raise FileNotFoundError(f"SOP artifact not found: {safe_key}")
    path = Path(path_value).resolve()
    if path != run_dir and run_dir not in path.parents:
        raise PermissionError("SOP artifact path escapes the run directory")
    if not path.exists():
        raise FileNotFoundError(f"SOP artifact file missing: {safe_key}")
    return path, _media_type_for_path(path)


def create_sop_fastapi_app(*, default_out_dir: Path | str = Path("outputs/sop_agent_api")):
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse
    except ImportError as exc:  # pragma: no cover - depends on optional web runtime.
        raise RuntimeError("FastAPI is required to create the SOP API app. Install fastapi first.") from exc

    app = FastAPI(
        title="Yunpai SOP Drawing Agent API",
        version=SOP_AGENT_VERSION,
        description="Draft-only 80806-129-style SOP Word table generation API with center flowchart rendering.",
    )

    @app.get("/api/sop/health", response_model=SopHealthResponse)
    def health() -> SopHealthResponse:
        return SopHealthResponse()

    @app.post("/api/sop/generate", response_model=SopGenerateResponse)
    def generate(payload: SopGenerateRequest) -> SopGenerateResponse:
        if not payload.out_dir or payload.out_dir == Path("outputs/sop_agent_api"):
            payload.out_dir = Path(default_out_dir)
        return build_sop_generate_response(payload)

    @app.get("/api/sop/runs/{run_id}", response_model=SopManifestResponse)
    def get_run(run_id: str) -> SopManifestResponse:
        try:
            return load_sop_manifest(run_id, default_out_dir=default_out_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/sop/runs/{run_id}/artifacts/{artifact_key}")
    def get_artifact(run_id: str, artifact_key: str) -> FileResponse:
        try:
            path, media_type = resolve_sop_artifact(run_id, artifact_key, default_out_dir=default_out_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return FileResponse(path, media_type=media_type, filename=path.name)

    return app


def _safe_path_segment(value: str) -> str:
    if not value or any(part in value for part in ["..", "/", "\\"]):
        raise ValueError("invalid path segment")
    return value


def _media_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix == ".png":
        return "image/png"
    if suffix == ".json":
        return "application/json"
    if suffix == ".txt":
        return "text/plain; charset=utf-8"
    return "application/octet-stream"
