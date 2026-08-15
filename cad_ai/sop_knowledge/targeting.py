from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal


TargetStatus = Literal["resolved", "likely", "needs_choice", "not_found"]

FIELD_HINTS = (
    "工序名称", "工序动作", "工序目的", "作业指导", "作业步骤", "操作方法",
    "工具", "设备", "治具", "材料", "输入资料", "工艺参数", "检查方法",
    "质量检查", "合格判据", "合格标准", "验收标准", "安全要求", "安全注意",
    "记录要求", "异常处理",
)
EDIT_MARKERS = (
    "修改", "改为", "补充", "补上", "新增", "删除", "合并", "拆分", "调整",
    "写入", "更新", "改一下", "完善", "增加",
)
ROUTE_SECTION_HINTS = (
    "路线章节", "产品信息章节", "物料章节", "设备治具章节", "工艺参数章节",
    "质量控制章节", "包装标签章节", "工时章节", "签核章节", "整条路线",
)
PRONOUN_HINTS = ("它", "这个", "该工序", "该步骤", "刚才那道", "上一道", "这道")
CONTINUATION_HINTS = ("再", "继续", "另外", "还要", "也", "同时")
STEP_JSON_FIELDS = (
    "input_json", "material_json", "tool_equipment_json", "fixture_json", "parameter_json",
    "method_json", "quality_check_json", "acceptance_criteria_json", "safety_json",
    "record_output_json", "exception_json",
)


@dataclass(frozen=True)
class TargetCandidate:
    step_id: int
    step_code: str
    sequence_no: float
    title: str
    reason: str
    score: int = field(default=0, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_code": self.step_code,
            "sequence_no": self.sequence_no,
            "title": self.title,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TargetResolution:
    status: TargetStatus
    target_phrase: str = ""
    selected_step_id: int | None = None
    candidates: tuple[TargetCandidate, ...] = ()
    excluded_step_ids: tuple[int, ...] = ()
    used_context: bool = False
    effective_instruction: str = ""
    pending_message_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target_phrase": self.target_phrase,
            "selected_step_id": self.selected_step_id,
            "candidates": [item.to_dict() for item in self.candidates],
            "excluded_step_ids": list(self.excluded_step_ids),
            "used_context": self.used_context,
            "pending_message_id": self.pending_message_id,
        }


def is_step_edit_request(message: str) -> bool:
    """Return whether an edit needs an existing route step to be selected first."""
    text = message.strip()
    if not any(marker in text for marker in EDIT_MARKERS):
        return False
    labels = "|".join(re.escape(value) for value in sorted(FIELD_HINTS, key=len, reverse=True))
    edits_existing_field = re.search(rf"(?:新增|添加|增加|插入|新建).{{0,16}}(?:工序|步骤)(?:的)?(?:{labels})", text)
    if not edits_existing_field and re.search(r"(?:新增|添加|增加|插入|新建).{0,16}(?:工序|步骤)", text):
        return False
    if any(marker in text for marker in ROUTE_SECTION_HINTS) and not re.search(r"(?:工序|步骤)", text):
        return False
    return bool(
        re.search(r"(?:工序|步骤|第\s*[一二三四五六七八九十两0-9]+\s*步|[A-Za-z]{1,8}[-_]?[0-9]{1,4})", text)
        or any(label in text for label in FIELD_HINTS)
        or any(marker in text for marker in PRONOUN_HINTS + CONTINUATION_HINTS)
    )


def resolve(
    message: str,
    route_steps: list[dict[str, Any]],
    history: list[dict[str, Any]],
    selected_step_id: int | None = None,
    pending_message_id: int | None = None,
) -> TargetResolution:
    text = message.strip()
    steps = sorted(route_steps, key=lambda item: (float(item.get("sequence_no", 0)), int(item.get("id", 0))))
    pending = _pending_request(history, pending_message_id)
    if selected_step_id is not None:
        return _resolve_clicked_candidate(text, steps, pending, selected_step_id)
    if pending and _looks_like_candidate_selection(text, pending[2]):
        chosen = _select_pending_candidate(text, pending[2])
        if chosen is not None:
            return TargetResolution(
                status="resolved",
                target_phrase=str(chosen.get("title", "")),
                selected_step_id=int(chosen["step_id"]),
                candidates=tuple(_candidate_from_dict(item) for item in pending[2]),
                effective_instruction=pending[1],
                pending_message_id=pending[0],
            )

    excluded_ids = tuple(sorted(_excluded_step_ids(text, steps)))
    explicit = _explicit_step(text, steps)
    if explicit and int(explicit["id"]) not in excluded_ids:
        candidate = _candidate(explicit, explicit["reason"], 120)
        return TargetResolution(
            status="resolved",
            target_phrase=explicit["phrase"],
            selected_step_id=candidate.step_id,
            candidates=(candidate,),
            excluded_step_ids=excluded_ids,
            effective_instruction=text,
        )

    phrase = _target_phrase(text)
    ranked = _rank_candidates(phrase, text, steps, excluded_ids)
    if ranked:
        title_matches = [item for item in ranked if item.score >= 80]
        if len(title_matches) == 1:
            return TargetResolution(
                status="resolved",
                target_phrase=phrase,
                selected_step_id=title_matches[0].step_id,
                candidates=tuple(ranked[:3]),
                excluded_step_ids=excluded_ids,
                effective_instruction=text,
            )
        if len(title_matches) > 1:
            return TargetResolution(
                status="needs_choice",
                target_phrase=phrase,
                candidates=tuple(title_matches[:3]),
                excluded_step_ids=excluded_ids,
                effective_instruction=text,
            )
        if len(ranked) == 1 or ranked[0].score - ranked[1].score >= 20:
            return TargetResolution(
                status="likely",
                target_phrase=phrase,
                candidates=tuple(ranked[:3]),
                excluded_step_ids=excluded_ids,
                effective_instruction=text,
            )
        return TargetResolution(
            status="needs_choice",
            target_phrase=phrase,
            candidates=tuple(ranked[:3]),
            excluded_step_ids=excluded_ids,
            effective_instruction=text,
        )

    context_step = _recent_step(steps, history)
    may_use_context = any(marker in text for marker in PRONOUN_HINTS) or (
        not phrase and any(marker in text for marker in CONTINUATION_HINTS)
    )
    if context_step and may_use_context and int(context_step["id"]) not in excluded_ids:
        candidate = _candidate(context_step, "沿用刚才已定位的工序", 70)
        return TargetResolution(
            status="resolved",
            selected_step_id=candidate.step_id,
            candidates=(candidate,),
            excluded_step_ids=excluded_ids,
            used_context=True,
            effective_instruction=text,
        )
    return TargetResolution(
        status="not_found",
        target_phrase=phrase,
        excluded_step_ids=excluded_ids,
        effective_instruction=text,
    )


def _resolve_clicked_candidate(
    message: str,
    steps: list[dict[str, Any]],
    pending: tuple[int, str, list[dict[str, Any]]] | None,
    selected_step_id: int,
) -> TargetResolution:
    if pending is None:
        return TargetResolution(status="not_found", effective_instruction=message)
    allowed = {int(item.get("step_id", 0)) for item in pending[2]}
    step = next((item for item in steps if int(item.get("id", 0)) == selected_step_id), None)
    if selected_step_id not in allowed or step is None:
        return TargetResolution(
            status="not_found",
            candidates=tuple(_candidate_from_dict(item) for item in pending[2]),
            effective_instruction=pending[1],
            pending_message_id=pending[0],
        )
    return TargetResolution(
        status="resolved",
        target_phrase=str(step.get("title", "")),
        selected_step_id=selected_step_id,
        candidates=tuple(_candidate_from_dict(item) for item in pending[2]),
        effective_instruction=pending[1],
        pending_message_id=pending[0],
    )


def _pending_request(
    history: list[dict[str, Any]],
    expected_message_id: int | None,
) -> tuple[int, str, list[dict[str, Any]]] | None:
    for item in reversed(history):
        if item.get("role") != "assistant":
            continue
        item_id = int(item.get("id", 0))
        metadata = item.get("metadata_json") or {}
        if expected_message_id is not None and int(metadata.get("resolved_pending_message_id") or 0) == expected_message_id:
            return None
        if expected_message_id is not None and item_id != expected_message_id:
            continue
        target = metadata.get("target_resolution") or {}
        instruction = str(metadata.get("pending_instruction", "")).strip()
        candidates = target.get("candidates")
        if target.get("status") in {"likely", "needs_choice"} and instruction and isinstance(candidates, list):
            return item_id, instruction, [value for value in candidates if isinstance(value, dict)]
        return None
    return None


def _looks_like_candidate_selection(text: str, candidates: list[dict[str, Any]]) -> bool:
    compact = _compact(text)
    if any(marker in text for marker in ("第一个", "第二个", "第三个", "最后一个", "最后那个", "选", "不是")):
        return True
    return any(
        _compact(item.get("step_code", "")) in compact or _compact(item.get("title", "")) in compact
        for item in candidates
        if _compact(item.get("step_code", "")) or _compact(item.get("title", ""))
    )


def _select_pending_candidate(text: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    if "最后一个" in text or "最后那个" in text:
        return candidates[-1]
    ordinal_map = {"一": 1, "二": 2, "三": 3}
    match = re.search(r"第\s*([一二三123])\s*(?:个|项|道)?", text)
    if match:
        index = ordinal_map.get(match.group(1), int(match.group(1)) if match.group(1).isdigit() else 0)
        if 1 <= index <= len(candidates):
            return candidates[index - 1]
    compact = _compact(text)
    matches = [
        item for item in candidates
        if (_compact(item.get("step_code", "")) and _compact(item.get("step_code", "")) in compact)
        or (_compact(item.get("title", "")) and _compact(item.get("title", "")) in compact)
    ]
    return matches[0] if len(matches) == 1 else None


def _explicit_step(text: str, steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    code_match = re.search(r"(?<![A-Za-z0-9])([A-Za-z]{1,8}[-_]?[0-9]{1,4})(?![A-Za-z0-9])", text)
    if code_match:
        reference = _compact(code_match.group(1))
        step = next((item for item in steps if _compact(item.get("step_code", "")) == reference), None)
        if step:
            return {**step, "phrase": code_match.group(1), "reason": "工序编号完全一致"}
    patterns = (
        r"第\s*(\d+)\s*(?:道|个)?\s*(?:工序|步骤|步)",
        r"(?:工序|步骤)\s*(?:第\s*)?(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        position = int(match.group(1))
        if 1 <= position <= len(steps):
            return {**steps[position - 1], "phrase": match.group(0), "reason": "工序序号完全一致"}
    chinese = re.search(r"第\s*([一二三四五六七八九十两]+)\s*(?:道|个)?\s*(?:工序|步骤|步)", text)
    if chinese:
        position = _chinese_number(chinese.group(1))
        if 1 <= position <= len(steps):
            return {**steps[position - 1], "phrase": chinese.group(0), "reason": "工序序号完全一致"}
    return None


def _target_phrase(text: str) -> str:
    correction = re.search(r"不是\s*[^，,。；;]{1,20}[，,]\s*(?:而?是)\s*([^，,。；;：:]{1,24})", text)
    if correction:
        return _clean_phrase(correction.group(1))
    labels = "|".join(re.escape(value) for value in sorted(FIELD_HINTS, key=len, reverse=True))
    match = re.search(
        rf"(?:请|麻烦)?(?:把|将|给)?\s*([^，,。；;：:]{{1,32}}?)\s*(?:的|里|中)?\s*(?:{labels})",
        text,
    )
    if match:
        return _clean_phrase(match.group(1))
    match = re.search(r"([^，,。；;：:\s]{1,24}?)(?:工序|步骤)", text)
    return _clean_phrase(match.group(1)) if match else ""


def _clean_phrase(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^(?:请|麻烦|把|将|给|修改|补充|补上|完善|更新|新增|添加|增加|插入|新建)+", "", text)
    text = re.sub(r"(?:工序|步骤)(?:的)?$", "", text)
    text = re.sub(r"(?:后|里的|中的|的)$", "", text)
    return text.strip(" 的，把将给：:")


def _rank_candidates(
    phrase: str,
    message: str,
    steps: list[dict[str, Any]],
    excluded_ids: tuple[int, ...],
) -> list[TargetCandidate]:
    target = _compact(phrase)
    if len(target) < 2:
        return []
    ranked: list[TargetCandidate] = []
    for step in steps:
        step_id = int(step.get("id", 0))
        if step_id in excluded_ids:
            continue
        title = _compact(step.get("title", ""))
        code = _compact(step.get("step_code", ""))
        content = _compact(_step_content(step))
        score = 0
        reason = ""
        if target == code or target == title:
            score, reason = 105, "名称与描述完全一致"
        elif target in title or (len(title) >= 2 and title in target):
            score, reason = 80, f"工序名称包含“{phrase}”"
        elif target in content:
            score, reason = 50, f"作业内容包含“{phrase}”"
        elif target in _compact(message) and _shared_cjk_terms(target, title):
            score, reason = 35, f"工序名称与“{phrase}”相关"
        if score:
            ranked.append(_candidate(step, reason, score))
    return sorted(ranked, key=lambda item: (-item.score, item.sequence_no, item.step_id))


def _excluded_step_ids(text: str, steps: list[dict[str, Any]]) -> set[int]:
    phrases = re.findall(r"(?:不是|不要(?:选|改)?|排除)\s*([^，,。；;：:\s]{1,20})", text)
    excluded: set[int] = set()
    for phrase in phrases:
        target = _compact(_clean_phrase(phrase))
        if len(target) < 2:
            continue
        for step in steps:
            if target in _compact(step.get("title", "")) or target == _compact(step.get("step_code", "")):
                excluded.add(int(step.get("id", 0)))
    return excluded


def _recent_step(steps: list[dict[str, Any]], history: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = {int(item.get("id", 0)): item for item in steps}
    for item in reversed(history):
        if item.get("role") != "assistant":
            continue
        metadata = item.get("metadata_json") or {}
        target = metadata.get("target_resolution") or {}
        selected = target.get("selected_step_id")
        if selected is not None and int(selected) in valid:
            return valid[int(selected)]
        changed = (metadata.get("applied") or {}).get("changed") or []
        if changed:
            step_id = int(changed[-1].get("step_id", 0))
            if step_id in valid:
                return valid[step_id]
    return None


def _step_content(step: dict[str, Any]) -> str:
    values: list[str] = [str(step.get("action", "")), str(step.get("why", ""))]
    for field_name in STEP_JSON_FIELDS:
        values.extend(_flatten_text(step.get(field_name)))
    return " ".join(values)


def _flatten_text(value: Any) -> list[str]:
    if isinstance(value, dict):
        result: list[str] = []
        for key, child in value.items():
            result.extend((str(key), *_flatten_text(child)))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for child in value:
            result.extend(_flatten_text(child))
        return result
    return [str(value)] if value is not None else []


def _candidate(step: dict[str, Any], reason: str, score: int) -> TargetCandidate:
    return TargetCandidate(
        step_id=int(step.get("id", 0)),
        step_code=str(step.get("step_code", "")),
        sequence_no=float(step.get("sequence_no", 0)),
        title=str(step.get("title", "")),
        reason=reason,
        score=score,
    )


def _candidate_from_dict(value: dict[str, Any]) -> TargetCandidate:
    return TargetCandidate(
        step_id=int(value.get("step_id", 0)),
        step_code=str(value.get("step_code", "")),
        sequence_no=float(value.get("sequence_no", 0)),
        title=str(value.get("title", "")),
        reason=str(value.get("reason", "")),
    )


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value).lower())


def _shared_cjk_terms(left: str, right: str) -> bool:
    terms = {left[index:index + 2] for index in range(max(0, len(left) - 1))}
    return any(term in right for term in terms)


def _chinese_number(value: str) -> int:
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(value, 0)
