from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ARRAY_FIELDS = {
    "method",
    "quality_check",
    "acceptance_criteria",
    "safety",
    "record_output",
    "exception",
    "materials",
    "tool_equipment",
    "fixtures",
    "inputs",
    "parameters",
}
ALLOWED_FIELDS = ARRAY_FIELDS | {"title", "action", "why", "reviewer_comment"}
SECTION_TYPES = {
    "product_identity",
    "bom_material",
    "equipment_fixture",
    "process_parameter",
    "quality_control",
    "packaging_label",
    "ie_timing",
    "release_signoff",
}


class NaturalLanguageSopAssistant:
    """Turn worker language into a reviewable draft proposal; never approve content."""

    def __init__(
        self,
        *,
        use_llm: bool = True,
        timeout: int = 75,
        max_llm_attempts: int = 2,
    ) -> None:
        self.use_llm = use_llm
        self.timeout = timeout
        self.max_llm_attempts = max_llm_attempts

    def preview(
        self,
        instruction: str,
        route: dict[str, Any],
        *,
        history: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], str]:
        text = instruction.strip()
        if len(text) < 2:
            raise ValueError("请描述要修改的工序、作业指导或图片。")
        if self.use_llm:
            configured = self._llm_config()
            if configured:
                try:
                    proposal = self._llm_preview_with_retry(text, route, configured, history=history or [])
                    return self._sanitize(proposal, route), "llm"
                except (OSError, ValueError, http.client.HTTPException, urllib.error.URLError, TimeoutError) as error:
                    proposal = self._deterministic_preview(text, route)
                    proposal["warnings"].append(
                        "AI 服务暂时不可用，已使用离线规则解析；请重点核对预览。"
                        f" 原因：{self._fallback_reason(error)}。"
                    )
                    return self._sanitize(proposal, route), "deterministic_fallback"
        return self._sanitize(self._deterministic_preview(text, route), route), "deterministic"

    def status(self) -> dict[str, str]:
        return {
            "mode": "ai_with_offline_fallback" if self._llm_config() else "offline_rules",
            "credential": "configured" if self._llm_config() else "not_configured",
        }

    def _deterministic_preview(self, text: str, route: dict[str, Any]) -> dict[str, Any]:
        steps = route.get("steps", [])
        changes: list[dict[str, Any]] = []
        new_steps: list[dict[str, Any]] = []
        image_refs: list[dict[str, str]] = []
        warnings: list[str] = []

        targeted = re.search(
            r"(?:工序|步骤)\s*(第?\s*\d+|[A-Za-z]+[-_]?\d+|[^，。；;：:]{1,24})\s*(?:的)?\s*"
            r"(?:作业指导|作业步骤|操作方法|方法)\s*(?:改为|是|为|补充为|应为)\s*([^；;。\n]+)",
            text,
        )
        if targeted:
            ref, method = targeted.group(1).strip(), targeted.group(2).strip()
            step = self._match_step(ref, steps)
            if step:
                changes.append(self._change(step, "method", self._split_items(method), "人工描述的作业指导"))
            else:
                warnings.append(f"没有找到工序“{ref}”，未自动写入。")

        operations = self._capture_operation_list(text)
        methods = self._capture_list(text, ("对应的作业指导", "对应作业指导", "作业指导", "作业步骤", "操作方法"))
        images = self._capture_list(text, ("对应的图片", "对应图片", "图片", "照片"))
        if operations:
            if methods and len(methods) not in {1, len(operations)}:
                warnings.append("工序数量与作业指导数量不一致，未能一一对应的内容需要人工核对。")
            if images and len(images) not in {1, len(operations)}:
                warnings.append("工序数量与图片数量不一致，未能一一对应的图片需要人工核对。")
            for index, title in enumerate(operations):
                method = methods[index] if index < len(methods) else (methods[0] if len(methods) == 1 else "")
                image_ref = images[index] if index < len(images) else (images[0] if len(images) == 1 else "")
                step = self._match_step(title, steps)
                if step:
                    if method:
                        changes.append(self._change(step, "method", self._split_items(method), "按工序顺序匹配作业指导"))
                    if image_ref:
                        image_refs.append({"step_ref": str(step["id"]), "reference": image_ref})
                else:
                    new_steps.append({
                        "title": title,
                        "method": self._split_items(method) if method else [],
                        "image_ref": image_ref,
                        "position": "append",
                    })

        field_patterns = {
            "quality_check": ("检查方法", "质量检查", "怎么检查"),
            "acceptance_criteria": ("合格标准", "合格判据", "验收标准"),
            "safety": ("安全注意", "安全要求"),
            "exception": ("异常处理", "有问题时"),
        }
        selected = self._selected_step(text, steps)
        for field_name, labels in field_patterns.items():
            values = self._capture_list(text, labels)
            if values and selected:
                changes.append(self._change(selected, field_name, values, f"人工描述的{labels[0]}"))

        if images and not image_refs and selected:
            image_refs.extend({"step_ref": str(selected["id"]), "reference": item} for item in images)
        if not changes and not new_steps and not image_refs:
            warnings.append("离线规则未识别出可安全写入的字段；请补充工序名称或编号，或等待 AI 解析。")
        return {
            "assistant_message": "我已按你的描述定位可修改内容；下面的改动会写入待人工核对的 SOP 草稿。",
            "judgement": ["根据工序编号、名称和字段关键词定位目标；没有明确依据的生产参数不会补造。"],
            "summary": f"识别到 {len(changes)} 项字段修改、{len(new_steps)} 个待新增工序、{len(image_refs)} 个图片引用。",
            "changes": changes,
            "new_steps": new_steps,
            "image_refs": image_refs,
            "warnings": warnings,
            "requires_human_confirmation": True,
        }

    def _llm_preview(
        self,
        text: str,
        route: dict[str, Any],
        config: dict[str, str],
        *,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        step_fields = {
            "inputs": "input_json", "materials": "material_json", "tool_equipment": "tool_equipment_json",
            "fixtures": "fixture_json", "parameters": "parameter_json", "method": "method_json",
            "quality_check": "quality_check_json", "acceptance_criteria": "acceptance_criteria_json",
            "safety": "safety_json", "record_output": "record_output_json", "exception": "exception_json",
        }
        steps = []
        for item in route.get("steps", []):
            compact = {
                "id": item["id"], "step_code": item["step_code"], "sequence_no": item["sequence_no"],
                "title": item["title"], "action": item.get("action", ""), "why": item.get("why", ""),
                "review_state": item.get("review_state", ""),
            }
            compact.update({name: item.get(column, []) for name, column in step_fields.items()})
            steps.append(compact)
        sections = [
            {
                "section_type": item["section_type"],
                "content": item.get("content_json", {}),
                "unknowns": item.get("unknowns_json", []),
                "review_state": item.get("review_state", ""),
            }
            for item in route.get("sections", [])
        ]
        locked_step_id = route.get("_locked_target_step_id")
        locked_step = next(
            (item for item in steps if int(item.get("id", 0)) == int(locked_step_id)),
            None,
        ) if locked_step_id is not None else None
        system = (
            "你是面向客户的制造 SOP 助手。只输出 JSON，不输出 Markdown。你可以根据上下文判断用户指的是哪道工序、"
            "哪个作业指导内容或哪个路线章节，不要求用户按固定句式填写。"
            "输出键包括 assistant_message、judgement、changes、new_steps、section_changes、image_refs、summary、warnings。"
            "面向客户的表达规则：assistant_message 用 1 到 3 句通俗中文先说结论，说明“已改什么”或“还需要确认什么”。"
            "避免长段落、书面腔、学术化解释、重复复述用户原话。严禁出现 route_steps、JSON、step_code、字段名、数据库、"
            "内部 ID、模型提示词、系统规则或推理过程。不要写“我理解您希望”“我会在方法、检查项和验收标准中”等笼统套话。"
            "judgement 只写 1 到 3 条简短、客户可读的依据，例如“已定位到裁线工序”“长度参数尚未提供，需人工确认”。"
            "summary 用一句话概括改动数量和对象，不使用技术字段名。warnings 用短句说明风险或缺少的信息，并说明下一步需要谁确认。"
            "changes 元素只能含 step_ref、field_name、value、reason；field_name 只能是"
            + ",".join(sorted(ALLOWED_FIELDS))
            + "。除parameters外，数组字段value必须是字符串数组；parameters允许对象数组。"
            "new_steps可含title、method、action、why、quality_check、acceptance_criteria、safety、record_output、exception、after_step_ref。"
            "section_changes元素只能含section_type、patch、reason；section_type只能是"
            + ",".join(sorted(SECTION_TYPES))
            + "，patch是与现有content合并的JSON对象。图片只记录工作人员给出的引用，不虚构文件。"
            "可以回答关于当前 SOP 的问题；如果用户只是询问，则给出简短直接的 assistant_message，并让所有改动数组为空。"
            "不要补造生产地点、设备型号、质量结论、参数、工时、单价、人数、批准或现场事实。所有写入都是待人工核对的草稿，不能代表批准。"
        )
        if locked_step:
            system += (
                f"本轮目标已经由系统和用户确认，只能修改 id={locked_step['id']}、名称为“{locked_step['title']}”的工序。"
                "changes 和 image_refs 的 step_ref 必须使用这个 id；不得沿用最近对话中的其他工序，不得新增工序。"
                "如果指令内容与这个目标明显冲突，所有改动数组留空，并在 warnings 中用一句话说明。"
            )
            if route.get("_target_retry"):
                system += "这是目标校验后的重试。上一次返回了其他工序，本次必须严格遵守已锁定目标。"
        recent_history = [
            {"role": item.get("role"), "content": item.get("content")}
            for item in history[-8:]
            if item.get("role") in {"user", "assistant"}
        ]
        user = json.dumps({
            "instruction": text,
            "route": route.get("route", {}),
            "route_steps": steps,
            "route_sections": sections,
            "available_media": [item.get("original_name") for item in route.get("media_assets", [])],
            "recent_conversation": recent_history,
            "locked_target": locked_step,
        }, ensure_ascii=False)
        payload = json.dumps({
            "model": config["model"],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }, ensure_ascii=False).encode("utf-8")
        url = config["base_url"].rstrip("/") + "/chat/completions"
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + config["api_key"]},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content).strip())
        return json.loads(content)

    def _llm_preview_with_retry(
        self,
        text: str,
        route: dict[str, Any],
        config: dict[str, str],
        *,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Retry a transient provider failure once before using the offline parser."""
        last_error: Exception | None = None
        for attempt in range(self.max_llm_attempts):
            try:
                return self._llm_preview(text, route, config, history=history)
            except Exception as error:
                last_error = error
                if attempt + 1 >= self.max_llm_attempts or not self._is_retryable(error):
                    raise
                time.sleep(0.5)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        if isinstance(error, (TimeoutError, http.client.HTTPException, urllib.error.URLError)):
            return True
        if isinstance(error, urllib.error.HTTPError):
            return error.code == 429 or error.code >= 500
        return False

    @staticmethod
    def _fallback_reason(error: Exception) -> str:
        if isinstance(error, TimeoutError):
            return "AI 响应超时"
        if isinstance(error, urllib.error.HTTPError):
            return f"AI 服务返回 HTTP {error.code}"
        if isinstance(error, urllib.error.URLError):
            return "AI 网络连接失败"
        if isinstance(error, http.client.HTTPException):
            return "AI 响应传输中断"
        if isinstance(error, ValueError):
            return "AI 返回内容无法解析"
        return "AI 请求失败"

    def _sanitize(self, proposal: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
        steps = route.get("steps", [])
        clean_changes: list[dict[str, Any]] = []
        for raw in proposal.get("changes", []):
            field = str(raw.get("field_name", ""))
            if field not in ALLOWED_FIELDS:
                continue
            step = self._match_step(str(raw.get("step_ref", "")), steps)
            if not step:
                continue
            value = raw.get("value")
            if field in ARRAY_FIELDS:
                value = value if isinstance(value, list) else self._split_items(str(value))
                if field == "parameters":
                    value = [item for item in value if isinstance(item, dict) or str(item).strip()]
                else:
                    value = [str(item).strip() for item in value if str(item).strip()]
            else:
                value = str(value).strip()
            if not value:
                continue
            clean_changes.append(self._change(step, field, value, str(raw.get("reason", "自然语言解析"))))
        new_steps = []
        for raw in proposal.get("new_steps", []):
            title = str(raw.get("title", "")).strip()
            if not title:
                continue
            methods = raw.get("method", [])
            if not isinstance(methods, list):
                methods = self._split_items(str(methods))
            clean_new = {
                "title": title,
                "method": [str(item).strip() for item in methods if str(item).strip()],
                "image_ref": str(raw.get("image_ref", "")).strip(),
                "after_step_ref": str(raw.get("after_step_ref", "")).strip(),
                "position": "append",
            }
            for name in ("action", "why"):
                if str(raw.get(name, "")).strip():
                    clean_new[name] = str(raw[name]).strip()
            for name in ("quality_check", "acceptance_criteria", "safety", "record_output", "exception"):
                values = raw.get(name, [])
                if not isinstance(values, list):
                    values = self._split_items(str(values))
                if values:
                    clean_new[name] = [str(item).strip() for item in values if str(item).strip()]
            new_steps.append(clean_new)
        section_changes = []
        for raw in proposal.get("section_changes", []):
            section_type = str(raw.get("section_type", "")).strip()
            patch = raw.get("patch")
            if section_type in SECTION_TYPES and isinstance(patch, dict) and patch:
                section_changes.append({
                    "section_type": section_type,
                    "patch": patch,
                    "reason": str(raw.get("reason", "自然语言章节修改")).strip(),
                })
        image_refs = []
        for raw in proposal.get("image_refs", []):
            step = self._match_step(str(raw.get("step_ref", "")), steps)
            reference = str(raw.get("reference", "")).strip()
            if step and reference:
                image_refs.append({"step_ref": str(step["id"]), "step_id": step["id"], "step_code": step["step_code"], "reference": reference})
        return {
            "assistant_message": str(proposal.get("assistant_message") or "我已识别这次请求，并整理为可审核的 SOP 草稿修改。"),
            "judgement": self._text_list(proposal.get("judgement")),
            "summary": str(proposal.get("summary") or f"识别到 {len(clean_changes)} 项修改。"),
            "changes": clean_changes,
            "new_steps": new_steps,
            "section_changes": section_changes,
            "image_refs": image_refs,
            "warnings": self._text_list(proposal.get("warnings")),
            "requires_human_confirmation": True,
        }

    @staticmethod
    def _text_list(value: Any) -> list[str]:
        """Normalize an LLM's optional text field before it reaches the UI."""
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _change(step: dict[str, Any], field: str, value: Any, reason: str) -> dict[str, Any]:
        return {"step_ref": str(step["id"]), "step_id": step["id"], "step_code": step["step_code"], "step_title": step["title"], "field_name": field, "value": value, "reason": reason}

    @classmethod
    def _selected_step(cls, text: str, steps: list[dict[str, Any]]) -> dict[str, Any] | None:
        match = re.search(r"(?:工序|步骤)\s*(第?\s*\d+|[A-Za-z]+[-_]?\d+|[^，。；;：:]{1,20})", text)
        return cls._match_step(match.group(1).strip(), steps) if match else None

    @staticmethod
    def _match_step(reference: str, steps: list[dict[str, Any]]) -> dict[str, Any] | None:
        ref = reference.strip().lower().replace("第", "").replace("道", "").replace("个", "")
        if not ref:
            return None
        if ref.isdigit():
            numeric = int(ref)
            by_id = next((item for item in steps if item.get("id") == numeric), None)
            if by_id:
                return by_id
            if 1 <= numeric <= len(steps):
                return steps[numeric - 1]
        exact = next((item for item in steps if str(item.get("step_code", "")).lower() == ref or str(item.get("title", "")).lower() == ref), None)
        if exact:
            return exact
        matches = [item for item in steps if ref in str(item.get("title", "")).lower() or ref in str(item.get("step_code", "")).lower()]
        return matches[0] if len(matches) == 1 else None

    @classmethod
    def _capture_list(cls, text: str, labels: tuple[str, ...]) -> list[str]:
        label_pattern = "|".join(re.escape(item) for item in sorted(labels, key=len, reverse=True))
        all_labels = "工序|流程|步骤|对应的作业指导|对应作业指导|作业指导|作业步骤|操作方法|对应的图片|对应图片|图片|照片|检查方法|质量检查|怎么检查|合格标准|合格判据|验收标准|安全注意|安全要求|异常处理|有问题时"
        match = re.search(rf"(?:{label_pattern})\s*(?:是|为|：|:)?\s*(.*?)(?=[；;\n。]|(?:{all_labels})\s*(?:是|为|：|:)|$)", text)
        return cls._split_items(match.group(1)) if match else []

    @classmethod
    def _capture_operation_list(cls, text: str) -> list[str]:
        """Only treat an explicit declaration or add command as an operation list."""
        declared = re.search(r"(?:工序|流程|步骤)\s*(?:是|为|：|:)", text)
        explicit_add = re.search(r"(?:新增|添加|增加|插入|新建).{0,12}(?:工序|步骤)", text)
        if not declared and not explicit_add:
            return []
        return cls._capture_list(text, ("工序", "流程", "步骤"))

    @staticmethod
    def _split_items(value: str) -> list[str]:
        return [item.strip() for item in re.split(r"\s*(?:、|，|,|\||/|→|->)\s*", value.strip()) if item.strip()]

    @staticmethod
    def _llm_config() -> dict[str, str] | None:
        project_root = Path(__file__).resolve().parents[2]
        local_env = project_root / ".env.local"
        if local_env.exists():
            for line in local_env.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                if key.strip() in {"LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"}:
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL")
        model = os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL")
        if not all((api_key, base_url, model)):
            return None
        return {"api_key": api_key, "base_url": base_url, "model": model}
