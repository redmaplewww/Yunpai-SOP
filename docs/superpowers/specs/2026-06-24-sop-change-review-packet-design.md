# SOP Change Review Packet Design

## Goal

Build a deterministic SOP change review packet after `change_impact_assessment` so a site team can see who must review a SOP change, what evidence each role needs, which site facts are still missing, and why AI cannot approve the change.

## Scope

This iteration adds `sop["change_review_packet"]` to the manufacturing appliance workflow. It does not add a real approval workflow, external MES/WMS/QMS integration, messaging, signatures, or automatic SOP version release.

## Data Model

`change_review_packet` contains:

- `status`: `blocked_missing_site_evidence`
- `approval_policy`: `human_change_board_required`
- `packet_decision`: `return_for_site_evidence`
- `review_board`: engineering, production, quality, EHS, and PMC role descriptors.
- `role_review_matrix`: one row per review role with `review_focus`, `required_evidence`, `missing_site_facts`, `affected_operations`, `decision_options`, and `status`.
- `required_packet_evidence`: cross-role evidence the packet must collect before human review.
- `ai_boundary`: `no_change_review_decision_without_human_board`

## Derivation

The packet is derived from `change_impact_assessment`, `release_candidate_lock`, and existing site input fields. OP50 line-performance exceptions should carry OEE, PMC confirmation, operator retraining, time-study remeasurement, quality revalidation, and EHS reapproval into the role review matrix when those signals are present upstream.

## Report And Web

The Markdown report adds a "变更评审包" section after "变更影响评估". The Web summary adds `sop_change_review_packet`, then renders "变更评审包" and "角色评审矩阵" after the current change-impact blocks.

## Boundaries

AI may assemble review requirements, missing facts, and evidence prompts. AI must not invent OEE, yield, equipment status, EHS approvals, trial-run evidence, training records, exception closure evidence, or human board decisions.
