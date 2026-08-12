from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel

from .models import ReviewFieldPatch, RouteSectionPatch, RouteStepDraft
from .conversation import SopConversationService
from .documents import SopDocumentService
from .nl_assistant import NaturalLanguageSopAssistant
from .store import SopKnowledgeStore


class ReviewerRequest(BaseModel):
    reviewer: str
    comment: str = ""


class ApprovalRequest(BaseModel):
    approved_by: str
    approval_scope: str
    confirmation_token: str | None = None


class ReorderRequest(BaseModel):
    ordered_step_ids: list[int]


class MergeRequest(BaseModel):
    target_step_id: int
    source_step_ids: list[int]
    reviewer: str


class NlPreviewRequest(BaseModel):
    instruction: str
    worker: str
    use_ai: bool = True


class ProposalApplyRequest(BaseModel):
    worker: str


class MediaUploadRequest(BaseModel):
    original_name: str
    mime_type: str
    data_base64: str
    uploaded_by: str
    source_note: str = ""


class MediaLinkRequest(BaseModel):
    step_id: int
    asset_id: int
    caption: str = ""


class StepConfirmRequest(BaseModel):
    reviewer: str
    comment: str = ""


class ChatRequest(BaseModel):
    message: str
    worker: str
    use_ai: bool = True


def create_review_app(db_path: str | Path):
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import HTMLResponse
    except ImportError as exc:
        raise RuntimeError("FastAPI is required for the SOP review workbench") from exc

    store = SopKnowledgeStore(db_path)
    store.initialize()
    documents = SopDocumentService(store)
    conversation = SopConversationService(store, documents)
    app = FastAPI(title="SOP 工艺路线人工审核工作台", version="1.0.0")

    def guard(call):
        try:
            return call()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return SIMPLE_REVIEW_HTML

    @app.get("/workbench", response_class=HTMLResponse)
    def full_workbench() -> str:
        return REVIEW_HTML

    @app.get("/api/products")
    def products() -> list[dict[str, Any]]:
        return store.list_products()

    @app.get("/api/routes/{route_id}")
    def route(route_id: int) -> dict[str, Any]:
        return guard(lambda: store.get_route(route_id))

    @app.get("/api/assistant/status")
    def assistant_status() -> dict[str, str]:
        return NaturalLanguageSopAssistant().status()

    @app.get("/api/routes/{route_id}/chat/history")
    def chat_history(route_id: int, limit: int = 40) -> list[dict[str, Any]]:
        return guard(lambda: store.list_chat_messages(route_id, limit=limit))

    @app.post("/api/routes/{route_id}/chat")
    def chat(route_id: int, request: ChatRequest) -> dict[str, Any]:
        service = conversation
        if not request.use_ai:
            service = SopConversationService(
                store,
                documents,
                assistant=NaturalLanguageSopAssistant(use_llm=False),
            )
        return guard(lambda: service.chat(route_id, request.message, worker=request.worker))

    @app.get("/api/routes/{route_id}/documents/latest")
    def latest_document(route_id: int) -> dict[str, Any]:
        return guard(lambda: documents.latest(route_id, generate_if_missing=True))

    @app.post("/api/routes/{route_id}/documents/generate")
    def generate_document(route_id: int) -> dict[str, Any]:
        return guard(lambda: documents.generate(route_id))

    @app.get("/api/routes/{route_id}/documents/latest.docx")
    def download_document(route_id: int):
        from fastapi.responses import FileResponse
        path, mime_type, filename = guard(lambda: documents.resolve_file(route_id, "docx"))
        return FileResponse(path, media_type=mime_type, filename=filename)

    @app.get("/api/routes/{route_id}/documents/preview.pdf")
    def preview_document(route_id: int):
        from fastapi.responses import FileResponse
        path, mime_type, _ = guard(lambda: documents.resolve_file(route_id, "pdf"))
        return FileResponse(path, media_type=mime_type, content_disposition_type="inline")

    @app.get("/api/routes/{route_id}/documents/pages/{page_no}.png")
    def preview_page(route_id: int, page_no: int):
        from fastapi.responses import FileResponse
        path, mime_type, _ = guard(lambda: documents.resolve_file(route_id, "page", page_no=page_no))
        return FileResponse(path, media_type=mime_type, content_disposition_type="inline")

    @app.post("/api/routes/{route_id}/nl/preview")
    def nl_preview(route_id: int, request: NlPreviewRequest) -> dict[str, Any]:
        route_payload = guard(lambda: store.get_route(route_id))
        proposal, parser_kind = guard(lambda: NaturalLanguageSopAssistant(use_llm=request.use_ai).preview(request.instruction, route_payload))
        proposal_id = guard(lambda: store.create_nl_proposal(route_id, request.instruction, proposal, parser_kind=parser_kind, requested_by=request.worker))
        return {"proposal_id": proposal_id, "parser_kind": parser_kind, **proposal}

    @app.post("/api/proposals/{proposal_id}/apply")
    def apply_proposal(proposal_id: int, request: ProposalApplyRequest) -> dict[str, Any]:
        return guard(lambda: store.apply_nl_proposal(proposal_id, reviewer=request.worker))

    @app.post("/api/routes/{route_id}/media")
    def upload_media(route_id: int, request: MediaUploadRequest) -> dict[str, Any]:
        try:
            data = base64.b64decode(request.data_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(status_code=400, detail="图片数据不是有效 Base64") from exc
        return guard(lambda: store.upload_media_asset(route_id, original_name=request.original_name, mime_type=request.mime_type, data=data, uploaded_by=request.uploaded_by, source_note=request.source_note))

    @app.post("/api/media/link")
    def link_media(request: MediaLinkRequest) -> dict[str, Any]:
        link_id = guard(lambda: store.link_media_asset(request.step_id, request.asset_id, caption=request.caption))
        return {"status": "draft_linked", "link_id": link_id}

    @app.get("/api/media/{asset_id}")
    def media_file(asset_id: int):
        from fastapi.responses import FileResponse
        asset = guard(lambda: store.get_media_asset(asset_id))
        return FileResponse(asset["storage_path"], media_type=asset["mime_type"], filename=asset["original_name"])

    @app.post("/api/steps/{step_id}/confirm")
    def confirm_step(step_id: int, request: StepConfirmRequest) -> dict[str, Any]:
        return guard(lambda: store.confirm_step(step_id, reviewer=request.reviewer, comment=request.comment))

    @app.get("/api/knowledge/search")
    def search_knowledge(q: str, route_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        return guard(lambda: store.search_confirmed_knowledge(q, route_id=route_id, limit=limit))

    @app.patch("/api/steps/{step_id}")
    def patch_step(step_id: int, patch: ReviewFieldPatch) -> dict[str, Any]:
        if patch.step_id != step_id:
            raise HTTPException(status_code=400, detail="step id mismatch")
        guard(lambda: store.update_step_field(step_id, patch.field_name, patch.value, reviewer="web_reviewer", decision=patch.decision, comment=patch.comment))
        return {"status": "saved", "step_id": step_id, "field": patch.field_name}

    @app.get("/api/routes/{route_id}/sections/history")
    def section_history(route_id: int) -> list[dict[str, Any]]:
        return guard(lambda: store.list_route_sections(route_id, include_history=True))

    @app.patch("/api/sections/{section_id}")
    def patch_section(section_id: int, patch: RouteSectionPatch) -> dict[str, Any]:
        new_id = guard(lambda: store.revise_route_section(
            section_id,
            content=patch.content,
            review_state=patch.review_state,
            reviewer_comment=patch.reviewer_comment,
            sources=[item.model_dump(mode="json") for item in patch.sources],
            conflicts=patch.conflicts,
            unknowns=[item.model_dump(mode="json") for item in patch.unknowns],
            reviewer=patch.reviewer,
            decision=patch.decision,
        ))
        return {"status": "version_created", "previous_section_id": section_id, "section_id": new_id}

    @app.post("/api/routes/{route_id}/steps")
    def add_step(route_id: int, step: RouteStepDraft) -> dict[str, Any]:
        step_id = guard(lambda: store.add_step(route_id, step))
        return {"status": "added", "step_id": step_id}

    @app.post("/api/steps/{step_id}/split")
    def split_step(step_id: int, child: RouteStepDraft) -> dict[str, Any]:
        parent = guard(lambda: _find_step(store, step_id))
        child.parent_step_code = parent["step_code"]
        child_id = guard(lambda: store.add_step(parent["route_id"], child))
        return {"status": "split", "parent_step_id": step_id, "child_step_id": child_id}

    @app.delete("/api/steps/{step_id}")
    def delete_step(step_id: int) -> dict[str, Any]:
        guard(lambda: store.delete_step(step_id))
        return {"status": "deleted", "step_id": step_id}

    @app.post("/api/routes/{route_id}/reorder")
    def reorder(route_id: int, request: ReorderRequest) -> dict[str, Any]:
        guard(lambda: store.reorder_steps(route_id, request.ordered_step_ids))
        return {"status": "reordered", "route_id": route_id}

    @app.post("/api/routes/{route_id}/merge")
    def merge(route_id: int, request: MergeRequest) -> dict[str, Any]:
        guard(lambda: store.merge_steps(route_id, request.target_step_id, request.source_step_ids, reviewer=request.reviewer))
        return {"status": "merged", "route_id": route_id}

    @app.post("/api/routes/{route_id}/reviews")
    def start_review(route_id: int, request: ReviewerRequest) -> dict[str, Any]:
        session_id = guard(lambda: store.create_review_session(route_id, request.reviewer, request.comment))
        return {"status": "draft", "review_session_id": session_id}

    @app.post("/api/reviews/{session_id}/submit")
    def submit(session_id: int) -> dict[str, Any]:
        guard(lambda: store.submit_review(session_id))
        return {"status": "under_review", "review_session_id": session_id}

    @app.post("/api/reviews/{session_id}/approve")
    def approve(session_id: int, request: ApprovalRequest) -> dict[str, Any]:
        route_id = guard(lambda: store.approve(
            session_id,
            approved_by=request.approved_by,
            approval_scope=request.approval_scope,
            confirmation_token=request.confirmation_token,
        ))
        return {"status": "approved", "route_id": route_id, "approval_scope": request.approval_scope}

    @app.post("/api/reviews/{session_id}/reject")
    def reject(session_id: int, request: ReviewerRequest) -> dict[str, Any]:
        guard(lambda: store.reject(session_id, reviewer=request.reviewer, comment=request.comment))
        return {"status": "rejected", "review_session_id": session_id}

    @app.post("/api/routes/{route_id}/revision")
    def revision(route_id: int, request: ReviewerRequest) -> dict[str, Any]:
        new_route_id = guard(lambda: store.create_revision(route_id, created_by=request.reviewer))
        return {"status": "draft", "route_id": new_route_id, "parent_route_id": route_id}

    return app


def _find_step(store: SopKnowledgeStore, step_id: int) -> dict[str, Any]:
    with store.connect() as connection:
        row = connection.execute("SELECT * FROM route_step WHERE id=?", (step_id,)).fetchone()
        if not row:
            raise KeyError(step_id)
        return dict(row)


def create_builtin_server(db_path: str | Path, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    """Dependency-free local review server used by the documented launch command."""
    store = SopKnowledgeStore(db_path)
    store.initialize()
    documents = SopDocumentService(store)
    conversation = SopConversationService(store, documents)

    class ReviewHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

        def _send(self, status: int, payload: Any, content_type: str = "application/json; charset=utf-8") -> None:
            data = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, path: str | Path, mime_type: str, *, filename: str | None = None, attachment: bool = False) -> None:
            data = Path(path).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(data)))
            disposition = "attachment" if attachment else "inline"
            safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename or Path(path).name)
            self.send_header("Content-Disposition", f'{disposition}; filename="{safe_name}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _run(self, call) -> None:
            try:
                self._send(200, call())
            except Exception as exc:
                self._send(400, {"detail": str(exc)})

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/":
                self._send(200, SIMPLE_REVIEW_HTML, "text/html; charset=utf-8")
            elif path == "/workbench":
                self._send(200, REVIEW_HTML, "text/html; charset=utf-8")
            elif path == "/api/products":
                self._run(store.list_products)
            elif path == "/api/assistant/status":
                self._run(lambda: NaturalLanguageSopAssistant().status())
            elif path == "/api/knowledge/search":
                q = query.get("q", [""])[0]
                route_id = int(query["route_id"][0]) if query.get("route_id") else None
                limit = int(query.get("limit", ["10"])[0])
                self._run(lambda: store.search_confirmed_knowledge(q, route_id=route_id, limit=limit))
            elif match := re.fullmatch(r"/api/routes/(\d+)/chat/history", path):
                limit = int(query.get("limit", ["40"])[0])
                self._run(lambda: store.list_chat_messages(int(match.group(1)), limit=limit))
            elif match := re.fullmatch(r"/api/routes/(\d+)/documents/latest", path):
                self._run(lambda: documents.latest(int(match.group(1)), generate_if_missing=True))
            elif match := re.fullmatch(r"/api/routes/(\d+)/documents/latest\.docx", path):
                try:
                    file_path, mime_type, filename = documents.resolve_file(int(match.group(1)), "docx")
                    self._send_file(file_path, mime_type, filename=filename, attachment=True)
                except Exception as exc:
                    self._send(400, {"detail": str(exc)})
            elif match := re.fullmatch(r"/api/routes/(\d+)/documents/preview\.pdf", path):
                try:
                    file_path, mime_type, filename = documents.resolve_file(int(match.group(1)), "pdf")
                    self._send_file(file_path, mime_type, filename=filename)
                except Exception as exc:
                    self._send(400, {"detail": str(exc)})
            elif match := re.fullmatch(r"/api/routes/(\d+)/documents/pages/(\d+)\.png", path):
                try:
                    file_path, mime_type, filename = documents.resolve_file(
                        int(match.group(1)), "page", page_no=int(match.group(2))
                    )
                    self._send_file(file_path, mime_type, filename=filename)
                except Exception as exc:
                    self._send(400, {"detail": str(exc)})
            elif match := re.fullmatch(r"/api/media/(\d+)", path):
                try:
                    asset = store.get_media_asset(int(match.group(1)))
                    self._send_file(asset["storage_path"], asset["mime_type"])
                except Exception as exc:
                    self._send(400, {"detail": str(exc)})
            elif match := re.fullmatch(r"/api/routes/(\d+)", path):
                self._run(lambda: store.get_route(int(match.group(1))))
            elif match := re.fullmatch(r"/api/routes/(\d+)/sections/history", path):
                self._run(lambda: store.list_route_sections(int(match.group(1)), include_history=True))
            else:
                self._send(404, {"detail": "not found"})

        def do_PATCH(self) -> None:
            body = self._body()
            if match := re.fullmatch(r"/api/steps/(\d+)", self.path):
                step_id = int(match.group(1))
                patch = ReviewFieldPatch.model_validate({"step_id": step_id, **body})
                self._run(lambda: (
                    store.update_step_field(step_id, patch.field_name, patch.value, reviewer=body.get("reviewer", "web_reviewer"), decision=patch.decision, comment=patch.comment),
                    {"status": "saved", "step_id": step_id, "field": patch.field_name},
                )[1])
                return
            if match := re.fullmatch(r"/api/sections/(\d+)", self.path):
                section_id = int(match.group(1))
                patch = RouteSectionPatch.model_validate(body)
                self._run(lambda: {
                    "status": "version_created",
                    "previous_section_id": section_id,
                    "section_id": store.revise_route_section(
                        section_id,
                        content=patch.content,
                        review_state=patch.review_state,
                        reviewer_comment=patch.reviewer_comment,
                        sources=[item.model_dump(mode="json") for item in patch.sources],
                        conflicts=patch.conflicts,
                        unknowns=[item.model_dump(mode="json") for item in patch.unknowns],
                        reviewer=patch.reviewer,
                        decision=patch.decision,
                    ),
                })
                return
            self._send(404, {"detail": "not found"})

        def do_DELETE(self) -> None:
            match = re.fullmatch(r"/api/steps/(\d+)", self.path)
            if not match:
                self._send(404, {"detail": "not found"})
                return
            step_id = int(match.group(1))
            self._run(lambda: (store.delete_step(step_id), {"status": "deleted", "step_id": step_id})[1])

        def do_POST(self) -> None:
            body = self._body()
            if match := re.fullmatch(r"/api/routes/(\d+)/chat", self.path):
                route_id = int(match.group(1))
                service = conversation
                if not body.get("use_ai", True):
                    service = SopConversationService(
                        store,
                        documents,
                        assistant=NaturalLanguageSopAssistant(use_llm=False),
                    )
                self._run(lambda: service.chat(route_id, body["message"], worker=body["worker"]))
                return
            if match := re.fullmatch(r"/api/routes/(\d+)/documents/generate", self.path):
                self._run(lambda: documents.generate(int(match.group(1))))
                return
            if match := re.fullmatch(r"/api/routes/(\d+)/nl/preview", self.path):
                route_id = int(match.group(1))
                def preview() -> dict[str, Any]:
                    payload = store.get_route(route_id)
                    proposal, parser_kind = NaturalLanguageSopAssistant(use_llm=body.get("use_ai", True)).preview(body["instruction"], payload)
                    proposal_id = store.create_nl_proposal(route_id, body["instruction"], proposal, parser_kind=parser_kind, requested_by=body["worker"])
                    return {"proposal_id": proposal_id, "parser_kind": parser_kind, **proposal}
                self._run(preview)
                return
            if match := re.fullmatch(r"/api/proposals/(\d+)/apply", self.path):
                self._run(lambda: store.apply_nl_proposal(int(match.group(1)), reviewer=body["worker"]))
                return
            if match := re.fullmatch(r"/api/routes/(\d+)/media", self.path):
                route_id = int(match.group(1))
                def upload() -> dict[str, Any]:
                    try:
                        data = base64.b64decode(body["data_base64"], validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise ValueError("图片数据不是有效 Base64") from exc
                    return store.upload_media_asset(route_id, original_name=body["original_name"], mime_type=body["mime_type"], data=data, uploaded_by=body["uploaded_by"], source_note=body.get("source_note", ""))
                self._run(upload)
                return
            if self.path == "/api/media/link":
                self._run(lambda: {"status": "draft_linked", "link_id": store.link_media_asset(int(body["step_id"]), int(body["asset_id"]), caption=body.get("caption", ""))})
                return
            if match := re.fullmatch(r"/api/steps/(\d+)/confirm", self.path):
                self._run(lambda: store.confirm_step(int(match.group(1)), reviewer=body["reviewer"], comment=body.get("comment", "")))
                return
            patterns = [
                (r"/api/routes/(\d+)/steps", lambda value: {"status": "added", "step_id": store.add_step(value, RouteStepDraft.model_validate(body))}),
                (r"/api/steps/(\d+)/split", lambda value: self._split(value, body)),
                (r"/api/routes/(\d+)/reorder", lambda value: (store.reorder_steps(value, body["ordered_step_ids"]), {"status": "reordered", "route_id": value})[1]),
                (r"/api/routes/(\d+)/merge", lambda value: (store.merge_steps(value, body["target_step_id"], body["source_step_ids"], reviewer=body["reviewer"]), {"status": "merged", "route_id": value})[1]),
                (r"/api/routes/(\d+)/reviews", lambda value: {"status": "draft", "review_session_id": store.create_review_session(value, body["reviewer"], body.get("comment", ""))}),
                (r"/api/reviews/(\d+)/submit", lambda value: (store.submit_review(value), {"status": "under_review", "review_session_id": value})[1]),
                (r"/api/reviews/(\d+)/approve", lambda value: {"status": "approved", "route_id": store.approve(value, approved_by=body["approved_by"], approval_scope=body["approval_scope"], confirmation_token=body.get("confirmation_token")), "approval_scope": body["approval_scope"]}),
                (r"/api/reviews/(\d+)/reject", lambda value: (store.reject(value, reviewer=body["reviewer"], comment=body["comment"]), {"status": "rejected", "review_session_id": value})[1]),
                (r"/api/routes/(\d+)/revision", lambda value: {"status": "draft", "route_id": store.create_revision(value, created_by=body["reviewer"]), "parent_route_id": value}),
            ]
            for pattern, call in patterns:
                if match := re.fullmatch(pattern, self.path):
                    self._run(lambda call=call, value=int(match.group(1)): call(value))
                    return
            self._send(404, {"detail": "not found"})

        @staticmethod
        def _split(step_id: int, body: dict[str, Any]) -> dict[str, Any]:
            parent = _find_step(store, step_id)
            child = RouteStepDraft.model_validate(body)
            child.parent_step_code = parent["step_code"]
            return {"status": "split", "parent_step_id": step_id, "child_step_id": store.add_step(parent["route_id"], child)}

    return ThreadingHTTPServer((host, port), ReviewHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local SOP route human-review workbench.")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    server = create_builtin_server(args.db, args.host, args.port)
    print(f"SOP review workbench: http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


LEGACY_REVIEW_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SOP 工艺路线人工审核工作台</title>
<style>
:root{--ink:#12263a;--blue:#1f5d8f;--pale:#eef4f8;--line:#ccd8e2;--warn:#fff4d6;--bad:#8f2f2f}*{box-sizing:border-box}
body{margin:0;font-family:"Microsoft YaHei",sans-serif;color:var(--ink);background:#f5f7f9}.top{padding:18px 26px;background:#173b57;color:white}.top h1{margin:0 0 5px;font-size:22px}.top p{margin:0;opacity:.8}
.layout{display:grid;grid-template-columns:310px 1fr 410px;gap:12px;padding:12px;min-height:calc(100vh - 86px)}.panel{background:white;border:1px solid var(--line);border-radius:8px;padding:14px;overflow:auto}.panel h2{font-size:15px;margin:0 0 12px}.muted{color:#617487;font-size:12px}.product,.step,.section{padding:9px;border:1px solid var(--line);border-radius:6px;margin-bottom:7px;cursor:pointer}.product:hover,.step:hover,.section:hover,.active{border-color:var(--blue);background:var(--pale)}
button{border:0;border-radius:5px;padding:8px 11px;background:var(--blue);color:white;cursor:pointer;margin:3px}button.secondary{background:#617487}button.danger{background:var(--bad)}button.warn{background:#9b6a00}button.formal{background:#274c2c}input,textarea,select{width:100%;padding:8px;border:1px solid var(--line);border-radius:5px;font:inherit}textarea{min-height:95px;resize:vertical}.field{margin-bottom:10px}.field label{display:block;font-weight:700;font-size:12px;margin-bottom:4px}.badge{display:inline-block;padding:3px 7px;border-radius:12px;background:var(--pale);font-size:11px}.unknown{background:var(--warn);padding:8px;border-radius:5px;font-size:12px}.toolbar{display:flex;flex-wrap:wrap;gap:3px;margin-bottom:10px}.evidence{font-size:11px;border-left:3px solid var(--blue);padding-left:8px;margin:6px 0}.status{padding:8px;background:var(--pale);border-radius:5px;margin-bottom:10px}.risk{border:2px solid #8f2f2f;background:#fff0f0;padding:7px;margin-top:8px;border-radius:6px}
</style></head><body><div class="top"><h1>SOP 工艺路线人工审核工作台</h1><p>产品身份、可变工序、字段来源、unknown、审批与新修订均在本地 SQLite 留痕；批准前不得复用。</p></div>
<div class="layout"><section class="panel"><h2>产品与路线</h2><div id="products"></div><hr><h2>路线级审核 Section</h2><div class="muted">产品信息、BOM、设备治具、参数、QC、包装、IE、签核分别审核。</div><div id="sections"></div></section><section class="panel"><h2>可变工序树</h2><div class="toolbar"><button onclick="addStep()">新增工序</button><button class="secondary" onclick="move(-1)">上移</button><button class="secondary" onclick="move(1)">下移</button><button class="warn" onclick="splitStep()">拆分为子步骤</button><button class="warn" onclick="mergePrevious()">与前一步合并</button><button class="danger" onclick="deleteStep()">删除</button></div><div id="routeStatus" class="status">请选择产品</div><div id="steps"></div></section><section class="panel"><h2>逐字段审核</h2><div id="editor" class="muted">选择一个工序或路线级section查看字段、来源、冲突和unknown。</div><hr><div class="toolbar"><button onclick="startReview()">保存审核草稿</button><button onclick="submitReview()">提交审核</button><button class="formal" onclick="approveFormal()">正式生产批准</button><button class="danger" onclick="rejectReview()">驳回</button><button class="secondary" onclick="newRevision()">创建新修订</button></div><div class="risk"><b>演示批准风险隔离</b><br><span class="muted">允许保留blocking unknown；默认检索不返回，不代表生产许可。</span><br><button class="danger" onclick="approveDemo()">仅作 demonstration_only 批准</button></div><div id="message" class="muted"></div></section></div>
<script>
let products=[],route=null,currentStep=null,currentSection=null,sessionId=null;
const jfetch=async(url,opt={})=>{let r=await fetch(url,{headers:{'Content-Type':'application/json'},...opt});let j=await r.json();if(!r.ok)throw Error(j.detail||r.statusText);return j};
async function loadProducts(){products=await jfetch('/api/products');document.getElementById('products').innerHTML=products.map(p=>`<div class="product" onclick="loadRoute(${p.latest_route_id})"><b>${p.product_code}</b><br><span class="muted">${p.product_name}</span><br><span class="badge">${p.process_family_code}</span></div>`).join('')}
async function loadRoute(id){route=await jfetch('/api/routes/'+id);document.getElementById('routeStatus').innerHTML=`<b>${route.route.product_code}</b> v${route.route.version} · ${route.route.status} · ${route.route.source_kind}<br><span class="muted">${route.route.route_summary}</span>`;renderSteps();renderSections();currentStep=null;currentSection=null;renderEditor()}
function renderSteps(){document.getElementById('steps').innerHTML=route.steps.map((s,i)=>`<div class="step ${currentStep&&currentStep.id===s.id?'active':''}" onclick="selectStep(${s.id})"><b>${i+1}. ${s.step_code}</b> ${s.title}<br><span class="muted">${s.parent_step_id?'子步骤':'顶层'} · ${s.review_state}</span></div>`).join('')}
function renderSections(){document.getElementById('sections').innerHTML=(route.sections||[]).map(s=>`<div class="section ${currentSection&&currentSection.id===s.id?'active':''}" onclick="selectSection(${s.id})"><b>${s.section_type}</b><br><span class="muted">v${s.version} · ${s.review_state} · unknown ${(s.unknowns_json||[]).length}</span></div>`).join('')}
function selectStep(id){currentStep=route.steps.find(s=>s.id===id);currentSection=null;renderSteps();renderSections();renderEditor()}
function selectSection(id){currentSection=route.sections.find(s=>s.id===id);currentStep=null;renderSteps();renderSections();renderEditor()}
const fields=['title','action','why','inputs','materials','tool_equipment','fixtures','parameters','method','quality_check','acceptance_criteria','safety','record_output','exception','unknowns','review_state','reviewer_comment'];
function val(s,f){let key={inputs:'input_json',materials:'material_json',tool_equipment:'tool_equipment_json',fixtures:'fixture_json',parameters:'parameter_json',method:'method_json',quality_check:'quality_check_json',acceptance_criteria:'acceptance_criteria_json',safety:'safety_json',record_output:'record_output_json',exception:'exception_json',unknowns:'unknowns_json'}[f]||f;let v=s[key];return typeof v==='string'?v:JSON.stringify(v,null,2)}
function renderEditor(){if(currentSection){renderSectionEditor();return}if(!currentStep){document.getElementById('editor').innerHTML='选择一个工序或路线级section查看并审核。';return}let html=fields.map(f=>`<div class="field"><label>${f}</label><textarea id="f_${f}">${val(currentStep,f)}</textarea><button onclick="saveField('${f}')">保存并确认字段</button></div>`).join('');html+=`<button onclick="confirmWholeStep()">确认整步已完成审核</button>`;let prov=route.provenance.filter(p=>p.route_step_id===currentStep.id).map(p=>`<div class="evidence">${p.field_name} · ${p.source_path||('批准路线 '+p.source_route_id)} · 置信 ${p.confidence} · 冲突 ${p.conflict_status}</div>`).join('')||'<div class="muted">当前无字段级来源；审核时必须补录。</div>';document.getElementById('editor').innerHTML=html+'<h3>来源与复用</h3>'+prov}
function renderSectionEditor(){let s=currentSection;document.getElementById('editor').innerHTML=`<div class="status"><b>${s.section_type}</b> · v${s.version} · ${s.review_state}</div><div class="field"><label>content_json</label><textarea id="section_content">${JSON.stringify(s.content_json,null,2)}</textarea></div><div class="field"><label>字段来源</label><textarea id="section_sources">${JSON.stringify(s.source_json,null,2)}</textarea></div><div class="field"><label>冲突</label><textarea id="section_conflicts">${JSON.stringify(s.conflicts_json,null,2)}</textarea></div><div class="field"><label>结构化 unknown</label><textarea id="section_unknowns">${JSON.stringify(s.unknowns_json,null,2)}</textarea></div><div class="field"><label>审核意见</label><textarea id="section_comment">${s.reviewer_comment||''}</textarea></div><div class="toolbar"><button onclick="saveSection('confirmed')">保存并确认</button><button class="warn" onclick="saveSection('needs_revision')">要求修改</button><button class="danger" onclick="saveSection('rejected')">驳回section</button></div><div class="muted">每次保存都会创建新版本并写入review_decision；旧版本不覆盖。</div>`}
async function saveField(f){let id=currentStep.id,raw=document.getElementById('f_'+f).value,v=raw;if(['inputs','materials','tool_equipment','fixtures','parameters','method','quality_check','acceptance_criteria','safety','record_output','exception','unknowns'].includes(f))v=JSON.parse(raw);await jfetch('/api/steps/'+id,{method:'PATCH',body:JSON.stringify({step_id:id,field_name:f,value:v,decision:'confirmed',comment:'人工工作台逐字段修改'})});await loadRoute(route.route.id);selectStep(id);msg('字段已保存并留痕')}
async function confirmWholeStep(){let id=currentStep.id;await jfetch('/api/steps/'+id,{method:'PATCH',body:JSON.stringify({step_id:id,field_name:'review_state',value:'confirmed',decision:'confirmed',comment:'人工确认整步全部字段'})});await loadRoute(route.route.id);selectStep(id);msg('整步已确认')}
async function saveSection(state){let id=currentSection.id,reviewer=prompt('审核人身份');if(!reviewer)return;let body={content:JSON.parse(document.getElementById('section_content').value),review_state:state,reviewer_comment:document.getElementById('section_comment').value,sources:JSON.parse(document.getElementById('section_sources').value),conflicts:JSON.parse(document.getElementById('section_conflicts').value),unknowns:JSON.parse(document.getElementById('section_unknowns').value),reviewer,decision:state==='unreviewed'?'needs_revision':state};let r=await jfetch('/api/sections/'+id,{method:'PATCH',body:JSON.stringify(body)});await loadRoute(route.route.id);selectSection(r.section_id);msg('section新版本已保存并留痕')}
async function addStep(){let code=prompt('新工序代码');let title=prompt('新工序名称');if(!code||!title)return;let s={step_code:code,sequence_no:route.steps.length+1,title,action:'由人工审核员补充具体动作。',why:'由人工审核员补充工序目的。',inputs:[],materials:[],tool_equipment:[],fixtures:[],parameters:[],method:['在审核工作台补充可执行子步骤。'],quality_check:['由品质工程师补充检查方法。'],acceptance_criteria:['批准前补充受控合格判据。'],safety:['由EHS/工艺工程师确认。'],record_output:['人工审核记录。'],exception:['异常时隔离并提交人工判定。'],unknowns:[{field_name:'new_step_content',reason:'新增工序尚未完成资料核对。',owner_role:'工艺工程师',required_evidence:'受控流程卡及现场确认记录',blocking:true}]};await jfetch(`/api/routes/${route.route.id}/steps`,{method:'POST',body:JSON.stringify(s)});await loadRoute(route.route.id)}
async function deleteStep(){if(!currentStep||!confirm('删除当前工序？'))return;await jfetch('/api/steps/'+currentStep.id,{method:'DELETE'});await loadRoute(route.route.id)}
async function splitStep(){if(!currentStep)return;let code=prompt('子步骤代码',currentStep.step_code+'.1');let title=prompt('子步骤名称');if(!title)return;let s={step_code:code,sequence_no:Number(currentStep.sequence_no)+0.1,title,action:'人工拆分后的子步骤动作。',why:'把原工序拆成可执行单元。',inputs:[],materials:[],tool_equipment:[],fixtures:[],parameters:[],method:['补充子步骤动作。'],quality_check:['补充检查方法。'],acceptance_criteria:['补充合格判据。'],safety:['补充安全要求。'],record_output:['拆分审核记录。'],exception:['异常隔离。'],unknowns:[{field_name:'split_content',reason:'拆分内容需要工艺工程师确认。',owner_role:'工艺工程师',required_evidence:'现场作业分解和受控流程卡',blocking:true}]};await jfetch('/api/steps/'+currentStep.id+'/split',{method:'POST',body:JSON.stringify(s)});await loadRoute(route.route.id)}
async function move(delta){if(!currentStep)return;let selectedId=currentStep.id,ids=route.steps.map(s=>s.id),i=ids.indexOf(selectedId),j=i+delta;if(j<0||j>=ids.length)return;[ids[i],ids[j]]=[ids[j],ids[i]];await jfetch(`/api/routes/${route.route.id}/reorder`,{method:'POST',body:JSON.stringify({ordered_step_ids:ids})});await loadRoute(route.route.id);selectStep(selectedId)}
async function mergePrevious(){if(!currentStep)return;let i=route.steps.findIndex(s=>s.id===currentStep.id);if(i<1)return alert('没有前一步');await jfetch(`/api/routes/${route.route.id}/merge`,{method:'POST',body:JSON.stringify({target_step_id:route.steps[i-1].id,source_step_ids:[currentStep.id],reviewer:'web_reviewer'})});await loadRoute(route.route.id)}
async function startReview(){let r=await jfetch(`/api/routes/${route.route.id}/reviews`,{method:'POST',body:JSON.stringify({reviewer:'web_reviewer',comment:'人工逐项审核'})});sessionId=r.review_session_id;msg('审核草稿已保存，session='+sessionId)}
async function submitReview(){if(!sessionId)return alert('请先保存审核草稿');await jfetch(`/api/reviews/${sessionId}/submit`,{method:'POST'});await loadRoute(route.route.id);msg('已提交审核')}
async function approveFormal(){if(!sessionId)return alert('请先提交审核');let reviewer=prompt('正式批准人身份（不能为空）');if(!reviewer)return;let token=prompt(`输入正式确认token：FORMAL_APPROVE:${route.route.product_code}`);if(!token)return;await jfetch(`/api/reviews/${sessionId}/approve`,{method:'POST',body:JSON.stringify({approved_by:reviewer,approval_scope:'formal_production',confirmation_token:token})});await loadRoute(route.route.id);msg('正式批准闸门全部通过并生成不可变快照')}
async function approveDemo(){if(!sessionId)return alert('请先提交审核');let reviewer=prompt('演示批准人身份');if(!reviewer)return;await jfetch(`/api/reviews/${sessionId}/approve`,{method:'POST',body:JSON.stringify({approved_by:reviewer,approval_scope:'demonstration_only'})});await loadRoute(route.route.id);msg('已作为演示审批快照锁定；存在风险隔离，不代表正式生产批准')}
async function rejectReview(){if(!sessionId)return alert('请先保存审核草稿');let c=prompt('驳回原因');if(!c)return;await jfetch(`/api/reviews/${sessionId}/reject`,{method:'POST',body:JSON.stringify({reviewer:'web_reviewer',comment:c})});await loadRoute(route.route.id);msg('已驳回；不会进入approved索引')}
async function newRevision(){let r=await jfetch(`/api/routes/${route.route.id}/revision`,{method:'POST',body:JSON.stringify({reviewer:'web_reviewer',comment:'创建新修订'})});await loadProducts();await loadRoute(r.route_id)}
function msg(t){document.getElementById('message').textContent=t}loadProducts();
</script></body></html>"""

# The worker UI is kept in a standalone file so layout and accessibility can be
# reviewed without touching the HTTP and approval code above.
REVIEW_HTML = Path(__file__).with_name("workbench.html").read_text(encoding="utf-8")
SIMPLE_REVIEW_HTML = Path(__file__).with_name("simple_workbench.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
