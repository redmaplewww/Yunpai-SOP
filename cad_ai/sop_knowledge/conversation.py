from __future__ import annotations

from typing import Any

from .documents import SopDocumentService
from .nl_assistant import NaturalLanguageSopAssistant
from .store import SopKnowledgeStore


class SopConversationService:
    """Apply a worker's plain-language edit and regenerate the DOCX in one request."""

    def __init__(
        self,
        store: SopKnowledgeStore,
        documents: SopDocumentService,
        *,
        assistant: NaturalLanguageSopAssistant | None = None,
    ) -> None:
        self.store = store
        self.documents = documents
        self.assistant = assistant or NaturalLanguageSopAssistant()

    def chat(self, route_id: int, message: str, *, worker: str) -> dict[str, Any]:
        worker = worker.strip()
        message = message.strip()
        if not worker:
            raise ValueError("请填写工作人员姓名或工号")
        if len(message) < 2:
            raise ValueError("请直接描述要修改的部分")

        route_payload = self.store.get_route(route_id)
        revision_from: int | None = None
        if route_payload["route"]["status"] == "approved":
            revision_from = route_id
            route_id = self.store.create_revision(route_id, created_by=worker)
            route_payload = self.store.get_route(route_id)
            self.store.append_chat_message(
                route_id,
                "system",
                f"原路线 {revision_from} 已锁定，系统已创建可编辑修订版 {route_id}。",
                metadata={"revision_from": revision_from},
            )

        history = self.store.list_chat_messages(route_id, limit=24)
        self.store.append_chat_message(route_id, "user", message, metadata={"worker": worker})
        proposal, parser_kind = self.assistant.preview(message, route_payload, history=history)
        has_changes = any(
            proposal.get(key)
            for key in ("changes", "new_steps", "section_changes", "image_refs")
        )
        applied: dict[str, Any] = {
            "route_id": route_id,
            "changed": [],
            "added": [],
            "section_changed": [],
            "linked_media": [],
            "unresolved_images": [],
            "status": "answered_without_edit",
        }
        proposal_id: int | None = None
        if has_changes:
            proposal_id = self.store.create_nl_proposal(
                route_id,
                message,
                proposal,
                parser_kind=parser_kind,
                requested_by=worker,
            )
            applied = self.store.apply_nl_proposal(proposal_id, reviewer=worker)
            document = self.documents.generate(route_id)
        else:
            document = self.documents.latest(route_id, generate_if_missing=True)

        response_text = str(proposal.get("assistant_message") or proposal.get("summary") or "已处理。")
        details = self._describe_changes(proposal, applied)
        metadata = {
            "parser_kind": parser_kind,
            "proposal_id": proposal_id,
            "summary": proposal.get("summary", ""),
            "judgement": proposal.get("judgement", []),
            "warnings": proposal.get("warnings", []),
            "changes": details,
            "applied": applied,
            "document": document,
            "docx_regenerated": bool(has_changes),
            "requires_human_confirmation": True,
            "revision_from": revision_from,
        }
        self.store.append_chat_message(route_id, "assistant", response_text, metadata=metadata)
        return {
            "route_id": route_id,
            "message": response_text,
            "parser_kind": parser_kind,
            "summary": proposal.get("summary", ""),
            "judgement": proposal.get("judgement", []),
            "warnings": proposal.get("warnings", []),
            "changes": details,
            "applied": applied,
            "document": document,
            "docx_regenerated": bool(has_changes),
            "requires_human_confirmation": True,
            "revision_from": revision_from,
        }

    @staticmethod
    def _describe_changes(proposal: dict[str, Any], applied: dict[str, Any]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for item in proposal.get("changes", []):
            value = item.get("value")
            if isinstance(value, list):
                preview = "；".join(str(part) for part in value[:3])
                if len(value) > 3:
                    preview += f"；另 {len(value) - 3} 项"
            else:
                preview = str(value)
            result.append({
                "target": f"{item.get('step_code', '')} {item.get('step_title', '')}".strip(),
                "field": str(item.get("field_name", "")),
                "change": preview,
                "reason": str(item.get("reason", "")),
            })
        for item in applied.get("added", []):
            result.append({
                "target": f"新增工序 {item.get('step_code', '')}",
                "field": "工序",
                "change": str(item.get("title", "")),
                "reason": "按对话要求加入可编辑草稿",
            })
        for item in applied.get("section_changed", []):
            result.append({
                "target": str(item.get("section_type", "")),
                "field": "路线章节",
                "change": f"已创建 v{item.get('version')}",
                "reason": "保留旧版本并合并对话修改",
            })
        return result
