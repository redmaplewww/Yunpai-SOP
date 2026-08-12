# SOP Change Review Packet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a SOP change review packet that turns `change_impact_assessment` into role-specific human review requirements without approving or releasing SOP changes.

**Architecture:** Extend `cad_ai/manufacturing_appliance.py` with a deterministic `_change_review_packet(...)` helper called after `_change_impact_assessment(...)`. Surface the packet in the Markdown report and Web summary using the existing single-file Web rendering pattern.

**Tech Stack:** Python standard library, existing unittest suite, existing `cad_ai/manufacturing_web.py` HTML/JS.

## Global Constraints

- Do not auto-approve SOP changes.
- Do not auto-release, overwrite, or lock SOP/BOM/routing versions.
- Do not invent OEE, yield, equipment status, EHS approvals, trial-run records, training records, exception closure evidence, or human board decisions.
- Do not add real MES/WMS/QMS integrations, messaging, signatures, or approval workflows.
- Update the technical solution report for this iteration.

---

### Task 1: Backend Review Packet

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`

**Interfaces:**
- Consumes: `sop["change_impact_assessment"]`, `sop["release_candidate_lock"]`, `sop["site_data_input_fields"]`
- Produces: `sop["change_review_packet"] -> dict[str, Any]`

- [x] **Step 1: Write failing backend test**

Add assertions in `test_sop_site_input_status_distinguishes_user_context_from_external_data`:

```python
review_packet = sop.get("change_review_packet")
self.assertIsNotNone(review_packet)
self.assertEqual(review_packet["status"], "blocked_missing_site_evidence")
self.assertEqual(review_packet["approval_policy"], "human_change_board_required")
self.assertEqual(review_packet["packet_decision"], "return_for_site_evidence")
pmc_review = next(item for item in review_packet["role_review_matrix"] if item["role"] == "PMC")
self.assertIn("OEE", pmc_review["missing_site_facts"])
self.assertIn("OP50", pmc_review["affected_operations"])
self.assertIn("return_for_site_evidence", pmc_review["decision_options"])
self.assertEqual(review_packet["ai_boundary"], "no_change_review_decision_without_human_board")
```

- [x] **Step 2: Verify backend test fails**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`

Expected: FAIL because `change_review_packet` is missing.

- [x] **Step 3: Implement minimal backend helper**

Add `_change_review_packet(...)` near the SOP helper functions and call it in `ApplianceSOPAgent.run(...)` after `change_impact_assessment` is built.

- [x] **Step 4: Verify backend test passes**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`

Expected: PASS.

### Task 2: Report Rendering

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`

**Interfaces:**
- Consumes: `sop["change_review_packet"]`
- Produces: Markdown report section `变更评审包`

- [x] **Step 1: Write failing report test**

Add assertions in `test_outputs_are_written` for `变更评审包`, `return_for_site_evidence`, and `no_change_review_decision_without_human_board`.

- [x] **Step 2: Verify report test fails**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_outputs_are_written -v`

Expected: FAIL because the report section is missing.

- [x] **Step 3: Render report section**

Add a Markdown section after `变更影响评估` that prints packet status, policy, packet decision, evidence, AI boundary, and role review rows.

- [x] **Step 4: Run backend tests**

Run: `python -m unittest tests.test_manufacturing_appliance -v`

Expected: PASS.

### Task 3: Web Visibility

**Files:**
- Modify: `tests/test_manufacturing_web.py`
- Modify: `cad_ai/manufacturing_web.py`

**Interfaces:**
- Consumes: `project.sop_baseline["change_review_packet"]`
- Produces: `last_result_summary["sop_change_review_packet"]` and visible HTML sections.

- [x] **Step 1: Write failing Web tests**

Add fake-result data and assertions for `sop_change_review_packet`, `变更评审包`, and `角色评审矩阵`.

- [x] **Step 2: Verify Web tests fail**

Run: `python -m unittest tests.test_manufacturing_web.YunpaiWebTests.test_submit_message_runs_background_turn_and_updates_web_state tests.test_manufacturing_web.YunpaiWebTests.test_html_exposes_deep_sop_workflow_sections -v`

Expected: FAIL because summary key and HTML sections are missing.

- [x] **Step 3: Implement Web summary and rendering**

Add `sop_change_review_packet` in `_result_summary(...)`; render "变更评审包" and "角色评审矩阵" after the change-impact blocks.

- [x] **Step 4: Run Web tests**

Run: `python -m unittest tests.test_manufacturing_web -v`

Expected: PASS.

### Task 4: Documentation And Verification

**Files:**
- Modify: `docs/work-packages/reports/15_yunpai_manufacturing_appliance_technical_solution.md`
- Modify: `docs/superpowers/plans/2026-06-24-sop-change-review-packet.md`

**Interfaces:**
- Produces: Technical report section `19.15 本轮继续迭代：变更评审包`

- [x] **Step 1: Update technical solution**

Append `19.15 本轮继续迭代：变更评审包` describing backend, report, Web, and AI boundary changes.

- [x] **Step 2: Compile changed Python files**

Run: `python -m py_compile cad_ai\manufacturing_appliance.py cad_ai\manufacturing_web.py tests\test_manufacturing_appliance.py tests\test_manufacturing_web.py`

Expected: exit code 0.

- [x] **Step 3: Run focused suite**

Run: `python -m unittest tests.test_manufacturing_appliance tests.test_manufacturing_web tests.test_manufacturing_chat tests.test_automation_llm_gateway -v`

Expected: all tests pass.

- [x] **Step 4: Check whitespace**

Run: `git diff --check`

Expected: exit code 0, allowing existing LF/CRLF warnings only.

- [x] **Step 5: Browser smoke**

Restart or reuse `http://127.0.0.1:8765/`, run one chat turn asking for SOP change review packet visibility, then verify `/api/state` and the browser show `变更评审包`, `return_for_site_evidence`, and `no_change_review_decision_without_human_board`.

Result: `/api/state` returned `PacketDecision=return_for_site_evidence` and `AIBoundary=no_change_review_decision_without_human_board`; runtime HTML returned status 200 and contained `变更评审包` plus `角色评审矩阵`. In-app browser automation was blocked by the current sandbox (`CreateProcessWithLogonW failed: 1326`), so visual inspection was replaced by runtime HTML verification.
