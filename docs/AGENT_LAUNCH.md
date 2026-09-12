# Backlot external-agent launch contract

Backlot owns the manifest work order, but OpenMontage does not embed an LLM or
turn the old demo runner into a production executor. The **Run Pipeline**
button starts the operator's configured external agent process when
`OPENMONTAGE_AGENT_COMMAND` is set.

For one of the supported local coding-agent CLIs, select it in the ignored
project `.env` file:

```dotenv
OPENMONTAGE_AGENT_COMMAND=python -m lib.local_director codex
OPENMONTAGE_AGENT_ID=openmontage-agent
```

Replace `codex` with `claude` or `antigravity` to use Claude Code or Google
Antigravity. The local CLI must be installed, on `PATH`, and signed in before
Run Pipeline can start it successfully. Switch the command and restart Backlot
to change the director; one CLI directs each run.

The adapter uses each CLI's non-interactive prompt mode and does not bypass its
normal permission controls. Configure local CLI permissions for the project
before unattended runs. It removes model API-key variables from the director
process so Codex/Claude/Antigravity use their own local sign-in. OpenMontage's
production tools continue to load their provider keys from `.env` when those
tools run.

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

The local director is a separate role from a production media provider. Its
model usage follows the local CLI account's limits; OpenMontage media calls
such as OpenAI image or TTS generation use the configured production API key.

When no command is configured, `/api/project/<id>/run` returns HTTP 503 before
claiming a fresh run and tells the operator how to configure it. An external
agent can still receive a manifest handoff explicitly with
`/run?agent_id=<your-id>`; that path never spawns a second process.
