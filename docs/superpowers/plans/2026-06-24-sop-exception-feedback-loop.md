# SOP Exception Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SOP exception feedback loop that links trial/production exceptions back to SOP operations, required site fields, release gates, and change-control boundaries.

**Architecture:** Extend `cad_ai/manufacturing_appliance.py` with a deterministic helper that derives exception categories and feedback tasks from existing SOP operation, field, gate, trial-run, and release-candidate structures. Surface the loop in the markdown report and Web summary so users can see how exceptions feed back into SOP iteration without pretending to have real MES/QMS records.

**Tech Stack:** Python standard library, existing unittest suite, existing single-file Web UI in `cad_ai/manufacturing_web.py`.

## Global Constraints

- Do not auto-change or auto-release SOP versions.
- Do not invent trial-run records, OEE, yield, equipment status, EHS approvals, defect counts, downtime causes, or operator confirmations.
- Do not add real MES/WMS/QMS integrations.
- Keep all new data derived from existing SOP draft structures and explicit AI boundaries.
- Update the technical solution report for this iteration.

---

### Task 1: Backend Exception Feedback Loop

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`
- Modify: `docs/work-packages/reports/15_yunpai_manufacturing_appliance_technical_solution.md`

**Interfaces:**
- Consumes: `sop["operation_breakdown"]`, `sop["site_data_input_fields"]`, `sop["release_gate_field_requirements"]`, `sop["trial_run_verification_matrix"]`, `sop["release_candidate_lock"]`
- Produces: `sop["exception_feedback_loop"] -> dict[str, Any]`

- [x] **Step 1: Write the failing backend test**

```python
feedback_loop = sop.get("exception_feedback_loop")
self.assertIsNotNone(feedback_loop)
self.assertEqual(feedback_loop["status"], "awaiting_trial_or_production_exception_records")
self.assertIn("OP50", [item["operation_id"] for item in feedback_loop["operation_feedback_matrix"]])
op50_feedback = next(item for item in feedback_loop["operation_feedback_matrix"] if item["operation_id"] == "OP50")
self.assertIn("line_performance_exception", op50_feedback["exception_categories"])
self.assertIn("OEE", op50_feedback["linked_fields"])
self.assertIn("PMC 确认", op50_feedback["linked_gates"])
self.assertIn("sop_change_request", op50_feedback["required_actions"])
self.assertEqual(feedback_loop["ai_boundary"], "no_exception_closure_without_site_evidence")
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`

Expected: FAIL because `exception_feedback_loop` is missing.

- [x] **Step 3: Implement minimal backend helper**

Add `_exception_feedback_loop(...)` near the SOP helper functions. Call it from `ApplianceSOPAgent.run(...)` after `release_candidate_lock` is built.

- [x] **Step 4: Render report section**

Add a markdown section named `异常反馈闭环` with status, feedback sources, operation matrix, required actions, and AI boundary.

- [x] **Step 5: Run backend tests**

Run: `python -m unittest tests.test_manufacturing_appliance -v`

Expected: PASS.

### Task 2: Web Exception Feedback Visibility

**Files:**
- Modify: `tests/test_manufacturing_web.py`
- Modify: `cad_ai/manufacturing_web.py`

**Interfaces:**
- Consumes: `project.sop_baseline["exception_feedback_loop"]`
- Produces: `last_result_summary["sop_exception_feedback_loop"]`

- [x] **Step 1: Write the failing Web test**

```python
self.assertEqual(
    state["last_result_summary"]["sop_exception_feedback_loop"]["status"],
    "awaiting_trial_or_production_exception_records",
)
self.assertIn("异常反馈闭环", INDEX_HTML)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_manufacturing_web.YunpaiWebTests.test_submit_message_runs_background_turn_and_updates_web_state tests.test_manufacturing_web.YunpaiWebTests.test_html_exposes_deep_sop_workflow_sections -v`

Expected: FAIL because the summary key and HTML section are missing.

- [x] **Step 3: Implement minimal Web summary and rendering**

Add `sop_exception_feedback_loop` in `_result_summary(...)` and render it in `renderSummary(...)`.

- [x] **Step 4: Run Web tests**

Run: `python -m unittest tests.test_manufacturing_web -v`

Expected: PASS.

### Task 3: Verification and Browser Smoke

**Files:**
- Read: Web app at `http://127.0.0.1:8765/`

**Interfaces:**
- Consumes: `/api/chat`, `/api/state`
- Produces: browser-visible exception feedback loop section

- [x] **Step 1: Compile changed Python files**

Run: `python -m py_compile cad_ai\manufacturing_appliance.py cad_ai\manufacturing_web.py tests\test_manufacturing_appliance.py tests\test_manufacturing_web.py`

Expected: exit code 0.

- [x] **Step 2: Run focused suite**

Run: `python -m unittest tests.test_manufacturing_appliance tests.test_manufacturing_web tests.test_manufacturing_chat tests.test_automation_llm_gateway -v`

Expected: all tests pass.

- [x] **Step 3: Check whitespace**

Run: `git diff --check`

Expected: exit code 0, allowing existing LF/CRLF warnings only.

- [x] **Step 4: Restart local Web app and smoke test**

Run the existing `python -m cad_ai yunpai-web ... --port 8765` command, POST a chat message that asks for exception feedback, then verify `/api/state` includes `sop_exception_feedback_loop`.

- [x] **Step 5: Refresh in-app browser**

Verify `异常反馈闭环`, `line_performance_exception`, and `no_exception_closure_without_site_evidence` are visible with no console errors.
