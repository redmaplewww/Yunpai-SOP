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
    def workbench() -> str:
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

    @app.delete("/api/media/{asset_id}")
    def delete_media(asset_id: int) -> dict[str, Any]:
        return guard(lambda: store.delete_media_asset(asset_id))

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
            if match := re.fullmatch(r"/api/media/(\d+)", self.path):
                asset_id = int(match.group(1))
                self._run(lambda: store.delete_media_asset(asset_id))
                return
            if match := re.fullmatch(r"/api/steps/(\d+)", self.path):
                step_id = int(match.group(1))
                self._run(lambda: (store.delete_step(step_id), {"status": "deleted", "step_id": step_id})[1])
                return
            self._send(404, {"detail": "not found"})

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


SIMPLE_REVIEW_HTML = Path(__file__).with_name("simple_workbench.html").read_text(encoding="utf-8")
REVIEW_HTML = Path(__file__).with_name("workbench.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
