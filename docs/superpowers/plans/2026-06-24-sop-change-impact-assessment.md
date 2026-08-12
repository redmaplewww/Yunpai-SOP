# SOP Change Impact Assessment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SOP change-impact assessment that turns exception-driven `sop_change_request` signals into an explicit review map across operations, fields, gates, locked artifacts, training, and re-verification evidence.

**Architecture:** Extend `cad_ai/manufacturing_appliance.py` with a deterministic helper that derives change-impact rows from `exception_feedback_loop` plus the existing release-candidate lock. Surface the result in the markdown report and Web summary so users can see what a SOP change would affect before any human approval or version update.

**Tech Stack:** Python standard library, existing unittest suite, existing single-file Web UI in `cad_ai/manufacturing_web.py`.

## Global Constraints

- Do not auto-approve SOP changes.
- Do not auto-change, auto-release, or overwrite locked SOP/BOM/routing versions.
- Do not invent exception closure evidence, OEE, yield, equipment status, EHS approvals, trial-run results, or operator training completion.
- Do not add real MES/WMS/QMS integrations or approval workflows.
- Update the technical solution report for this iteration.

---

### Task 1: Backend Change Impact Assessment

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`
- Modify: `docs/work-packages/reports/15_yunpai_manufacturing_appliance_technical_solution.md`

**Interfaces:**
- Consumes: `sop["exception_feedback_loop"]`, `sop["release_candidate_lock"]`
- Produces: `sop["change_impact_assessment"] -> dict[str, Any]`

- [x] **Step 1: Write the failing backend test**

```python
impact = sop.get("change_impact_assessment")
self.assertIsNotNone(impact)
self.assertEqual(impact["status"], "awaiting_change_board_review")
self.assertEqual(impact["approval_policy"], "human_change_board_required")
self.assertIn("operator_training_record", impact["locked_artifacts_at_risk"])
op50_impact = next(item for item in impact["operation_change_matrix"] if item["operation_id"] == "OP50")
self.assertIn("OEE", op50_impact["impacted_fields"])
self.assertIn("PMC 确认", op50_impact["impacted_gates"])
self.assertIn("operator_retraining", op50_impact["required_reverification"])
self.assertEqual(impact["ai_boundary"], "no_sop_change_approval_without_change_board")
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`

Expected: FAIL because `change_impact_assessment` is missing.

- [x] **Step 3: Implement minimal backend helper**

Add `_change_impact_assessment(...)` near the SOP helper functions. Call it from `ApplianceSOPAgent.run(...)` after `exception_feedback_loop` is built.

- [x] **Step 4: Render report section**

Add a markdown section named `变更影响评估` with status, approval policy, artifacts at risk, operation matrix, required re-verification, and AI boundary.

- [x] **Step 5: Run backend tests**

Run: `python -m unittest tests.test_manufacturing_appliance -v`

Expected: PASS.

### Task 2: Web Change Impact Visibility

**Files:**
- Modify: `tests/test_manufacturing_web.py`
- Modify: `cad_ai/manufacturing_web.py`

**Interfaces:**
- Consumes: `project.sop_baseline["change_impact_assessment"]`
- Produces: `last_result_summary["sop_change_impact_assessment"]`

- [x] **Step 1: Write the failing Web test**

```python
self.assertEqual(
    state["last_result_summary"]["sop_change_impact_assessment"]["status"],
    "awaiting_change_board_review",
)
self.assertIn("变更影响评估", INDEX_HTML)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_manufacturing_web.YunpaiWebTests.test_submit_message_runs_background_turn_and_updates_web_state tests.test_manufacturing_web.YunpaiWebTests.test_html_exposes_deep_sop_workflow_sections -v`

Expected: FAIL because the summary key and HTML section are missing.

- [x] **Step 3: Implement minimal Web summary and rendering**

Add `sop_change_impact_assessment` in `_result_summary(...)` and render it in `renderSummary(...)`.

- [x] **Step 4: Run Web tests**

Run: `python -m unittest tests.test_manufacturing_web -v`

Expected: PASS.

### Task 3: Verification and Browser Smoke

**Files:**
- Read: Web app at `http://127.0.0.1:8765/`

**Interfaces:**
- Consumes: `/api/chat`, `/api/state`
- Produces: browser-visible change-impact assessment section

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

Run the existing `python -m cad_ai yunpai-web ... --port 8765` command, POST a chat message that asks for SOP change impact, then verify `/api/state` includes `sop_change_impact_assessment`.

- [x] **Step 5: Refresh in-app browser**

Verify `变更影响评估`, `human_change_board_required`, and `no_sop_change_approval_without_change_board` are visible with no console errors.
