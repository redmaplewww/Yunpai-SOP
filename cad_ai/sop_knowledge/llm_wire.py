from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any


_LOCAL_CONFIG_KEYS = {
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_WIRE_API",
    "LLM_REASONING_EFFORT",
    "LLM_DISABLE_RESPONSE_STORAGE",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "OPENAI_WIRE_API",
    "OPENAI_REASONING_EFFORT",
    "OPENAI_DISABLE_RESPONSE_STORAGE",
}


def load_llm_config() -> dict[str, Any] | None:
    """Load the local provider configuration without overriding process settings."""
    project_root = Path(__file__).resolve().parents[2]
    local_env = project_root / ".env.local"
    if local_env.exists():
        for line in local_env.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            if key in _LOCAL_CONFIG_KEYS:
                os.environ.setdefault(key, value.strip().strip('"').strip("'"))

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL")
    model = os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL")
    if not all((api_key, base_url, model)):
        return None

    wire_api = (os.getenv("OPENAI_WIRE_API") or os.getenv("LLM_WIRE_API") or "chat_completions").strip()
    if wire_api not in {"chat_completions", "responses"}:
        raise ValueError("LLM_WIRE_API must be chat_completions or responses")
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "wire_api": wire_api,
        "reasoning_effort": (
            os.getenv("OPENAI_REASONING_EFFORT") or os.getenv("LLM_REASONING_EFFORT") or ""
        ).strip(),
        "disable_response_storage": _as_bool(
            os.getenv("OPENAI_DISABLE_RESPONSE_STORAGE")
            or os.getenv("LLM_DISABLE_RESPONSE_STORAGE")
            or "false"
        ),
    }


def request_json_object(
    *,
    system: str,
    user: str,
    config: dict[str, Any],
    timeout: int,
    images: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Send one JSON-only request through the configured compatible wire API."""
    wire_api = str(config.get("wire_api") or "chat_completions")
    if wire_api == "responses":
        payload = _responses_payload(system=system, user=user, config=config, images=images)
        endpoint = "/responses"
    elif wire_api == "chat_completions":
        user_content: str | list[dict[str, Any]] = user
        if images:
            user_content = _chat_image_content(user, images)
        payload = {
            "model": config["model"],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        endpoint = "/chat/completions"
    else:
        raise ValueError("Unsupported LLM wire API")

    request = urllib.request.Request(
        str(config["base_url"]).rstrip("/") + endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + str(config["api_key"])},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = _response_content(body, wire_api=wire_api)
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    result = json.loads(clean)
    if not isinstance(result, dict):
        raise ValueError("LLM response must contain a JSON object")
    return result


def _responses_payload(
    *,
    system: str,
    user: str,
    config: dict[str, Any],
    images: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    user_content: list[dict[str, Any]] = [{"type": "input_text", "text": user}]
    for image in images or []:
        user_content.extend(
            [
                {"type": "input_text", "text": _image_source_label(image)},
                {"type": "input_image", "image_url": image["data_url"], "detail": "auto"},
            ]
        )
    payload: dict[str, Any] = {
        "model": config["model"],
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system}]},
            {"role": "user", "content": user_content},
        ],
        "text": {"format": {"type": "json_object"}},
    }
    reasoning_effort = str(config.get("reasoning_effort") or "").strip()
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    if bool(config.get("disable_response_storage")):
        payload["store"] = False
    return payload


def _chat_image_content(user: str, images: list[dict[str, str]]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": user}]
    for image in images:
        content.extend(
            [
                {"type": "text", "text": _image_source_label(image)},
                {"type": "image_url", "image_url": {"url": image["data_url"], "detail": "auto"}},
            ]
        )
    return content


def _image_source_label(image: dict[str, str]) -> str:
    source_id = str(image.get("source_id") or "")
    original_name = str(image.get("original_name") or "")
    return f"Uploaded image source_id={source_id}; original_name={original_name}"


def _response_content(body: dict[str, Any], *, wire_api: str) -> str:
    if wire_api == "chat_completions":
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("Chat Completions response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("Chat Completions response has no message")
        return _content_text(message.get("content"))

    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    output = body.get("output")
    if isinstance(output, list):
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = content.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
        if text_parts:
            return "".join(text_parts)
    raise ValueError("Responses API response has no output text")


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    raise ValueError("LLM response content is not text")


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
