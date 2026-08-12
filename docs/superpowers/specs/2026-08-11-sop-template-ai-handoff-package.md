# SOP Template AI Handoff Package Design

## Goal

Provide one deterministic, AI-readable handoff path that reproduces the accepted two-page USB-C cable packaging SOP template without allowing a future session to select a different SOP entrypoint or silently relax validation.

## Design

- Freeze a template identity: `yunpai.sop.usb_c_cable_packaging.two_page.v1`.
- Reuse the existing `build_usb_cable_packaging_demo()` and native Word table renderer.
- Apply the accepted compact two-page delivery controls after the base Word document is built.
- Expose one CLI entrypoint: `scripts/generate_sop_template_ai_handoff.py`.
- Emit a DOCX, center flowchart PNG, format check, generation manifest, and structural validation report.
- Add a Windows Word/Poppler render helper with a strict two-page gate.
- Add an AI-first README, a machine-readable handoff manifest, and a copyable next-session prompt.
- Route future sessions from the workspace `AGENTS.md` to this package.

## Safety boundary

The package always remains `demo_not_for_release`. Approval, audit, and author cells are blank. Draft IE values cannot be represented as site measurements. Production location, equipment state, EHS, OEE, yield, trial, training, and review records are never fabricated.

## Acceptance

- The generator produces exactly five formal artifacts.
- DOCX structural validation passes with 2 sections, 8 top-level tables, PNG media, no SVG/VML, no replacement character, fixed right/bottom sections, visual step order, and blank signoff values.
- Rendering produces exactly two pages.
- Page 1 is portrait; page 2 is landscape and contains all six IE rows and the full signoff table.
- `tests.test_sop_template_ai_handoff` and `tests.test_sop_visual_template` pass.
