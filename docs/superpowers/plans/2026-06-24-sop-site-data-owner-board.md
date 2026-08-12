# SOP Site Data Owner Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group SOP site data collection tasks by owner role so production, IE, equipment, quality, EHS, warehouse, and PMC can each see their own open data responsibilities.

**Architecture:** Build a deterministic `site_data_collection_board` from existing `site_data_collection_tasks` in `cad_ai/manufacturing_appliance.py`. Project File remains dictionary-based; Web and Markdown report read the new board without introducing a workflow engine or real task system.

**Tech Stack:** Python standard library, unittest, existing Web UI.

## Global Constraints

- Do not add external task systems or integrations.
- Do not auto-confirm data or release SOP.
- Keep generated tasks advisory and auditable.
- Record this iteration in the technical solution document.

---

### Task 1: Backend Owner Board

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`

**Interfaces:**
- Consumes: `sop["site_data_collection_tasks"]`
- Produces: `sop["site_data_collection_board"]`

- [x] **Step 1: Write failing test**

```python
board = sop["site_data_collection_board"]
self.assertIn("PMC/生产", board)
self.assertIn("line_capability", board["PMC/生产"]["categories"])
self.assertIn("OP50", board["PMC/生产"]["blocking_operations"])
self.assertGreaterEqual(board["PMC/生产"]["open_tasks"], 1)
```

- [x] **Step 2: Run failing test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`
Expected: FAIL with missing `site_data_collection_board`.

- [x] **Step 3: Implement helper**

Add `_site_data_collection_board(tasks)` and wire it into `ApplianceSOPAgent.run()`.

- [x] **Step 4: Run passing test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`
Expected: PASS.

### Task 2: Web Projection

**Files:**
- Modify: `tests/test_manufacturing_web.py`
- Modify: `cad_ai/manufacturing_web.py`

**Interfaces:**
- Consumes: `_result_summary(result)`
- Produces: `sop_site_data_collection_board` and HTML section `责任角色补数看板`

- [x] **Step 1: Write failing test**

```python
self.assertEqual(state["last_result_summary"]["sop_site_data_collection_board"]["PMC/生产"]["open_tasks"], 1)
self.assertIn("责任角色补数看板", INDEX_HTML)
```

- [x] **Step 2: Run failing test**

Run: `python -m unittest tests.test_manufacturing_web -v`
Expected: FAIL with missing summary field or HTML text.

- [x] **Step 3: Implement Web projection**

Expose board in `_result_summary()` and render a compact owner-role summary in `renderSummary`.

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
- Produces: report section `责任角色补数看板` and technical solution iteration `19.8`

- [x] **Step 1: Write failing report test**

```python
self.assertIn("责任角色补数看板", report_text)
self.assertIn("PMC/生产", report_text)
```

- [x] **Step 2: Run failing report test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_outputs_are_written -v`
Expected: FAIL with missing report text.

- [x] **Step 3: Implement report and docs**

Add report lines after `现场补数任务` and append section `19.8` to the technical solution.

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

Submit a local Web turn and verify `/api/state` includes `sop_site_data_collection_board`.

- [x] **Step 4: Browser refresh**

Refresh `http://127.0.0.1:8765/` and verify visible text includes `责任角色补数看板`.
