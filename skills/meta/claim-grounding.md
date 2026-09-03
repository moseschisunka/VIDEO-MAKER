# Claim-grounding contract (binding for every production pipeline)

This policy applies to research, brief, script, scene, and review stages. The
agent owns research judgment; the runtime owns deterministic enforcement.

## Research output

When a factual topic is researched, every `research_brief.sources[]` record
must carry a stable `id`, human-readable `title`, `canonical_locator`, an
ISO `accessed_at` date, a short relevant `excerpt_or_note`, `license`,
`usage_constraints`, and the `claim_ids` it supports. Data points use stable
IDs and retain their `source_url`. Do not embed source documents in artifacts.

## Script output

Production scripts set `grounding_contract.required=true` and provide an
explicit `claims[]` ledger, or equivalent section-level `claim_id` and
`source_refs` fields. Each claim is classified as `factual`, `creative`,
`opinion`, `instruction`, `cta`, or `rhetorical`.

- Factual claims cite one or more source IDs, URLs, or stable data-point IDs.
- `supported`, `contradicted`, `uncertain`, `unsupported`, and `missing_source`
  statuses are preserved; uncertainty is never rewritten as fact.
- Creative/opinion/instruction copy is reviewed for intent, not forced to have
  a citation.
- High-risk factual claims (health, medical, legal, financial, safety, and
  claims marked `risk_level=high` or `critical`) with a non-supported status
  block the stage. Other unsupported claims require revision.

## Deterministic gate

Before a script or grounded review checkpoint is completed, run
`lib.grounding.validate_grounding(research_brief, script, strict=True)` and
retain its serialisable report as stage evidence. A `pass` decision is
required; `revise` and `block` are not production-ready.
