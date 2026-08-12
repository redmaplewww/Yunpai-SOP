# SOP Release Gate Field Requirements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect SOP release gates to field-level missing site inputs so reviewers can see exactly which unconfirmed fields block production, quality, EHS, and PMC approval.

**Architecture:** Build deterministic `release_gate_field_requirements` from existing `release_checklist` and `site_data_input_fields`. Keep it advisory: the structure explains gate blockers and owner responsibilities without creating a real approval workflow or auto-releasing SOP.

**Tech Stack:** Python standard library, unittest, existing Web UI.

## Global Constraints

- Do not add external approval systems or integrations.
- Do not auto-confirm field values or release SOP.
- Only reference existing `site_data_input_fields`; do not invent site data.
- Keep AI boundary explicit through `forbidden_external_fact`.
- Record this iteration in the technical solution document.

---

### Task 1: Backend Gate Requirements

**Files:**
- Modify: `tests/test_manufacturing_appliance.py`
- Modify: `cad_ai/manufacturing_appliance.py`

**Interfaces:**
- Consumes: `sop["release_checklist"]`, `sop["site_data_input_fields"]`
- Produces: `sop["release_gate_field_requirements"]`

- [x] **Step 1: Write failing test**

```python
gate_requirements = sop["release_gate_field_requirements"]
pmc_gate = next(item for item in gate_requirements if item["gate"] == "PMC确认")
self.assertEqual(pmc_gate["status"], "blocked_missing_required_fields")
self.assertIn("OEE", pmc_gate["required_field_names"])
self.assertIn("line_capability", pmc_gate["categories"])
self.assertIn("OP50", pmc_gate["blocking_operations"])
self.assertEqual(pmc_gate["ai_fill_policy"], "forbidden_external_fact")
production_gate = next(item for item in gate_requirements if item["gate"] == "生产确认")
self.assertNotIn("production_location", production_gate["categories"])
```

- [x] **Step 2: Run failing test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`
Expected: FAIL with missing `release_gate_field_requirements`.

- [x] **Step 3: Implement helper**

Add `_release_gate_field_requirements(release_checklist, input_fields)` and wire it into `ApplianceSOPAgent.run()`.

- [x] **Step 4: Run passing test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_sop_site_input_status_distinguishes_user_context_from_external_data -v`
Expected: PASS.

### Task 2: Web Projection

**Files:**
- Modify: `tests/test_manufacturing_web.py`
- Modify: `cad_ai/manufacturing_web.py`

**Interfaces:**
- Consumes: `_result_summary(result)`
- Produces: `sop_release_gate_field_requirements` and HTML section `闸口字段阻塞`

- [x] **Step 1: Write failing test**

```python
self.assertEqual(
    state["last_result_summary"]["sop_release_gate_field_requirements"][0]["status"],
    "blocked_missing_required_fields",
)
self.assertIn("闸口字段阻塞", INDEX_HTML)
```

- [x] **Step 2: Run failing test**

Run: `python -m unittest tests.test_manufacturing_web -v`
Expected: FAIL with missing summary field or HTML text.

- [x] **Step 3: Implement Web projection**

Expose `sop_release_gate_field_requirements` in `_result_summary()` and render compact gate blockers after `现场录入字段`.

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
- Produces: report section `闸口字段阻塞` and technical solution iteration `19.10`

- [x] **Step 1: Write failing report test**

```python
self.assertIn("闸口字段阻塞", report_text)
self.assertIn("blocked_missing_required_fields", report_text)
```

- [x] **Step 2: Run failing report test**

Run: `python -m unittest tests.test_manufacturing_appliance.ManufacturingApplianceWorkflowTests.test_outputs_are_written -v`
Expected: FAIL with missing report text.

- [x] **Step 3: Implement report and docs**

Add report lines after `现场录入字段` and append section `19.10` to the technical solution.

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

Submit a local Web turn and verify `/api/state` includes `sop_release_gate_field_requirements` with `PMC确认`, `OEE`, and `blocked_missing_required_fields`.

- [x] **Step 4: Browser refresh**

Refresh `http://127.0.0.1:8765/` and verify visible text includes `闸口字段阻塞`.
