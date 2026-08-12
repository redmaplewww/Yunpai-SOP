# SOP Site Data Input Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand SOP site data collection tasks into field-level input cards so each owner knows the exact production-site or line data that must be recorded before SOP release.

**Architecture:** Build deterministic `site_data_input_fields` from existing `site_data_collection_tasks`. Keep the output advisory and auditable: it describes required fields, source, owner, AI fill boundary, and blocking operations without integrating a real MES/WMS/QMS form engine.

**Tech Stack:** Python standard library, unittest, existing Web UI.

## Global Constraints

- Do not add external task systems or integrations.
- Do not auto-confirm field values or release SOP.
- Generate fields only for categories still marked `missing_external_data`.
- Keep AI boundary explicit: AI may ask for fields, but may not invent site facts.
- Record this iteration in the technical solution document.

---

### Task 1: Backend Field Cards

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`

**Interfaces:**
- Consumes: `sop["site_data_collection_tasks"]`
- Produces: `sop["site_data_input_fields"]`

- [x] **Step 1: Write failing test**

```python
input_fields = sop["site_data_input_fields"]
line_fields = [field for field in input_fields if field["category"] == "line_capability"]
self.assertIn("OEE", [field["field_name"] for field in line_fields])
oee_field = next(field for field in line_fields if field["field_name"] == "OEE")
self.assertEqual(oee_field["owner_role"], "PMC/生产")
self.assertEqual(oee_field["input_mode"], "manual_or_system_sync")
self.assertEqual(oee_field["ai_fill_policy"], "forbidden_external_fact")
self.assertIn("OP50", oee_field["blocking_operations"])
self.assertNotIn("production_location", {field["category"] for field in input_fields})
```

- [x] **Step 2: Run failing test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`
Expected: FAIL with missing `site_data_input_fields`.

- [x] **Step 3: Implement helper**

Add `_site_data_input_fields(tasks)` and wire it into `ApplianceSOPAgent.run()` after `site_data_collection_tasks`.

- [x] **Step 4: Run passing test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`
Expected: PASS.

### Task 2: Web Projection

**Files:**
- Modify: `tests/test_manufacturing_web.py`
- Modify: `cad_ai/manufacturing_web.py`

**Interfaces:**
- Consumes: `_result_summary(result)`
- Produces: `sop_site_data_input_fields` and HTML section `现场录入字段`

- [x] **Step 1: Write failing test**

```python
self.assertEqual(
    state["last_result_summary"]["sop_site_data_input_fields"][0]["input_mode"],
    "manual_or_system_sync",
)
self.assertIn("现场录入字段", INDEX_HTML)
```

- [x] **Step 2: Run failing test**

Run: `python -m unittest tests.test_manufacturing_web -v`
Expected: FAIL with missing summary field or HTML text.

- [x] **Step 3: Implement Web projection**

Expose `sop_site_data_input_fields` in `_result_summary()` and render a compact field list after the owner board.

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
- Produces: report section `现场录入字段` and technical solution iteration `19.9`

- [x] **Step 1: Write failing report test**

```python
self.assertIn("现场录入字段", report_text)
self.assertIn("forbidden_external_fact", report_text)
```

- [x] **Step 2: Run failing report test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_outputs_are_written -v`
Expected: FAIL with missing report text.

- [x] **Step 3: Implement report and docs**

Add report lines after `责任角色补数看板` and append section `19.9` to the technical solution.

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

Submit a local Web turn and verify `/api/state` includes `sop_site_data_input_fields` with `OEE` and `forbidden_external_fact`.

- [x] **Step 4: Browser refresh**

Refresh `http://127.0.0.1:8765/` and verify visible text includes `现场录入字段`.
