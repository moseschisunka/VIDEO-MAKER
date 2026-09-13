# Backlot external-agent launch contract

Backlot owns the manifest work order, but OpenMontage does not embed an LLM or
turn the old demo runner into a production executor. Choose **Codex**,
**Claude Code**, or **Antigravity** in the Local Director selector; the choice
applies to that run and does not require editing `.env` or restarting Backlot.
The library wizard remembers the selected local director in this browser.
The board and wizard default to a local CLI only when it confirms ChatGPT
sign-in; a CLI whose account status is unverified stays available for explicit
selection and does not silently become the director.

The supported local CLI must be installed and signed in to its own account.
Codex, Claude Code, and Antigravity are separate directors; the current Codex
desktop conversation itself cannot be launched as a background process. The
adapter invokes the local Codex CLI, Claude Code CLI, or Antigravity `agy`
CLI. Native executables and PowerShell (`.ps1`) shims are supported on Windows;
`.bat` and `.cmd` shims are not.

An operator may still configure a custom runner as a fallback:

```dotenv
OPENMONTAGE_AGENT_COMMAND=python -m my_agent
OPENMONTAGE_AGENT_ID=openmontage-agent
```

The adapter uses each CLI's non-interactive prompt mode and does not bypass its
normal permission controls. Configure local CLI permissions for the project
before unattended runs. Each CLI uses its own local account sign-in; its model
usage follows that account's plan and limits. OpenMontage's production
provider calls continue to use credentials configured for those tools; those
credentials are not passed in the director's child environment.

For Codex, use `codex login` and choose ChatGPT sign-in if you want subscription
usage. Backlot checks that Codex is not signed in with an API key before
launching it. API-key sign-in is billed as API usage, so do not use the
production API key for the local director. See the [Codex authentication
guide](https://learn.chatgpt.com/docs/auth).

For Claude Code, run `claude auth login` and choose your Claude subscription
sign-in. Do not choose the Anthropic Console option; the `--console` option on
`claude auth login` is for API billing rather than subscription usage.
Backlot does not yet inspect Claude's saved login or billing mode. Verify that
the CLI is signed in to your Claude subscription before starting a run; the
CLI's `claude auth status` command checks the login state. See the [Claude
Code CLI reference](https://code.claude.com/docs/en/cli-usage).

Antigravity's headless `agy -p` mode uses cached credentials. Open `agy`
interactively and sign in once before using it as a director. Backlot cannot
verify the cached Google account or its plan. See the [Antigravity headless
mode guide](https://antigravity.google/docs/cli/headless/).

Before starting the configured agent, Backlot removes credential-like
environment variables (including provider keys, tokens, and credential-file
paths). The CLI runs with the selected project's directory as its working
directory. These are defense-in-depth measures, not an OS security boundary:
the CLI still runs as the same Windows user and may be able to inspect files
outside the project, including `.env`, or inspect another same-user process.
The app also does not yet issue a capability token limited to one project/run.
Credential isolation is therefore not certified for production use until
process/filesystem isolation and scoped Backlot authorization are implemented
and verified for each supported CLI and operating system. See
[`production-readiness/evidence/PR-10G-local-director-boundary.md`](production-readiness/evidence/PR-10G-local-director-boundary.md).

The command is parsed into an argument vector and launched with `shell=False`.
It receives these environment variables:

| Variable | Meaning |
|---|---|
| `OPENMONTAGE_PROJECT_ID` | Durable Backlot project identifier |
| `OPENMONTAGE_PROJECT_DIR` | Absolute project workspace path |
| `OPENMONTAGE_RUN_ID` | Immutable work-order run identity |
| `OPENMONTAGE_AGENT_ID` | Lease owner to use for heartbeat/checkpoint calls |
| `OPENMONTAGE_STAGE` | Manifest-derived next stage |
| `OPENMONTAGE_BACKLOT_URL` | URL of the Backlot API that owns the run |
| `OPENMONTAGE_AGENT_PROMPT` | Ready-to-forward instruction for an LLM CLI or wrapper |

The child output is appended to `projects/<id>/agent.log`; launch metadata is
written atomically to `projects/<id>/agent_process.json`. A configured command
must claim/heartbeat through the existing work-order API and write the normal
artifact/checkpoint records. Backlot does not infer completion from process
exit, fabricate artifacts, or silently substitute a different renderer.
At launch, Backlot also writes an instruction bundle under
`projects/<id>/.openmontage/` with the current manifest, stage skill, agent
guide, and project context. The CLI prompt points to this bundle so it does not
have to guess or lose the browser's handoff instructions.

The local director is a separate role from a production media provider. Its
model usage follows the local CLI account's limits; OpenMontage media calls
such as OpenAI image or TTS generation use the configured production API key.

When no director is selected and no custom command is configured,
`/api/project/<id>/run` returns HTTP 503 before claiming a fresh run. An
external agent can still receive a manifest handoff explicitly with
`/run?agent_id=<your-id>`; that path never spawns a second process.
