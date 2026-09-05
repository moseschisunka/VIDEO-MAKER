# PR-10G simulation evidence correction

Date: 2026-09-05

Status: IMPLEMENTED and locally verified; supported CI pending.

The localhost harness previously emitted unqualified PASS results, assigned
human reviewer names without human review, and copied new results into files
named after an old commit. A subsequent execution could therefore regenerate
misleading evidence after the documentation audit corrected it.

The harness now emits SIMULATED_PASS, evidence_kind=localhost_simulation,
production_gate_satisfied=false, and a null reviewer. Automated acknowledgement
uses simulation-agent. External origin isolation is marked untested. Markdown
decisions explicitly state the remaining deployed-infrastructure requirements.
The CLI requires a full candidate SHA, embeds it in JSON, and no longer rewrites
historical candidate files. CI passes GITHUB_SHA explicitly.

Validation: staging integration and adjacent security tests: 21 passed,
1 warning. The integration regression executes all four local simulations,
checks generated JSON and Markdown classifications, and verifies preservation
of a pre-existing historical evidence file. This tests an uncommitted patch
based on c106d80; it is not frozen-candidate certification.

PR-10G remains BLOCKED on deployed rollback, trusted edge, durable external
metrics, and external paging. PR-11G remains locked. Historical raw JSON is
retained as historical output; its earlier PASS/reviewer fields do not establish
production certification or actual human review.
