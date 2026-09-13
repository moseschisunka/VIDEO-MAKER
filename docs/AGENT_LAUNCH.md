# Backlot external-agent launch contract

Backlot owns the manifest work order, but OpenMontage does not embed an LLM or
turn the old demo runner into a production executor. Choose **Codex CLI** or
**Antigravity** for an automated local run, or choose **Claude Code** for a
manual interactive handoff. These local directors use their own account
allowances and do not require editing `.env` or restarting Backlot.
The library wizard remembers the selected local director in this browser.
The board and wizard default to a local CLI only when it confirms ChatGPT
sign-in; a CLI whose account status is unverified stays available for explicit
selection and does not silently become the director.

The supported local CLI must be installed and signed in to its own account.
Codex CLI and Antigravity `agy` are separate local directors; the current
Codex desktop conversation itself cannot be launched as a background process.
Claude Code remains available in its native interactive terminal. When selected
on the board, Backlot claims the work order, writes a local instruction bundle,
and copies a prompt for the user. The user opens Claude Code and submits that
prompt manually; Backlot never starts Claude, injects the prompt, or sends a
request through a Claude subscription login.
Anthropic says third-party applications must use API-key or supported cloud
provider authentication instead of routing requests through Claude Free, Pro,
or Max credentials. Claude's `-p` subscription use also draws from a separate
monthly Agent SDK allowance, rather than interactive usage. See Anthropic's
[legal and compliance guidance](https://code.claude.com/docs/en/legal-and-compliance)
and [Claude Code automation guide](https://code.claude.com/docs/en/headless).
Native executables and PowerShell (`.ps1`) shims are supported on Windows;
`.bat` and `.cmd` shims are not.

An operator may still configure a custom runner as a fallback:

```dotenv
OPENMONTAGE_AGENT_COMMAND=python -m my_agent
OPENMONTAGE_AGENT_ID=openmontage-agent
```

This escape hatch is for a trusted, policy-compliant worker. Do not use it to
re-enable automated Claude subscription requests through `claude -p`.

The adapter uses Codex CLI and Antigravity's supported non-interactive prompt
modes and does not bypass their normal permission controls. Claude Code uses
the user-controlled interactive path described above. Configure local CLI
permissions for the project before unattended runs. Their model use follows
each account's allowance and limits; OpenMontage cannot promise a daily free quota. OpenAI's
Codex allowance is shared with other Codex and agentic features on eligible
ChatGPT plans. Antigravity has account quotas and an optional AI-credit
overage setting that can be set to **Never** in its own controls. OpenMontage's
production provider calls continue to use credentials configured for those
tools; credential-like environment variables are not passed to the director.

For Codex, use `codex login` and choose ChatGPT sign-in if you want subscription
usage. Backlot checks that Codex is not signed in with an API key before
launching it. API-key sign-in is billed as API usage, so do not use the
production API key for the local director. See [Using Codex with your ChatGPT
plan](https://help.openai.com/en/articles/11369540).

For Claude Code, choose the interactive handoff on the project board, then run
`claude` in the project workspace and paste the copied task prompt. Backlot
stores the same prompt at `.openmontage/claude_interactive_prompt.txt` if the
browser cannot copy it. A manual run is already claimed while you switch to the
terminal; if you do not submit it, its default five-minute lease expires and
can then be reclaimed. On Windows, use Anthropic's [terminal setup
guide](https://code.claude.com/docs/en/terminal-guide).
Backlot does not read Claude credentials or submit the task on your behalf.
If you later want an automated Claude director, it needs a permitted API or
supported cloud-provider setup, separate from the OpenAI production media key.
To use your Claude subscription allowance in the native CLI, sign in with your
Claude Pro/Max account using `/login` and check `/status`; decline any API-credit
option if you want to wait for the subscription allowance to reset. Anthropic's
[Claude Code plan guide](https://support.anthropic.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)
explains the separate subscription and API billing paths.

Antigravity's headless `agy -p` mode uses cached credentials. Open `agy`
interactively and sign in once before using it as a director. Backlot cannot
verify the cached Google account or its plan. See the [Antigravity headless
mode guide](https://antigravity.google/docs/cli/headless/) and [AI credits
guide](https://antigravity.google/docs/cli/credits). Set AI Credit Overages to
**Never** in Antigravity if you want it to stop at the included quota instead
of using purchased credits.

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

The local director is separate from a production media provider. Codex, Claude
Code, and Antigravity use their local account allowances; OpenMontage media
calls such as OpenAI image, video, or TTS generation use the configured
production API key. Automated local directors launched by Backlot receive no
credential-like environment variables, and their prompts must not read `.env`
or use the production API key for inference. Claude Code is opened by the user,
so it inherits that terminal's environment. This is process-level separation,
not an OS security boundary: a local CLI runs as the same Windows user and may
still be able to inspect files outside the project.

When no director is selected and no custom command is configured,
`/api/project/<id>/run` returns HTTP 503 before claiming a fresh run. An
external agent can still receive a manifest handoff explicitly with
`/run?agent_id=<your-id>`; that path never spawns a second process.
