# Production-readiness fixture contract

The eight versioned golden scenario briefs live in
`tests/eval/golden_scenarios/`. They are validated against
`schemas/evals/golden_scenario.schema.json` by the Phase 0 contract suite.

This directory is reserved for deterministic media fixtures and expected artifact
snapshots when a scenario needs bytes rather than a provider-agnostic descriptor. No
provider credentials, developer-specific absolute paths, or generated media are stored here.
