# SOP Agent FastAPI Packaging Plan

Date: 2026-07-07

## Objective

Package the 80806-129-style SOP drawing workflow as a reusable agent module and expose FastAPI-ready interfaces for downstream Web/API integration.

## Scope

- Add a reusable `cad_ai.sop_agent` module for draft SOP generation.
- Keep the required sequence: parse requirement -> fill Word tables -> build structured flowchart -> render center flowchart PNG -> insert PNG -> validate docx.
- Add `cad_ai.sop_api` with FastAPI app factory and artifact download routes.
- Add CLI startup command for the FastAPI service.
- Preserve draft-only guardrails: no automatic SOP release, no fake site IE measured time, no signoff fabrication.
- Add tests for direct agent generation and FastAPI routes.

## API Contract

- `POST /api/sop/generate`
  - Generates `.docx`, center flowchart `.png`, manifest, parsed SOP JSON, and format check JSON.
- `GET /api/sop/runs/{run_id}`
  - Returns the manifest for a generated SOP run.
- `GET /api/sop/runs/{run_id}/artifacts/{artifact_key}`
  - Downloads `document_docx`, `center_flowchart_png`, `manifest_json`, `format_check_json`, or `parsed_sop_json`.
- `GET /api/sop/health`
  - Returns agent readiness, draft status, generation sequence, and route contract.

## Guardrails

- Default status remains `demo_not_for_release`.
- Approval/signoff cells remain blank unless supplied by a real approval record.
- IE standard time values generated from model or structured input are draft estimates and require site IE confirmation before release.
- Testing/inspection/measurement nodes render as diamonds; processing/assembly/cleaning/packaging nodes render as ellipses.

## Verification

- `python -m py_compile cad_ai\sop_agent.py cad_ai\sop_api.py tests\test_sop_agent_api.py`
- `python -m unittest tests.test_sop_agent_api tests.test_sop_visual_template -v`
