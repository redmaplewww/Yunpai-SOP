# SOP Trial Run Verification Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SOP trial-run verification matrix that explains what evidence is required before moving from site-bound draft toward `L3_trial_run_verified`.

**Architecture:** Build deterministic `trial_run_verification_matrix` from existing operation breakdown and release-gate field requirements. The matrix is advisory and auditable: it lists sample requirements, evidence categories, blocking gates, blocking fields, and AI boundaries without inventing trial results or changing SOP release status.

**Tech Stack:** Python standard library, unittest, existing Web UI.

## Global Constraints

- Do not auto-create real trial records.
- Do not mark SOP as `L3_trial_run_verified`.
- Do not fake OEE, yield, cycle time, EHS, quality, or operator data.
- Keep all outputs tied to existing operations, fields, and gate blockers.
- Record this iteration in the technical solution document.

---

### Task 1: Backend Trial Matrix

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`

**Interfaces:**
- Consumes: `sop["operation_breakdown"]`, `sop["release_gate_field_requirements"]`
- Produces: `sop["trial_run_verification_matrix"]`

- [x] **Step 1: Write failing test**

```python
trial_matrix = sop["trial_run_verification_matrix"]
op50_trial = next(item for item in trial_matrix if item["operation_id"] == "OP50")
self.assertEqual(op50_trial["maturity_target"], "L3_trial_run_verified")
self.assertEqual(op50_trial["status"], "blocked_missing_release_gate_fields")
self.assertIn("PMC 确认", op50_trial["blocked_by_gates"])
self.assertIn("OEE", op50_trial["blocking_fields"])
self.assertIn("trial_run_records", op50_trial["required_evidence"])
self.assertEqual(op50_trial["ai_boundary"], "no_trial_result_without_site_record")
```

- [x] **Step 2: Run failing test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`
Expected: FAIL with missing `trial_run_verification_matrix`.

- [x] **Step 3: Implement helper**

Add `_trial_run_verification_matrix(operations, gate_requirements)` and wire it into `ApplianceSOPAgent.run()`.

- [x] **Step 4: Run passing test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`
Expected: PASS.

### Task 2: Web Projection

**Files:**
- Modify: `tests/test_manufacturing_web.py`
- Modify: `cad_ai/manufacturing_web.py`

**Interfaces:**
- Consumes: `_result_summary(result)`
- Produces: `sop_trial_run_verification_matrix` and HTML section `试产验证矩阵`

- [x] **Step 1: Write failing test**

```python
self.assertEqual(
    state["last_result_summary"]["sop_trial_run_verification_matrix"][0]["maturity_target"],
    "L3_trial_run_verified",
)
self.assertIn("试产验证矩阵", INDEX_HTML)
```

- [x] **Step 2: Run failing test**

Run: `python -m unittest tests.test_manufacturing_web -v`
Expected: FAIL with missing summary field or HTML text.

- [x] **Step 3: Implement Web projection**

Expose `sop_trial_run_verification_matrix` in `_result_summary()` and render compact rows after `闸口字段阻塞`.

- [x] **Step 4: Run passing test**

Run: `python -m unittest tests.test_manufacturing_web -v`
Expected: PASS.

### Task 3: Report And Iteration Record

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`
- Modify: `docs/work-packages/reports/15_yunpai_manufacturing_appliance_technical_solution.md`

**Interfaces:**
- Consumes: `render_manufacturing_appliance_report(project_file)`
- Produces: report section `试产验证矩阵` and technical solution iteration `19.11`

- [x] **Step 1: Write failing report test**

```python
self.assertIn("试产验证矩阵", report_text)
self.assertIn("no_trial_result_without_site_record", report_text)
```

- [x] **Step 2: Run failing report test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_outputs_are_written -v`
Expected: FAIL with missing report text.

- [x] **Step 3: Implement report and docs**

Add report lines after `闸口字段阻塞` and append section `19.11` to the technical solution.

- [x] **Step 4: Run passing report test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_outputs_are_written -v`
Expected: PASS.

### Task 4: Verification

- [x] **Step 1: Compile**

Run: `python -m py_compile cad_ai\manufacturing_appliance.py cad_ai\manufacturing_web.py cad_ai\cli.py tests\test_manufacturing_appliance.py tests\test_manufacturing_web.py`
Expected: exit 0.

- [x] **Step 2: Relevant tests**

Run: `python -m unittest tests.test_manufacturing_appliance tests.test_manufacturing_web tests.test_manufacturing_chat tests.test_automation_llm_gateway -v`
Expected: all OK.

- [x] **Step 3: Web smoke**

Submit a local Web turn and verify `/api/state` includes `sop_trial_run_verification_matrix` with `OP50`, `L3_trial_run_verified`, and `no_trial_result_without_site_record`.

- [x] **Step 4: Browser refresh**

Refresh `http://127.0.0.1:8765/` and verify visible text includes `试产验证矩阵`.
