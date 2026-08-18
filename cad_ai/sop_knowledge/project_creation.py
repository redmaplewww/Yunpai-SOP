from __future__ import annotations

import base64
import http.client
import json
import re
import time
import urllib.error
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .documents import SopDocumentService
from .llm_wire import request_json_object
from .models import ProductIdentity, RouteDraft, RouteSectionDraft, RouteStepDraft, UnknownItem
from .nl_assistant import NaturalLanguageSopAssistant
from .store import ROUTE_SECTION_TYPES, SopKnowledgeStore


STEP_LIST_FIELDS = (
    "inputs",
    "materials",
    "tool_equipment",
    "fixtures",
    "method",
    "quality_check",
    "acceptance_criteria",
    "safety",
    "record_output",
    "exception",
)
GROUNDING_REQUIRED_FIELDS = {"inputs", "materials", "tool_equipment", "fixtures", "parameters"}
STEP_PLACEHOLDERS = {
    "action": "待补充：请说明这道工序要做什么",
    "why": "待补充：请说明这道工序的目的",
    "method": "待补充：请说明具体操作步骤",
    "quality_check": "待补充：请说明检查方法",
    "acceptance_criteria": "待补充：请说明怎样算合格",
    "record_output": "待补充：请说明需要填写的记录",
    "exception": "待补充：请说明发生异常时怎么办",
}
ROUTE_SECTION_LABELS = {
    "product_identity": "产品信息",
    "bom_material": "物料/BOM",
    "equipment_fixture": "设备治具",
    "process_parameter": "工艺参数",
    "quality_control": "质量控制",
    "packaging_label": "包装标签",
    "ie_timing": "IE工时",
    "release_signoff": "发布签核",
}
SENSITIVE_FACT_PATTERN = re.compile(
    r"(?:设备型号|机型|工时|秒|分钟|小时|单价|价格|人数|\d+\s*人|良率|合格率|质量结论)",
    re.IGNORECASE,
)
PROJECT_IMAGE_MIME_TYPES = {"image/png", "image/jpeg"}
PROJECT_IMAGE_LIMIT = 24
PROJECT_IMAGE_BYTES_LIMIT = 10 * 1024 * 1024
PROJECT_IMAGE_PACKAGE_LIMIT = 50 * 1024 * 1024


class _ProjectStepValidationError(ValueError):
    def __init__(self, index: int, title: str) -> None:
        self.index = index
        self.title = title or "未命名工序"
        super().__init__(f"第 {index} 道工序“{self.title}”内容不完整，请返回继续补充。")


class NaturalLanguageProjectService:
    """Create reviewable project drafts from worker language without inventing facts."""

    def __init__(
        self,
        store: SopKnowledgeStore,
        documents: SopDocumentService | None = None,
        *,
        timeout: int = 75,
        max_llm_attempts: int = 2,
        draft_ttl_seconds: int = 30 * 60,
    ) -> None:
        self.store = store
        self.documents = documents or SopDocumentService(store)
        self.timeout = timeout
        self.max_llm_attempts = max_llm_attempts
        self.draft_ttl_seconds = draft_ttl_seconds
        self._drafts: dict[str, dict[str, Any]] = {}

    def preview(
        self,
        description: str,
        *,
        previous_draft_id: str | None = None,
        use_ai: bool = True,
        images: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        text = description.strip()
        if len(text) < 4:
            raise ValueError("请先简单说一下产品是什么、准备怎么做。")
        self._purge_expired_drafts()
        previous = self._drafts.get(previous_draft_id or "")
        intake_images = self._validated_images(images if images is not None else (previous or {}).get("images", []))
        source_text = "\n".join(
            item for item in (str(previous.get("source_text", "")) if previous else "", text) if item
        )
        config = NaturalLanguageSopAssistant._llm_config() if use_ai else None
        if not config:
            return self._unavailable(
                "AI 服务当前未连接。完整工艺路线不会使用离线规则猜测，请恢复 AI 后重试。"
            )
        try:
            raw = self._request_with_retry(
                text,
                source_text=source_text,
                previous_draft=previous.get("draft") if previous else None,
                config=config,
                images=intake_images,
            )
        except (OSError, ValueError, http.client.HTTPException, urllib.error.URLError, TimeoutError) as error:
            reason = NaturalLanguageSopAssistant._fallback_reason(error)
            return self._unavailable(
                f"AI 服务暂时不可用（{reason}）。这次没有生成工序，也没有写入项目。"
            )

        draft = self._sanitize(raw, source_text, intake_images)
        duplicate_matches = self._duplicate_matches(draft.get("product_name", ""), draft.get("product_code", ""))
        preflight_error: _ProjectStepValidationError | None = None
        if draft["intent"] == "create_project" and draft["steps"]:
            try:
                self._build_route_steps(draft["steps"])
            except _ProjectStepValidationError as error:
                preflight_error = error
                draft["unknowns"].append(
                    {
                        "label": f"{error.title}：工序内容不完整",
                        "question": str(error),
                        "scope": "step",
                        "step_title": error.title,
                        "blocking": True,
                    }
                )
                draft["unknowns"] = self._dedupe_unknowns(draft["unknowns"])
                draft["warnings"] = list(dict.fromkeys([*draft["warnings"], str(error)]))
        blocking = [item for item in draft["unknowns"] if item.get("blocking")]
        can_create = (
            draft["intent"] == "create_project"
            and bool(draft["product_name"])
            and bool(draft["steps"])
            and preflight_error is None
            and not blocking
            and not duplicate_matches
        )
        draft_id = uuid.uuid4().hex
        draft["can_create"] = can_create
        draft["duplicate_matches"] = duplicate_matches
        draft["parser_kind"] = "llm"
        draft["requires_human_confirmation"] = True
        self._drafts[draft_id] = {
            "created_at": time.time(),
            "source_text": source_text,
            "draft": deepcopy(draft),
            "images": deepcopy(intake_images),
        }
        self._trim_drafts()
        return {"draft_id": draft_id, **draft}

    def confirm(self, draft_id: str, *, worker: str) -> dict[str, Any]:
        reviewer = worker.strip()
        if not reviewer:
            raise ValueError("请填写工作人员姓名或工号。")
        self._purge_expired_drafts()
        stored = self._drafts.get(draft_id)
        if not stored:
            raise ValueError("项目草稿已过期，请重新让 AI 整理。")
        draft = deepcopy(stored["draft"])
        intake_images = self._validated_images(stored.get("images", []))
        duplicates = self._duplicate_matches(draft.get("product_name", ""), draft.get("product_code", ""))
        if duplicates:
            raise ValueError("发现同名或同编号项目，请先选择新型号、复制项目或创建修订版。")
        if not draft.get("can_create"):
            raise ValueError("当前信息还不足以创建项目，请先补充页面中标出的内容。")

        product_code = draft.get("product_code") or self._next_draft_product_code()
        identity = ProductIdentity(
            product_code=product_code,
            product_name=draft["product_name"],
            process_family_code="natural_language_draft",
            description=draft.get("route_summary", ""),
        )
        steps = self._build_route_steps(draft["steps"])
        route = RouteDraft(
            product=identity,
            route_name=draft.get("route_name") or f"{identity.product_name}工艺路线",
            route_summary=draft.get("route_summary") or "根据现场人员自然语言描述整理的待核对草稿。",
            source_kind="manual",
            status="draft",
            approval_scope="none",
            version=1,
            steps=steps,
            route_unknowns=[self._route_unknown(item) for item in draft["unknowns"] if not item.get("step_title")],
        )
        self.store.upsert_product(identity, {"creation_source": "worker_natural_language"})
        route_id = self.store.create_route(route, created_by=reviewer)
        self._create_initial_route_sections(route_id, identity, reviewer, draft=draft)
        created_route = self.store.get_route(route_id)
        steps_by_title = {self._normalize(item["title"]): item for item in created_route["steps"]}
        assets_by_source: dict[str, dict[str, Any]] = {}
        for image in intake_images:
            assets_by_source[image["source_id"]] = self.store.upload_media_asset(
                route_id,
                original_name=image["original_name"],
                mime_type=image["mime_type"],
                data=image["data"],
                uploaded_by=reviewer,
                source_note="新建项目批量录入，图片关联待人工核对。",
            )
        linked_sources: set[str] = set()
        for assignment in draft.get("image_assignments", []):
            asset = assets_by_source.get(assignment["source_id"])
            step = steps_by_title.get(self._normalize(assignment["target_step_title"]))
            if not asset or not step:
                continue
            self.store.link_media_asset(step["id"], asset["id"], caption=assignment.get("caption", ""))
            linked_sources.add(assignment["source_id"])
        document: dict[str, Any] | None = None
        document_error = ""
        try:
            document = self.documents.generate(route_id)
        except Exception as error:
            document_error = str(error)
        self._drafts.pop(draft_id, None)
        return {
            "status": "draft_created",
            "route_id": route_id,
            "product_code": product_code,
            "product_name": identity.product_name,
            "route_status": "draft",
            "step_count": len(steps),
            "image_count": len(intake_images),
            "linked_image_count": len(linked_sources),
            "unmatched_image_count": len(intake_images) - len(linked_sources),
            "ie_timing_count": len(draft.get("ie_timing", [])),
            "unknown_count": len(draft.get("unknowns", [])),
            "image_assignments": draft.get("image_assignments", []),
            "review_state": "needs_revision",
            "document": document,
            "document_error": document_error,
        }

    def _request_with_retry(
        self,
        instruction: str,
        *,
        source_text: str,
        previous_draft: dict[str, Any] | None,
        config: dict[str, str],
        images: list[dict[str, Any]],
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_llm_attempts):
            try:
                return self._request_llm(
                    instruction,
                    source_text=source_text,
                    previous_draft=previous_draft,
                    config=config,
                    images=images,
                )
            except Exception as error:
                last_error = error
                if attempt + 1 >= self.max_llm_attempts or not NaturalLanguageSopAssistant._is_retryable(error):
                    raise
                time.sleep(0.5)
        assert last_error is not None
        raise last_error

    def _request_llm(
        self,
        instruction: str,
        *,
        source_text: str,
        previous_draft: dict[str, Any] | None,
        config: dict[str, str],
        images: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system = (
            "你是面向一线工人的制造 SOP 整理助手。只输出 JSON，不输出 Markdown。"
            "用户会用自然语言描述一个新产品怎么做，你要把已明确说出的事实整理成项目草稿，而不是编写理想工艺。"
            "输出键为 intent、assistant_message、product_name、product_code、route_name、route_summary、steps、"
            "ie_timing、image_assignments、unknowns、warnings。"
            "intent 只能是 create_project 或 question；如果用户只是咨询而不是描述要创建的项目，使用 question，steps 留空。"
            "steps 每项可含 title、action、why、inputs、materials、tool_equipment、fixtures、parameters、method、"
            "quality_check、acceptance_criteria、safety、record_output、exception。数组字段一律输出数组。"
            "unknowns 每项包含 label、question、scope、step_title、blocking；只有缺产品名称或完全没有工序时 blocking=true。"
            "ie_timing 每项包含 step_title、duration、source_text；只整理用户原文明确写出的工时，source_text 必须逐字引用原文。"
            "image_assignments 每项包含 source_id、target_step_title、caption、reason。只能使用 uploaded_images 中真实存在的"
            "source_id；target_step_title 必须是本次 steps 中的一道工序。caption 要简短描述图中实际动作，不确定就不要关联。"
            "表达必须通俗简短，禁止出现 JSON、数据库、字段名、内部 ID、模型提示词和推理过程。"
            "不得补造用户没说过的设备或治具型号、材料牌号、工艺参数、尺寸、工时、价格、人数、良率、质量结论、"
            "现场事实、批准或发布状态。未提供的内容不要填默认值，改为加入 unknowns。"
            "图片只能用于建议它属于哪道工序和简短图片说明，不能从图片推断参数、工时、型号、质量结论或批准状态。"
            "如果提供了 previous_draft，要结合本轮补充返回一份完整的新草稿，不要只返回增量。"
        )
        user = json.dumps(
            {
                "latest_worker_message": instruction,
                "all_worker_descriptions": source_text,
                "previous_draft": previous_draft,
                "uploaded_images": [
                    {"source_id": image["source_id"], "original_name": image["original_name"]}
                    for image in images
                ],
            },
            ensure_ascii=False,
        )
        llm_images = [
            {
                "source_id": image["source_id"],
                "original_name": image["original_name"],
                "data_url": f"data:{image['mime_type']};base64,{base64.b64encode(image['data']).decode('ascii')}",
            }
            for image in images
        ]
        return request_json_object(
            system=system,
            user=user,
            config=config,
            timeout=self.timeout,
            images=llm_images,
        )

    def _sanitize(
        self,
        raw: dict[str, Any],
        source_text: str,
        images: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        intent = "question" if str(raw.get("intent", "")).strip() == "question" else "create_project"
        product_name = self._text(raw.get("product_name"), 100)
        if product_name and not self._grounded(product_name, source_text):
            product_name = ""
        product_code = self._text(raw.get("product_code"), 80)
        if product_code and not self._grounded(product_code, source_text):
            product_code = ""
        steps: list[dict[str, Any]] = []
        warnings = self._text_list(raw.get("warnings"), limit=20)
        for raw_step in self._as_list(raw.get("steps"))[:80]:
            if not isinstance(raw_step, dict):
                continue
            title = self._text(raw_step.get("title"), 100)
            if not title:
                continue
            step = {
                "title": title,
                "action": self._normalize_required_step_text(
                    "action", self._safe_text(raw_step.get("action"), source_text)
                ),
                "why": self._normalize_required_step_text(
                    "why", self._safe_text(raw_step.get("why"), source_text)
                ),
                "parameters": self._grounded_parameters(raw_step.get("parameters"), source_text),
            }
            for field in STEP_LIST_FIELDS:
                values = self._text_list(raw_step.get(field), limit=24)
                if field in GROUNDING_REQUIRED_FIELDS:
                    removed = [item for item in values if not self._grounded(item, source_text)]
                    values = [item for item in values if self._grounded(item, source_text)]
                    if removed:
                        warnings.append(f"“{title}”中有未在描述里出现的{self._field_label(field)}，已保持待补充。")
                step[field] = values
            steps.append(step)

        unknowns = []
        for item in self._as_list(raw.get("unknowns"))[:100]:
            if not isinstance(item, dict):
                continue
            label = self._text(item.get("label"), 100)
            question = self._text(item.get("question"), 180)
            if not label and not question:
                continue
            unknowns.append(
                {
                    "label": label or question,
                    "question": question or label,
                    "scope": "step" if str(item.get("scope", "")) == "step" else "project",
                    "step_title": self._text(item.get("step_title"), 100),
                    "blocking": bool(item.get("blocking")),
                }
            )
        if not product_name:
            unknowns.append(
                {
                    "label": "产品名称或型号还没有说清楚",
                    "question": "请补充产品名称或型号。",
                    "scope": "project",
                    "step_title": "",
                    "blocking": True,
                }
            )
        if not steps:
            unknowns.append(
                {
                    "label": "还没有识别出明确工序",
                    "question": "请至少说出一道这个产品要经过的工序。",
                    "scope": "project",
                    "step_title": "",
                    "blocking": True,
                }
            )
        for step in steps:
            for field in ("action", "method", "quality_check", "acceptance_criteria", "record_output", "exception"):
                if step.get(field):
                    continue
                unknowns.append(
                    {
                        "label": f"{step['title']}：{self._field_label(field)}待补充",
                        "question": f"请补充“{step['title']}”的{self._field_label(field)}。",
                        "scope": "step",
                        "step_title": step["title"],
                        "blocking": False,
                    }
                )
        image_by_source = {image["source_id"]: image for image in images or []}
        step_titles: dict[str, list[str]] = {}
        for step in steps:
            step_titles.setdefault(self._normalize(step["title"]), []).append(step["title"])
        image_assignments = []
        assigned_sources: set[str] = set()
        for item in self._as_list(raw.get("image_assignments"))[:PROJECT_IMAGE_LIMIT]:
            if not isinstance(item, dict):
                continue
            source_id = self._text(item.get("source_id"), 64)
            target_key = self._normalize(self._text(item.get("target_step_title"), 100))
            targets = step_titles.get(target_key, [])
            image = image_by_source.get(source_id)
            if not image or len(targets) != 1 or source_id in assigned_sources:
                continue
            image_assignments.append(
                {
                    "source_id": source_id,
                    "original_name": image["original_name"],
                    "target_step_title": targets[0],
                    "caption": self._text(item.get("caption"), 120),
                    "reason": self._text(item.get("reason"), 160),
                    "link_state": "draft",
                }
            )
            assigned_sources.add(source_id)
        unassigned_images = [
            {"source_id": image["source_id"], "original_name": image["original_name"]}
            for image in images or []
            if image["source_id"] not in assigned_sources
        ]
        if unassigned_images:
            warnings.append(f"{len(unassigned_images)} 张图片暂时无法确定工序，已保留为未分配素材。")

        ie_timing = []
        for item in self._as_list(raw.get("ie_timing"))[:80]:
            if not isinstance(item, dict):
                continue
            source_excerpt = self._text(item.get("source_text"), 300)
            if not source_excerpt or self._normalize(source_excerpt) not in self._normalize(source_text):
                continue
            target_key = self._normalize(self._text(item.get("step_title"), 100))
            targets = step_titles.get(target_key, [])
            duration = self._safe_text(item.get("duration"), source_excerpt, max_length=80)
            if len(targets) != 1 or not duration:
                continue
            ie_timing.append(
                {"工序": targets[0], "工时": duration, "来源原文": source_excerpt, "状态": "待人工核对"}
            )
        return {
            "intent": intent,
            "assistant_message": self._text(raw.get("assistant_message"), 260)
            or ("我已把你说的内容整理成工序草稿，请先核对顺序。" if intent == "create_project" else "这是一个咨询问题，没有创建项目草稿。"),
            "product_name": product_name,
            "product_code": product_code,
            "route_name": self._text(raw.get("route_name"), 120) or (f"{product_name}工艺路线" if product_name else "新项目工艺路线"),
            "route_summary": self._safe_text(raw.get("route_summary"), source_text, max_length=500),
            "steps": steps,
            "image_count": len(images or []),
            "image_assignments": image_assignments,
            "unassigned_images": unassigned_images,
            "ie_timing": ie_timing,
            "unknowns": self._dedupe_unknowns(unknowns),
            "warnings": list(dict.fromkeys(warnings)),
        }

    def _build_route_steps(self, items: list[dict[str, Any]]) -> list[RouteStepDraft]:
        steps: list[RouteStepDraft] = []
        for index, item in enumerate(items, start=1):
            try:
                steps.append(self._to_route_step(item, index))
            except (KeyError, TypeError, ValueError) as error:
                title = self._text(item.get("title") if isinstance(item, dict) else "", 100)
                raise _ProjectStepValidationError(index, title) from error
        return steps

    def _create_initial_route_sections(
        self,
        route_id: int,
        identity: ProductIdentity,
        reviewer: str,
        *,
        draft: dict[str, Any] | None = None,
    ) -> None:
        for section_type in ROUTE_SECTION_TYPES:
            label = ROUTE_SECTION_LABELS[section_type]
            content: dict[str, Any] = {"当前资料": "待人工补充"}
            if section_type == "product_identity":
                content = {
                    "产品名称": identity.product_name,
                    "产品编号": identity.product_code,
                    "产品规格": "待人工补充",
                }
            elif section_type == "ie_timing" and (draft or {}).get("ie_timing"):
                content = {
                    "工序工时": deepcopy(draft["ie_timing"]),
                    "核对状态": "待人工核对",
                }
            self.store.create_route_section(
                route_id,
                RouteSectionDraft(
                    section_type=section_type,
                    content=content,
                    review_state="needs_revision",
                    reviewer_comment="自然语言新建项目，待人工补充并核对。",
                    unknowns=[
                        UnknownItem(
                            field_name=section_type,
                            reason=f"新建项目尚未提供“{label}”的受控资料。",
                            owner_role="项目或工艺负责人",
                            required_evidence=f"上传并人工核对“{label}”相关的受控路线资料。",
                            blocking=True,
                        )
                    ],
                ),
                created_by=reviewer,
            )

    def _to_route_step(self, item: dict[str, Any], index: int) -> RouteStepDraft:
        unknowns: list[UnknownItem] = []
        action = self._normalize_required_step_text("action", item.get("action")) or STEP_PLACEHOLDERS["action"]
        why = self._normalize_required_step_text("why", item.get("why")) or STEP_PLACEHOLDERS["why"]
        values: dict[str, list[Any]] = {}
        for field in STEP_LIST_FIELDS:
            current = list(item.get(field) or [])
            if not current and field in STEP_PLACEHOLDERS:
                current = [STEP_PLACEHOLDERS[field]]
            values[field] = current
        for field in ("action", "why", "method", "quality_check", "acceptance_criteria", "record_output", "exception"):
            missing = (field in {"action", "why"} and not item.get(field)) or (
                field not in {"action", "why"} and not item.get(field)
            )
            if missing:
                unknowns.append(self._step_unknown(item["title"], field))
        return RouteStepDraft(
            step_code=f"OP{index:02d}",
            sequence_no=float(index),
            title=item["title"],
            action=action,
            why=why,
            inputs=values["inputs"],
            materials=values["materials"],
            tool_equipment=values["tool_equipment"],
            fixtures=values["fixtures"],
            parameters=list(item.get("parameters") or []),
            method=values["method"],
            quality_check=values["quality_check"],
            acceptance_criteria=values["acceptance_criteria"],
            safety=values["safety"],
            record_output=values["record_output"],
            exception=values["exception"],
            unknowns=unknowns,
            review_state="needs_revision",
            reviewer_comment="由自然语言新建项目整理，待人工逐项核对。",
        )

    def _duplicate_matches(self, product_name: str, product_code: str) -> list[dict[str, Any]]:
        normalized_name = self._normalize(product_name)
        normalized_code = self._normalize(product_code)
        matches = []
        for item in self.store.list_products():
            same_name = bool(normalized_name and self._normalize(item.get("product_name")) == normalized_name)
            same_code = bool(normalized_code and self._normalize(item.get("product_code")) == normalized_code)
            if not same_name and not same_code:
                continue
            status = "draft"
            route_id = item.get("latest_route_id")
            if route_id:
                try:
                    status = str(self.store.get_route(int(route_id))["route"]["status"])
                except (KeyError, TypeError, ValueError):
                    pass
            matches.append({**item, "route_status": status, "matched_by": "name" if same_name else "code"})
        return matches

    @staticmethod
    def _validated_images(images: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        items = list(images or [])
        if len(items) > PROJECT_IMAGE_LIMIT:
            raise ValueError(f"一次最多上传 {PROJECT_IMAGE_LIMIT} 张图片。")
        normalized: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        total_bytes = 0
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index} 张图片资料无效。")
            source_id = str(item.get("source_id") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source_id) or source_id in seen_sources:
                raise ValueError(f"第 {index} 张图片的来源编号无效或重复。")
            original_name = re.split(r"[/\\]", str(item.get("original_name") or ""))[-1].strip()[:180]
            mime_type = str(item.get("mime_type") or "").strip().lower()
            data = item.get("data")
            if not original_name:
                raise ValueError(f"第 {index} 张图片缺少文件名。")
            if mime_type not in PROJECT_IMAGE_MIME_TYPES:
                raise ValueError("项目图片仅支持 PNG 或 JPEG。")
            if not isinstance(data, bytes) or not data or len(data) > PROJECT_IMAGE_BYTES_LIMIT:
                raise ValueError("每张图片必须大于 0 且不超过 10MB。")
            if mime_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError(f"{original_name} 不是有效的 PNG 图片。")
            if mime_type == "image/jpeg" and not data.startswith(b"\xff\xd8"):
                raise ValueError(f"{original_name} 不是有效的 JPEG 图片。")
            total_bytes += len(data)
            if total_bytes > PROJECT_IMAGE_PACKAGE_LIMIT:
                raise ValueError("全部项目图片合计不能超过 50MB。")
            seen_sources.add(source_id)
            normalized.append(
                {
                    "source_id": source_id,
                    "original_name": original_name,
                    "mime_type": mime_type,
                    "data": data,
                }
            )
        return normalized

    def _next_draft_product_code(self) -> str:
        prefix = datetime.now(timezone.utc).strftime("DRAFT-%Y%m%d")
        existing = {str(item.get("product_code", "")).upper() for item in self.store.list_products()}
        for index in range(1, 10000):
            candidate = f"{prefix}-{index:03d}"
            if candidate.upper() not in existing:
                return candidate
        raise ValueError("当天临时项目编号已用完，请联系管理员。")

    @staticmethod
    def _unavailable(message: str) -> dict[str, Any]:
        return {
            "draft_id": None,
            "intent": "create_project",
            "assistant_message": message,
            "product_name": "",
            "product_code": "",
            "route_name": "",
            "route_summary": "",
            "steps": [],
            "image_count": 0,
            "image_assignments": [],
            "unassigned_images": [],
            "ie_timing": [],
            "unknowns": [],
            "warnings": [message],
            "duplicate_matches": [],
            "can_create": False,
            "parser_kind": "unavailable",
            "requires_human_confirmation": True,
        }

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if value in (None, ""):
            return []
        return [value]

    @classmethod
    def _text_list(cls, value: Any, *, limit: int) -> list[str]:
        return [text for item in cls._as_list(value) if (text := cls._text(item, 240))][:limit]

    @staticmethod
    def _text(value: Any, max_length: int) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()[:max_length]

    @classmethod
    def _safe_text(cls, value: Any, source_text: str, *, max_length: int = 240) -> str:
        text = cls._text(value, max_length)
        if not text:
            return ""
        if SENSITIVE_FACT_PATTERN.search(text) and not cls._grounded(text, source_text):
            return ""
        ungrounded_numbers = [number for number in re.findall(r"\d+(?:\.\d+)?", text) if number not in source_text]
        return "" if ungrounded_numbers else text

    @classmethod
    def _normalize_required_step_text(cls, field: str, value: Any) -> str:
        text = cls._text(value, 240)
        if not text or len(text) >= 4:
            return text
        if field == "action":
            return f"完成“{text}”这项操作"
        if field == "why":
            return f"本工序用于{text}"
        return text

    @classmethod
    def _grounded_parameters(cls, value: Any, source_text: str) -> list[dict[str, Any]]:
        grounded = []
        for item in cls._as_list(value)[:24]:
            if not isinstance(item, dict):
                continue
            text = " ".join(str(part) for part in item.values() if part not in (None, ""))
            if text and cls._grounded(text, source_text):
                grounded.append(item)
        return grounded

    @staticmethod
    def _normalize(value: Any) -> str:
        return re.sub(r"[\s\-_—·.]+", "", str(value or "")).lower()

    @classmethod
    def _grounded(cls, value: str, source_text: str) -> bool:
        candidate = cls._normalize(value)
        source = cls._normalize(source_text)
        if not candidate:
            return False
        if candidate in source:
            return True
        tokens = [token for token in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", value) if len(token) >= 2]
        return bool(tokens and all(cls._normalize(token) in source for token in tokens))

    @staticmethod
    def _field_label(field: str) -> str:
        return {
            "action": "工序动作",
            "why": "工序目的",
            "inputs": "输入资料",
            "materials": "材料",
            "tool_equipment": "工具或设备",
            "fixtures": "治具",
            "parameters": "工艺参数",
            "method": "操作步骤",
            "quality_check": "检查方法",
            "acceptance_criteria": "合格标准",
            "safety": "安全要求",
            "record_output": "记录要求",
            "exception": "异常处理",
        }.get(field, field)

    @classmethod
    def _step_unknown(cls, step_title: str, field: str) -> UnknownItem:
        return UnknownItem(
            field_name=field,
            reason=f"工作人员尚未说明“{step_title}”的{cls._field_label(field)}。",
            owner_role="现场工艺或质量负责人",
            required_evidence=f"补充并人工确认“{step_title}”的{cls._field_label(field)}。",
            blocking=False,
        )

    @staticmethod
    def _route_unknown(item: dict[str, Any]) -> UnknownItem:
        return UnknownItem(
            field_name=str(item.get("label") or "route_information")[:100],
            reason=str(item.get("question") or item.get("label") or "路线资料待补充")[:300],
            owner_role="项目或工艺负责人",
            required_evidence="补充对应资料并由人工确认。",
            blocking=bool(item.get("blocking")),
        )

    @staticmethod
    def _dedupe_unknowns(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        result = []
        for item in items:
            key = (str(item.get("step_title", "")), str(item.get("label", "")))
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _purge_expired_drafts(self) -> None:
        threshold = time.time() - self.draft_ttl_seconds
        for draft_id in [key for key, value in self._drafts.items() if value["created_at"] < threshold]:
            self._drafts.pop(draft_id, None)

    def _trim_drafts(self) -> None:
        if len(self._drafts) <= 100:
            return
        ordered = sorted(self._drafts, key=lambda key: self._drafts[key]["created_at"])
        for draft_id in ordered[:-100]:
            self._drafts.pop(draft_id, None)
