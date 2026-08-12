# SOP Release Candidate Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SOP release-candidate lock checklist that makes the L3 trial-run-to-L4 shopfloor-release boundary visible without auto-releasing SOPs or inventing site facts.

**Architecture:** Extend `cad_ai/manufacturing_appliance.py` with a helper that derives release-candidate lock items from existing release gates, site fields, and trial-run rows. Surface the result in the markdown report and Web summary so users can see which versions, evidence, and owners are still blocking shopfloor SOP release.

**Tech Stack:** Python standard library, existing unittest suite, existing single-file Web UI in `cad_ai/manufacturing_web.py`.

## Global Constraints

- Do not auto-release an SOP.
- Do not invent OEE, yield, equipment state, shifts, EHS approvals, or trial-run results.
- Do not add real MES/WMS/QMS integrations.
- Keep all new data derived from existing SOP draft structures and human/site-data boundaries.
- Update the technical solution report for each iteration.

---

### Task 1: Backend Release Candidate Lock

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`
- Modify: `docs/work-packages/reports/15_yunpai_manufacturing_appliance_technical_solution.md`

**Interfaces:**
- Consumes: `sop["release_gate_field_requirements"]`, `sop["trial_run_verification_matrix"]`, `sop["site_data_input_fields"]`
- Produces: `sop["release_candidate_lock"] -> dict[str, Any]`

- [x] **Step 1: Write the failing backend test**

```python
release_lock = sop["release_candidate_lock"]
self.assertEqual(release_lock["target_maturity"], "L4_released_shopfloor_sop")
self.assertEqual(release_lock["status"], "blocked_before_release_candidate")
self.assertIn("SOP version", release_lock["locked_artifacts"])
self.assertIn("trial_run_records", release_lock["required_evidence"])
self.assertIn("PMC 确认", release_lock["blocking_gates"])
self.assertEqual(release_lock["ai_boundary"], "no_shopfloor_release_without_human_lock")
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`

Expected: FAIL because `release_candidate_lock` is missing.

- [x] **Step 3: Implement minimal backend helper**

Add `_release_candidate_lock(...)` near the existing SOP helper functions. Call it from `ApplianceSOPAgent.run(...)` after the trial-run matrix is built.

- [x] **Step 4: Render report section**

Add a markdown section named `发布候选锁版清单` with status, locked artifacts, blocking gates, required evidence, and AI boundary.

- [x] **Step 5: Run backend tests**

Run: `python -m unittest tests.test_manufacturing_appliance -v`

Expected: PASS.

### Task 2: Web Release Candidate Visibility

**Files:**
- Modify: `tests/test_manufacturing_web.py`
- Modify: `cad_ai/manufacturing_web.py`

**Interfaces:**
- Consumes: `project.sop_baseline["release_candidate_lock"]`
- Produces: `last_result_summary["sop_release_candidate_lock"]`

- [x] **Step 1: Write the failing Web test**

```python
self.assertEqual(
    state["last_result_summary"]["sop_release_candidate_lock"]["target_maturity"],
    "L4_released_shopfloor_sop",
)
self.assertIn("发布候选锁版清单", INDEX_HTML)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_manufacturing_web.YunpaiWebTests.test_submit_message_runs_background_turn_and_updates_web_state tests.test_manufacturing_web.YunpaiWebTests.test_html_exposes_deep_sop_workflow_sections -v`

Expected: FAIL because the summary key and HTML section are missing.

- [x] **Step 3: Implement minimal Web summary and rendering**

Add `sop_release_candidate_lock` in `_result_summary(...)` and render it in `renderSummary(...)`.

- [x] **Step 4: Run Web tests**

Run: `python -m unittest tests.test_manufacturing_web -v`

Expected: PASS.

### Task 3: Full Verification and Browser Smoke

**Files:**
- Read: Web app at `http://127.0.0.1:8765/`

**Interfaces:**
- Consumes: `/api/chat`, `/api/state`
- Produces: browser-visible release candidate lock section

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

Run the existing `python -m cad_ai yunpai-web ... --port 8765` command, POST a chat message with site and missing line data, then verify `/api/state` includes `sop_release_candidate_lock`.

- [x] **Step 5: Refresh in-app browser**

Verify `发布候选锁版清单`, `L4_released_shopfloor_sop`, and `no_shopfloor_release_without_human_lock` are visible with no console errors.
