from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Route, sync_playwright


BASE_URL = "http://127.0.0.1:8787/"
OUTPUT_DIR = Path(__file__).resolve().parent / "screenshots" / "sop-targeting-20260814"


def run_viewport(page, *, name: str, likely: bool, complete_selection: bool) -> dict[str, object]:
    console_issues: list[str] = []
    page_errors: list[str] = []
    failed_responses: list[str] = []
    posted: list[dict[str, object]] = []
    page.on("console", lambda message: console_issues.append(f"{message.type}: {message.text}") if message.type in {"error", "warning"} else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("response", lambda response: failed_responses.append(f"{response.status} {response.url}") if response.status >= 400 else None)

    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.wait_for_function("() => typeof documentInfo !== 'undefined' && documentInfo !== null && Number(routeId) > 0", timeout=30_000)
    document_info = page.evaluate("() => documentInfo")
    route_id = page.evaluate("() => Number(routeId)")
    route_payload = page.evaluate(
        "async id => await (await fetch(`/api/routes/${id}`)).json()",
        route_id,
    )
    steps = route_payload["steps"][:3]
    candidates = [
        {
            "step_id": step["id"],
            "step_code": step["step_code"],
            "sequence_no": step["sequence_no"],
            "title": step["title"],
            "reason": "工序名称或作业内容与描述相近",
        }
        for step in steps
    ]
    call_count = 0

    def mock_chat(route: Route) -> None:
        nonlocal call_count
        call_count += 1
        body = route.request.post_data_json
        posted.append(body)
        if call_count == 1:
            status = "likely" if likely else "needs_choice"
            payload = {
                "route_id": route_id,
                "message": "我找到一个最可能的工序，请先确认。确认后才会修改 SOP。" if likely else "这句话可能指向多道工序，请先选一个。选定后才会修改 SOP。",
                "parser_kind": "target_selection",
                "summary": "等待人工确认目标工序。",
                "judgement": ["系统没有擅自写入。"],
                "warnings": [],
                "changes": [],
                "applied": {"status": "awaiting_target_confirmation"},
                "document": document_info,
                "docx_regenerated": False,
                "requires_human_confirmation": True,
                "proposal_id": None,
                "assistant_message_id": 9001,
                "target_resolution": {
                    "status": status,
                    "target_phrase": "检验",
                    "selected_step_id": None,
                    "candidates": candidates,
                    "excluded_step_ids": [],
                    "used_context": False,
                    "pending_message_id": None,
                },
            }
        else:
            payload = {
                "route_id": route_id,
                "message": f"已按 {candidates[0]['step_code']} {candidates[0]['title']} 处理，本次测试没有写入文档。",
                "parser_kind": "llm",
                "summary": "目标选择请求已正确提交。",
                "judgement": ["已锁定用户选择的工序。"],
                "warnings": [],
                "changes": [],
                "applied": {"status": "answered_without_edit"},
                "document": document_info,
                "docx_regenerated": False,
                "requires_human_confirmation": True,
                "proposal_id": None,
                "assistant_message_id": 9002,
                "target_resolution": {
                    "status": "resolved",
                    "target_phrase": candidates[0]["title"],
                    "selected_step_id": candidates[0]["step_id"],
                    "candidates": candidates,
                    "excluded_step_ids": [],
                    "used_context": False,
                    "pending_message_id": 9001,
                },
            }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    page.route("**/api/routes/*/chat", mock_chat)
    page.screenshot(path=OUTPUT_DIR / f"{name}-01-initial.png", full_page=True)
    page.locator("#prompt").fill("把检验工序的记录要求补充完整。")
    page.locator("#send").click()
    page.locator(".target-option").first.wait_for(state="visible")
    page.screenshot(path=OUTPUT_DIR / f"{name}-02-candidates.png", full_page=True)
    page.locator(".assistant-card").last.screenshot(path=OUTPUT_DIR / f"{name}-02-candidate-card.png")

    first_height = page.locator(".target-option").first.bounding_box()["height"]
    assert first_height >= 44, first_height
    assert not page.evaluate("() => document.documentElement.scrollWidth > window.innerWidth")
    if likely:
        assert page.get_by_text("查看其他候选").is_visible()
    if complete_selection:
        page.locator(".target-option").first.click()
        page.wait_for_function("() => document.querySelectorAll('.message.assistant').length >= 2")
        page.screenshot(path=OUTPUT_DIR / f"{name}-03-selected.png", full_page=True)
        assert posted[-1]["selected_step_id"] == candidates[0]["step_id"]
        assert posted[-1]["pending_message_id"] == 9001
        assert page.locator('[data-pending-message="9001"]').first.is_disabled()

    return {
        "viewport": name,
        "candidate_height": first_height,
        "posted_requests": posted,
        "horizontal_overflow": False,
        "console_issues": console_issues,
        "page_errors": page_errors,
        "failed_responses": failed_responses,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        desktop = browser.new_page(viewport={"width": 1440, "height": 900})
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        results = [
            run_viewport(desktop, name="desktop", likely=False, complete_selection=True),
            run_viewport(mobile, name="mobile", likely=True, complete_selection=False),
        ]
        browser.close()
    issues = [
        issue
        for result in results
        for key in ("console_issues", "page_errors", "failed_responses")
        for issue in result[key]
    ]
    print(json.dumps({"results": results, "issues": issues, "screenshots": str(OUTPUT_DIR)}, ensure_ascii=False, indent=2))
    if issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
