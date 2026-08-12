# SOP Readiness Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deepen the Yunpai manufacturing SOP from a generic operation list into an operation-level readiness matrix that shows which production-site inputs block each SOP operation.

**Architecture:** Keep the existing monolithic manufacturing workflow structure and add focused helper functions in `cad_ai/manufacturing_appliance.py`. Project File output remains dictionary-based, with Web and Markdown report projections reading the new fields without adding new dependencies.

**Tech Stack:** Python standard library, existing Project File schemas, existing unittest test suite, inline HTML/JS in `cad_ai/manufacturing_web.py`.

## Global Constraints

- Keep DeepSeek/API keys environment-based; never hardcode secrets.
- Do not add complex PMC optimization or automatic external message sending.
- Preserve the current Web chat workflow and visible stage status.
- Maintain iteration documentation in `docs/work-packages/reports/15_yunpai_manufacturing_appliance_technical_solution.md`.
- Use TDD: add failing tests before production code.

---

### Task 1: SOP Readiness Matrix Backend

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`

**Interfaces:**
- Consumes: `run_manufacturing_appliance_workflow(...)`
- Produces: `project.sop_baseline["operation_readiness_matrix"]`, `project.sop_baseline["site_input_dependencies"]`, and `project.sop_baseline["maturity_model"]`

- [x] **Step 1: Write the failing test**

```python
matrix = project.sop_baseline["operation_readiness_matrix"]
self.assertGreaterEqual(len(matrix), 6)
self.assertEqual(matrix[0]["readiness"], "blocked_missing_site_inputs")
self.assertIn("production_location", matrix[0]["blocked_by_categories"])
self.assertIn("owner_role", matrix[0]["required_inputs"][0])
self.assertEqual(project.sop_baseline["maturity_model"]["current_level"], "L1_common_process_draft")
self.assertIn("L2_site_bound_draft", project.sop_baseline["maturity_model"]["levels"])
self.assertIn("OP50", project.sop_baseline["site_input_dependencies"])
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sequential_workflow_runs_full_framework -v`
Expected: FAIL with missing `operation_readiness_matrix`.

- [x] **Step 3: Write minimal implementation**

Add helpers:

```python
def _site_input_dependency_catalog() -> dict[str, dict[str, Any]]:
    ...

def _sop_operation_readiness_matrix(operations: list[dict[str, Any]], dependencies: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    ...

def _sop_maturity_model() -> dict[str, Any]:
    ...
```

Wire the returned dictionaries into `sop`.

- [x] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sequential_workflow_runs_full_framework -v`
Expected: PASS.

### Task 2: Web Projection

**Files:**
- Modify: `tests/test_manufacturing_web.py`
- Modify: `cad_ai/manufacturing_web.py`

**Interfaces:**
- Consumes: `_result_summary(result)`
- Produces: `sop_operation_readiness_matrix`, `sop_maturity_model`, and HTML section text `工序级阻塞矩阵`

- [x] **Step 1: Write the failing test**

```python
self.assertEqual(state["last_result_summary"]["sop_operation_readiness_matrix"][0]["readiness"], "blocked_missing_site_inputs")
self.assertEqual(state["last_result_summary"]["sop_maturity_model"]["current_level"], "L1_common_process_draft")
self.assertIn("工序级阻塞矩阵", INDEX_HTML)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_manufacturing_web -v`
Expected: FAIL with missing summary fields or missing HTML text.

- [x] **Step 3: Write minimal implementation**

Expose the new fields in `_result_summary()` and render a compact right-panel block:

```javascript
addBlock("工序级阻塞矩阵", list((summary.sop_operation_readiness_matrix || []).map(row =>
  `${row.operation_id || ""} · ${row.readiness || "missing"} · ${(row.blocked_by_categories || []).join("/")}`
)));
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_manufacturing_web -v`
Expected: PASS.

### Task 3: Report And Iteration Documentation

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`
- Modify: `docs/work-packages/reports/15_yunpai_manufacturing_appliance_technical_solution.md`

**Interfaces:**
- Consumes: `render_manufacturing_appliance_report(project_file)`
- Produces: Markdown sections containing `工序级阻塞矩阵` and updated `2026-06-24` iteration notes.

- [x] **Step 1: Write the failing test**

```python
self.assertIn("工序级阻塞矩阵", report_text)
self.assertIn("L2_site_bound_draft", report_text)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_outputs_are_written -v`
Expected: FAIL with missing report text.

- [x] **Step 3: Write minimal implementation**

Add report lines after SOP operation breakdown and append iteration notes to the existing 2026-06-24 section.

- [x] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_outputs_are_written -v`
Expected: PASS.

### Task 4: Fresh Verification And Web Smoke

**Files:**
- Verify: `cad_ai/manufacturing_appliance.py`
- Verify: `cad_ai/manufacturing_web.py`
- Verify: tests touched above

**Interfaces:**
- Consumes: current worktree.
- Produces: fresh command evidence and local browser/page state.

- [x] **Step 1: Run compile check**

Run: `python -m py_compile cad_ai\manufacturing_appliance.py cad_ai\manufacturing_web.py cad_ai\cli.py tests\test_manufacturing_appliance.py tests\test_manufacturing_web.py`
Expected: exit 0.

- [x] **Step 2: Run relevant tests**

Run: `python -m unittest tests.test_manufacturing_appliance tests.test_manufacturing_web tests.test_manufacturing_chat tests.test_automation_llm_gateway -v`
Expected: all tests OK.

- [x] **Step 3: Restart or reuse Web app and submit a smoke turn**

Use `python -m cad_ai yunpai-web ... --port 8765` if needed, submit one local `/api/chat` message, and verify `/api/state` includes operation readiness rows.

- [x] **Step 4: Refresh the in-app browser**

Open `http://127.0.0.1:8765/` and verify the page text includes `工序级阻塞矩阵`.
