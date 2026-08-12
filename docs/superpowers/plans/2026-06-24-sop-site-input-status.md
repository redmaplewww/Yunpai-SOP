# SOP Site Input Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Yunpai SOP workflow distinguish site information already supplied in the conversation from production-line data that AI cannot obtain and must be collected from humans or systems.

**Architecture:** Add a lightweight deterministic status classifier inside `cad_ai/manufacturing_appliance.py` that reads the accumulated source text and annotates each SOP dependency category. Existing SOP readiness remains conservative; supplied signals improve visibility but do not auto-release the SOP.

**Tech Stack:** Python standard library, existing unittest suite, existing inline Web UI.

## Global Constraints

- Do not hardcode API keys or secrets.
- Do not auto-upgrade SOP to shopfloor release based only on text hints.
- Do not add MES/WMS/QMS integrations; represent required data as input status only.
- Keep Web chat continuity and workflow status visible.
- Record this iteration in the technical solution document.

---

### Task 1: Backend Site Input Status

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`

**Interfaces:**
- Consumes: `run_manufacturing_appliance_workflow(text=...)`
- Produces: `project.sop_baseline["site_input_status"]` and enriched `operation_readiness_matrix[*]["input_status_summary"]`

- [x] **Step 1: Write the failing test**

```python
self.assertEqual(project.sop_baseline["site_input_status"]["production_location"]["status"], "provided_by_user")
self.assertEqual(project.sop_baseline["site_input_status"]["line_capability"]["status"], "missing_external_data")
self.assertIn("production_location", project.sop_baseline["provided_site_input_categories"])
self.assertIn("line_capability", project.sop_baseline["missing_external_input_categories"])
self.assertGreaterEqual(readiness_matrix[0]["input_status_summary"]["provided"], 1)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sequential_workflow_runs_full_framework -v`
Expected: FAIL with missing `site_input_status`.

- [x] **Step 3: Implement minimal classifier**

Add `_site_input_status(source_text, catalog)` and wire it into `ApplianceSOPAgent.run()`.

- [x] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sequential_workflow_runs_full_framework -v`
Expected: PASS.

### Task 2: Web Projection

**Files:**
- Modify: `tests/test_manufacturing_web.py`
- Modify: `cad_ai/manufacturing_web.py`

**Interfaces:**
- Consumes: `_result_summary(result)`
- Produces: `sop_site_input_status`, `sop_provided_site_input_categories`, `sop_missing_external_input_categories`, HTML section `现场输入状态`

- [x] **Step 1: Write failing test**

```python
self.assertEqual(state["last_result_summary"]["sop_site_input_status"]["production_location"]["status"], "provided_by_user")
self.assertIn("现场输入状态", INDEX_HTML)
```

- [x] **Step 2: Run failing test**

Run: `python -m unittest tests.test_manufacturing_web -v`
Expected: FAIL with missing summary field/HTML text.

- [x] **Step 3: Implement projection and display**

Add the new summary keys and render provided/missing categories in the right panel.

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
- Produces: report section `现场输入状态` and updated iteration notes.

- [x] **Step 1: Write failing report test**

```python
self.assertIn("现场输入状态", report_text)
self.assertIn("provided_by_user", report_text)
self.assertIn("missing_external_data", report_text)
```

- [x] **Step 2: Run failing report test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_outputs_are_written -v`
Expected: FAIL with missing report section.

- [x] **Step 3: Implement report and docs**

Add report lines after SOP data gaps and append a new `19.6` iteration note.

- [x] **Step 4: Run passing report test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_outputs_are_written -v`
Expected: PASS.

### Task 4: Verification

**Files:**
- Verify all touched files.

- [x] **Step 1: Compile**

Run: `python -m py_compile cad_ai\manufacturing_appliance.py cad_ai\manufacturing_web.py cad_ai\cli.py tests\test_manufacturing_appliance.py tests\test_manufacturing_web.py`
Expected: exit 0.

- [x] **Step 2: Unit tests**

Run: `python -m unittest tests.test_manufacturing_appliance tests.test_manufacturing_web tests.test_manufacturing_chat tests.test_automation_llm_gateway -v`
Expected: all OK.

- [x] **Step 3: Web smoke**

Submit one local Web turn and verify `/api/state` has `sop_site_input_status`.

- [x] **Step 4: Browser refresh**

Refresh `http://127.0.0.1:8765/` and verify visible text includes `现场输入状态`.
