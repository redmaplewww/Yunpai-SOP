from __future__ import annotations

import re
from typing import Any

from .documents import SopDocumentService
from .nl_assistant import NaturalLanguageSopAssistant
from .store import SopKnowledgeStore
from .targeting import TargetResolution, is_step_edit_request, resolve


FIELD_LABELS = {
    "title": "工序名称",
    "action": "工序动作",
    "why": "工序目的",
    "method": "作业步骤",
    "tool_equipment": "工具、设备",
    "fixtures": "治具",
    "materials": "材料",
    "inputs": "输入资料",
    "parameters": "工艺参数",
    "quality_check": "检查方法",
    "acceptance_criteria": "合格判据",
    "safety": "安全要求",
    "record_output": "记录要求",
    "exception": "异常处理",
}


def is_read_only_request(message: str) -> bool:
    """Prevent explicit questions from becoming edits if the model over-interprets them."""
    text = message.strip().lower()
    if any(marker in text for marker in (
        "不要修改", "不修改", "仅回答", "只回答", "不要写入", "不写入", "仅说明", "只说明",
    )):
        return True
    change_markers = ("修改", "改为", "补充", "补上", "新增", "删除", "合并", "拆分", "调整", "写入", "更新")
    if any(marker in text for marker in change_markers):
        return False
    question_markers = ("？", "?", "什么", "哪些", "如何", "为什么", "多少", "查看", "介绍", "哪里", "位置", "找不到")
    return any(marker in text for marker in question_markers)


def is_location_query(message: str) -> bool:
    text = message.strip().lower()
    return any(marker in text for marker in ("哪里", "位置", "在哪", "找不到", "怎么看")) and not any(
        marker in text for marker in ("修改", "改为", "补充", "新增", "删除", "写入", "更新")
    )


def has_explicit_new_step_intent(message: str) -> bool:
    """Require an unmistakable add command before offline parsing may create steps."""
    text = message.strip()
    labels = "|".join(re.escape(value) for value in sorted(FIELD_LABELS.values(), key=len, reverse=True))
    if re.search(rf"(?:新增|添加|增加|插入|新建).{{0,16}}(?:工序|步骤)(?:的)?(?:{labels})", text):
        return False
    return bool(re.search(r"(?:新增|添加|增加|插入|新建).{0,16}(?:工序|步骤)", text))


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

    def chat(
        self,
        route_id: int,
        message: str,
        *,
        worker: str,
        selected_step_id: int | None = None,
        pending_message_id: int | None = None,
    ) -> dict[str, Any]:
        worker = worker.strip()
        message = message.strip()
        if not worker:
            raise ValueError("请填写工作人员姓名或工号")
        if len(message) < 2:
            raise ValueError("请直接描述要修改的部分")

        route_payload = self.store.get_route(route_id)
        read_only = is_read_only_request(message)
        location_query = is_location_query(message)
        revision_from: int | None = None
        if route_payload["route"]["status"] == "approved" and not read_only:
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
        target_resolution: TargetResolution | None = None
        target_required = False
        if not read_only and not location_query:
            target_resolution = resolve(
                message,
                route_payload.get("steps", []),
                history,
                selected_step_id=selected_step_id,
                pending_message_id=pending_message_id,
            )
            target_required = (
                selected_step_id is not None
                or target_resolution.pending_message_id is not None
                or is_step_edit_request(message)
            )
        self.store.append_chat_message(
            route_id,
            "user",
            message,
            metadata={
                "worker": worker,
                "selected_step_id": selected_step_id,
                "pending_message_id": pending_message_id,
            },
        )
        if target_required and target_resolution and target_resolution.status != "resolved":
            return self._request_target_confirmation(
                route_id,
                message,
                target_resolution,
                revision_from=revision_from,
            )

        effective_message = (
            target_resolution.effective_instruction
            if target_resolution and target_resolution.status == "resolved"
            else message
        )
        if location_query:
            proposal = self._location_reply(history, route_payload)
            parser_kind = "reference"
        else:
            assistant_route = route_payload
            if target_resolution and target_resolution.selected_step_id is not None:
                assistant_route = {
                    **route_payload,
                    "_locked_target_step_id": target_resolution.selected_step_id,
                    "_target_resolution": target_resolution.to_dict(),
                }
            proposal, parser_kind = self.assistant.preview(effective_message, assistant_route, history=history)
            fallback_new_steps_are_guarded_below = (
                parser_kind in {"deterministic", "deterministic_fallback"}
                and bool(proposal.get("new_steps"))
                and not has_explicit_new_step_intent(message)
            )
            if (
                target_resolution
                and target_resolution.selected_step_id is not None
                and not fallback_new_steps_are_guarded_below
                and not self._proposal_targets_locked_step(proposal, target_resolution.selected_step_id)
            ):
                retry_route = {**assistant_route, "_target_retry": True}
                proposal, parser_kind = self.assistant.preview(effective_message, retry_route, history=history)
                if not self._proposal_targets_locked_step(proposal, target_resolution.selected_step_id):
                    proposal = self._blocked_target_mismatch(target_resolution)
        proposal["judgement"] = self._text_list(proposal.get("judgement"))
        proposal["warnings"] = self._text_list(proposal.get("warnings"))
        if (
            parser_kind in {"deterministic", "deterministic_fallback"}
            and proposal.get("new_steps")
            and not has_explicit_new_step_intent(message)
        ):
            blocked_count = len(proposal["new_steps"])
            warnings = proposal.get("warnings", [])
            if isinstance(warnings, str):
                warnings = [warnings]
            elif not isinstance(warnings, list):
                warnings = []
            warnings.append(
                "离线解析检测到可能的新增工序，但你没有明确要求新增工序，本次未写入。"
                "如需新增，请明确说明“新增工序：工序名称”。"
            )
            judgement = proposal.get("judgement", [])
            if isinstance(judgement, str):
                judgement = [judgement]
            elif not isinstance(judgement, list):
                judgement = []
            judgement.append(f"已阻止 {blocked_count} 个不明确的新增工序。")
            proposal = {
                **proposal,
                "assistant_message": "当前描述没有明确要求新增工序，系统未改动 SOP。请说明第几道工序的哪个内容需要补充。",
                "summary": "离线解析未执行不明确的新增工序请求。",
                "judgement": judgement,
                "new_steps": [],
                "warnings": warnings,
            }
        if read_only:
            proposal = {**proposal, "changes": [], "new_steps": [], "section_changes": [], "image_refs": []}
            if not location_query:
                proposal["assistant_message"] = "已按只读请求完成说明，未写入 SOP 草稿，也未重新生成 DOCX。"
            proposal.setdefault("judgement", []).append("该请求仅用于查询，系统已阻止任何 SOP 写入。")
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
                effective_message,
                proposal,
                parser_kind=parser_kind,
                requested_by=worker,
            )
            applied = self.store.apply_nl_proposal(proposal_id, reviewer=worker)
            document = self.documents.generate(route_id)
        else:
            document = self.documents.latest(route_id, generate_if_missing=True)

        response_text = str(proposal.get("assistant_message") or proposal.get("summary") or "已处理。")
        details = self._describe_changes(proposal, applied, route_payload)
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
            "target_resolution": target_resolution.to_dict() if target_resolution else None,
            "resolved_pending_message_id": target_resolution.pending_message_id if target_resolution else None,
        }
        assistant_message_id = self.store.append_chat_message(route_id, "assistant", response_text, metadata=metadata)
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
            "proposal_id": proposal_id,
            "assistant_message_id": assistant_message_id,
            "target_resolution": target_resolution.to_dict() if target_resolution else None,
        }

    def _request_target_confirmation(
        self,
        route_id: int,
        instruction: str,
        resolution: TargetResolution,
        *,
        revision_from: int | None,
    ) -> dict[str, Any]:
        if resolution.status == "likely":
            message = "我找到一个最可能的工序，请先确认。确认后才会修改 SOP。"
            judgement = ["已根据工序名称和现有作业内容找到最接近的结果。"]
        elif resolution.status == "needs_choice":
            message = "这句话可能指向多道工序，请先选一个。选定后才会修改 SOP。"
            judgement = ["存在多个合理目标，系统没有擅自写入。"]
        else:
            message = "暂时不能确定你说的是哪道工序，请补充工序编号或名称。"
            judgement = ["当前描述不足以安全定位工序，系统没有写入。"]
        target_payload = resolution.to_dict()
        document = self.documents.latest(route_id, generate_if_missing=True)
        metadata = {
            "parser_kind": "target_selection",
            "proposal_id": None,
            "summary": "等待人工确认目标工序。",
            "judgement": judgement,
            "warnings": [],
            "changes": [],
            "applied": {
                "route_id": route_id,
                "changed": [], "added": [], "section_changed": [], "linked_media": [],
                "unresolved_images": [], "status": "awaiting_target_confirmation",
            },
            "document": document,
            "docx_regenerated": False,
            "requires_human_confirmation": True,
            "revision_from": revision_from,
            "pending_instruction": instruction,
            "target_resolution": target_payload,
        }
        assistant_message_id = self.store.append_chat_message(route_id, "assistant", message, metadata=metadata)
        return {
            "route_id": route_id,
            "message": message,
            "parser_kind": "target_selection",
            "summary": metadata["summary"],
            "judgement": judgement,
            "warnings": [],
            "changes": [],
            "applied": metadata["applied"],
            "document": document,
            "docx_regenerated": False,
            "requires_human_confirmation": True,
            "revision_from": revision_from,
            "proposal_id": None,
            "assistant_message_id": assistant_message_id,
            "target_resolution": target_payload,
        }

    @staticmethod
    def _proposal_targets_locked_step(proposal: dict[str, Any], locked_step_id: int) -> bool:
        if proposal.get("new_steps"):
            return False
        for key in ("changes", "image_refs"):
            for item in proposal.get(key, []):
                raw = item.get("step_id", item.get("step_ref"))
                try:
                    step_id = int(raw)
                except (TypeError, ValueError):
                    return False
                if step_id != locked_step_id:
                    return False
        return True

    @staticmethod
    def _blocked_target_mismatch(resolution: TargetResolution) -> dict[str, Any]:
        selected = next(
            (item for item in resolution.candidates if item.step_id == resolution.selected_step_id),
            None,
        )
        label = f"{selected.step_code} {selected.title}" if selected else "已选工序"
        return {
            "assistant_message": f"我没有把修改写入。AI 返回的工序与已确认的“{label}”不一致，请重试。",
            "judgement": [f"已锁定目标为 {label}，但 AI 两次返回了其他工序。"],
            "summary": "目标校验未通过，本次未修改 SOP。",
            "changes": [], "new_steps": [], "section_changes": [], "image_refs": [],
            "warnings": ["目标不一致，已阻止写入，DOCX 未重新生成。"],
            "requires_human_confirmation": True,
        }

    @staticmethod
    def _text_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _describe_changes(
        proposal: dict[str, Any],
        applied: dict[str, Any],
        route_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        steps_by_id = {int(step["id"]): step for step in route_payload.get("steps", [])}
        for item in proposal.get("changes", []):
            value = item.get("value")
            if isinstance(value, list):
                preview = "；".join(str(part) for part in value)
            else:
                preview = str(value)
            step = steps_by_id.get(int(item.get("step_id", 0)))
            sequence = int(float(step["sequence_no"])) if step else 0
            title = str(item.get("step_title", ""))
            field = str(item.get("field_name", ""))
            label = FIELD_LABELS.get(field, field)
            location = f"DOCX 第 {sequence + 1} 页 > {label}" if sequence else label
            result.append({
                "target": f"第 {sequence} 道：{title}" if sequence else title,
                "field": label,
                "change": preview,
                "reason": str(item.get("reason", "")),
                "location": location,
                "page_number": sequence + 1 if sequence else None,
                "field_key": field,
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

    @staticmethod
    def _location_reply(history: list[dict[str, Any]], route_payload: dict[str, Any]) -> dict[str, Any]:
        steps = route_payload.get("steps", [])
        by_code = {str(step.get("step_code", "")): step for step in steps}
        for item in reversed(history):
            if item.get("role") != "assistant":
                continue
            changes = item.get("metadata_json", {}).get("changes", [])
            locations: list[str] = []
            for change in changes:
                location = str(change.get("location", "")).strip()
                if location:
                    locations.append(f"{change.get('target', '')}，{location}")
                    continue
                target = str(change.get("target", ""))
                code = next((candidate for candidate in by_code if candidate and candidate in target), "")
                step = by_code.get(code)
                if step:
                    sequence = int(float(step["sequence_no"]))
                    label = FIELD_LABELS.get(str(change.get("field", "")), str(change.get("field", "")))
                    locations.append(f"第 {sequence} 道：{step['title']}，DOCX 第 {sequence + 1} 页 > {label}")
            if locations:
                return {
                    "assistant_message": "上一项修改的位置如下：\n" + "\n".join(locations) + "。\n可在左侧 DOCX 预览中直接查看对应页。",
                    "judgement": ["已根据上一条修改记录定位到对应页面。"],
                    "summary": "已提供修改位置。",
                    "changes": [], "new_steps": [], "section_changes": [], "image_refs": [], "warnings": [],
                }
        return {
            "assistant_message": "暂时没有找到可定位的上一条修改记录。请先完成一次修改，再询问“刚才改在哪里”。",
            "judgement": [], "summary": "未找到修改位置。",
            "changes": [], "new_steps": [], "section_changes": [], "image_refs": [], "warnings": [],
        }
