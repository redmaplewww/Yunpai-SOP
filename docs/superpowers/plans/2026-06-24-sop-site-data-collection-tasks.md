# SOP Site Data Collection Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn missing SOP site input categories into actionable collection tasks grouped by category, owner role, source, and blocking operations.

**Architecture:** Reuse existing `site_input_status` and `operation_readiness_matrix` from `cad_ai/manufacturing_appliance.py`. Add a deterministic helper that creates open collection tasks only for `missing_external_data`; expose those tasks in Web and report views without integrating real MES/WMS/QMS systems.

**Tech Stack:** Python standard library, unittest, existing inline Web UI.

## Global Constraints

- Do not hardcode secrets.
- Do not integrate real external systems in this increment.
- Do not auto-release SOP; collection tasks remain advisory until humans/systems confirm data.
- Keep Web chat workflow visible and continuous.
- Record this iteration in the technical solution document.

---

### Task 1: Backend Collection Tasks

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`

**Interfaces:**
- Consumes: `sop["site_input_status"]` and `sop["operation_readiness_matrix"]`
- Produces: `sop["site_data_collection_tasks"]`

- [x] **Step 1: Write failing test**

```python
tasks = sop["site_data_collection_tasks"]
self.assertIn("line_capability", [task["category"] for task in tasks])
self.assertNotIn("production_location", [task["category"] for task in tasks])
line_task = next(task for task in tasks if task["category"] == "line_capability")
self.assertEqual(line_task["status"], "open_missing_external_data")
self.assertIn("OP50", line_task["blocking_operations"])
self.assertIn("owner_role", line_task)
```

- [x] **Step 2: Run failing test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`
Expected: FAIL with missing `site_data_collection_tasks`.

- [x] **Step 3: Implement helper**

Add `_site_data_collection_tasks(site_input_status, operation_readiness_matrix)` and wire it into `ApplianceSOPAgent.run()`.

- [x] **Step 4: Run passing test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`
Expected: PASS.

### Task 2: Web Projection

**Files:**
- Modify: `tests/test_manufacturing_web.py`
- Modify: `cad_ai/manufacturing_web.py`

**Interfaces:**
- Consumes: `_result_summary(result)`
- Produces: `sop_site_data_collection_tasks` and HTML section `现场补数任务`

- [x] **Step 1: Write failing test**

```python
self.assertEqual(state["last_result_summary"]["sop_site_data_collection_tasks"][0]["status"], "open_missing_external_data")
self.assertIn("现场补数任务", INDEX_HTML)
```

- [x] **Step 2: Run failing test**

Run: `python -m unittest tests.test_manufacturing_web -v`
Expected: FAIL with missing summary field or HTML text.

- [x] **Step 3: Implement projection and display**

Expose and render a compact owner/category/task list.

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
- Produces: report section `现场补数任务` and technical solution iteration `19.7`

- [x] **Step 1: Write failing report test**

```python
self.assertIn("现场补数任务", report_text)
self.assertIn("open_missing_external_data", report_text)
```

- [x] **Step 2: Run failing report test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_outputs_are_written -v`
Expected: FAIL with missing report text.

- [x] **Step 3: Implement report and docs**

Add report lines after `现场输入状态` and append a new `19.7` section to the technical solution document.

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

Submit one local Web turn and verify `/api/state` includes `sop_site_data_collection_tasks`.

- [x] **Step 4: Browser refresh**

Refresh `http://127.0.0.1:8765/` and verify visible text includes `现场补数任务`.
