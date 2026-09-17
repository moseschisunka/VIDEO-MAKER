# Local directors and their security boundary

**Status: BLOCKED for production certification**
**Reviewed:** 2026-09-13
**Base revision:** `e36a6e9` (`feat(backlot): launch selected local directors`)
**Scope:** Codex CLI, Claude Code CLI, and Antigravity CLI started by Backlot on the local Windows device.

## Intended use

OpenMontage's configured provider API credentials are for production media and
voice calls. A local director should use its own CLI sign-in. The ChatGPT
desktop conversation currently being used to direct this work cannot itself be
spawned by Backlot; the local option is Codex CLI.

## Implemented in the working tree

- The project board and creation wizard have a per-run director selector for
  Codex, Claude Code, and Antigravity. The selection is saved in browser
  storage; it no longer requires editing `.env` or restarting Backlot. When a
  director reports confirmed ChatGPT sign-in, the UI can default to it. A
  director with unverified account status remains manually selectable and
  does not silently replace the configured runner.
- Backlot validates a selected director before claiming work, then launches
  its installed CLI in that project's directory. Codex API-key sign-in is
  rejected; Codex only launches after `codex login status` confirms ChatGPT
  sign-in. Unknown Codex auth status also fails closed.
- Run Pipeline writes the browser-selected manifest, stage instructions,
  agent guide, and project context into a `.openmontage/` handoff bundle in the
  project directory. The local CLI receives a prompt pointing to those files.
- Credential-like environment variables are removed from the CLI environment.
  The child process does not receive Backlot's production provider keys or its
  global Backlot token.
- The built-in local MCP bridge now requires a random, short-lived capability
  issued by Backlot. Backlot validates the exact project path, project id, run,
  agent, and live work-order lease before the bridge can read the work order or
  execute a tool. The token cannot authorize a different project/run and is
  separate from both the production API key and the global Backlot token.
- Setup notes distinguish ChatGPT sign-in from Codex API-key billing and
  Claude subscription sign-in from Anthropic Console API billing. Antigravity
  headless runs require its cached credentials.

## Host readiness checked

The local catalog reported:

- Codex CLI is installed but not signed in. Run `codex login` and choose
  ChatGPT sign-in before selecting it.
- Claude Code CLI is not installed on `PATH`.
- `agy` is installed; Backlot cannot inspect its saved account or plan. Sign in
  through an interactive `agy` session before using headless runs.

No real CLI model session or production provider request was started.

## Verification

```text
python -m pytest tests/contracts/test_agent_launcher.py tests/contracts/test_phase10_security.py tests/backlot/test_run_pipeline_ui.py -q
39 passed in 62.23s

python -m pytest tests/contracts/test_phase10_package_data.py -q
3 passed in 236.93s

python -m compileall -q lib/agent_launcher.py lib/local_director.py backlot/server.py
exit 0

node --check backlot/ui/board.js
node --check backlot/ui/library.js
exit 0

git diff --check
no whitespace errors (Git reports the existing CRLF normalization warning for backlot/server.py)
```

The tests cover per-run director selection, work-order identity, credential
environment filtering, selected-project working directory, Windows PowerShell
shim arguments, handoff bundle contents, symlink rejection, Codex billing-mode
gating, and the browser run flow. The package-data suite installed a wheel in a
normal virtual environment, found the conventional `config.yaml` and Remotion
paths, and rendered an FFmpeg sample from outside the source checkout. The
tests do not prove isolation from files or processes owned by the same Windows
user.

## Why production certification remains blocked

Environment filtering is not OS isolation. A local director still runs as the
Backlot user's Windows identity. It may be able to read `.env` or other profile
files, inspect a same-user Backlot process, or call local Backlot APIs outside
the built-in bridge. The working directory is a convenience and CLI workspace
boundary, not a filesystem sandbox.

Backlot also does not verify Claude's saved billing mode or Antigravity's
account/plan. Operators must confirm that Claude Code is signed in with a
subscription rather than Anthropic Console API billing, and that Antigravity
has an authenticated account. Current host status is recorded under
"Current device availability and smoke" below: Codex still needs ChatGPT
sign-in; Claude Code is installed but remains an interactive, user-submitted
handoff; Antigravity completed one headless no-op smoke. These checks do not
verify subscription or plan eligibility.

The short-lived run capability is now implemented and contract-tested, but it
does not defend against a malicious process running as the same Windows user.
That process can still read `.env`, inspect same-user processes, or copy the
capability from local MCP configuration. The capability registry also lives in
Backlot process memory; restarting Backlot invalidates existing MCP sessions,
which then fail closed. The process-local registry also requires a single
Backlot worker; a capability minted in one worker is unknown to another.
Operating-system isolation and live tests of cross-project, credential-canary,
sibling-process, and provider-call denial are still absent. Keep `INV-13` and
`SEC-08` blocked.

## Required closure

1. Move production provider credentials out of files and process state
   accessible to the director identity.
2. Run each CLI under an OS-enforced isolated identity or sandbox with only its
   project workspace and explicitly approved local account profile available.
3. Exercise the short-lived capability against a running Backlot instance for
   allowed project edits and denied cross-project/run, stale-lease, and expired
   capability requests on every supported CLI and OS.
4. Verify local account auth, blocked credential canary and sibling-process
   access, and clean failure for missing or signed-out CLIs on every supported
   OS.
5. Only then mark `INV-13` and `SEC-08` `PASS` for the tested CLI/OS
   combinations.

**Reviewer:** Codex code and test review. No production API request occurred.

## Follow-up verification (2026-09-13)

The local catalog was rechecked on the current Windows host:

- Codex CLI is installed but signed out (`not_signed_in`).
- Claude Code CLI is not installed (`unavailable`).
- Antigravity `agy` is installed, but its authentication remains unverified
  (`unknown`).

No CLI model session or production provider request was started. The browser
regression fixture sets Antigravity to installed/ready with unknown auth and a
configured runner present; both the project board and creation wizard keep the
configured runner selected by default.

```text
python -m pytest tests/contracts/test_agent_launcher.py tests/contracts/test_phase10_security.py tests/backlot/test_run_pipeline_ui.py -q
39 passed in 19.48s

python -m pytest tests -m "not live_provider and not hyperframes_qa" -q
1824 passed, 6 skipped, 3 deselected, 1 subtests passed in 561.80s

node --check backlot/ui/lib.js
node --check backlot/ui/board.js
node --check backlot/ui/library.js
git diff --check
all exited 0
```

The broad suite excludes live-provider and HyperFrames QA coverage. Its final
run includes the explicit `auth_status == "chatgpt"` default allowlist and the
shared DOM boolean fix. The browser fixture confirms that an unknown-auth CLI
remains manually selectable while the configured runner stays selected by
default. These results verify the offline code baseline only; they do not
satisfy the environment-owned operational, production-secret isolation, or
human AV release gates above.

## Scoped-capability follow-up (2026-09-13)

The run capability, project Git ignore rules, Backlot lease validation, and
capability validation before `.env` loading were checked after implementation:

```text
python -m pytest tests/contracts/test_local_director_capabilities.py tests/contracts/test_local_director_mcp.py tests/contracts/test_agent_launcher.py tests/contracts/test_provider_approval_flow.py tests/backlot/test_run_pipeline_ui.py -q
49 passed in 40.93s

python -m py_compile lib/local_director_capabilities.py lib/agent_mcp.py lib/agent_launcher.py lib/local_director.py backlot/server.py
node --check backlot/ui/board.js
node --check backlot/ui/library.js
git diff --check
all exited 0; Git reported only the CRLF normalization warning for backlot/server.py
```

The full offline regression also completed during this follow-up with **1,848
passed, 6 skipped, 3 deselected, and 1 subtest passed**. The final Git ignore
and environment-load ordering edits landed while that run was already
executing; the focused run above covers those final edits.

## Backlot route and launcher follow-up (2026-09-13)

Removed two unused imports from `backlot/server.py` after confirming there were
no repository or test references to those module attributes.

```text
python -m pytest tests/backlot/test_run_pipeline_ui.py tests/contracts/test_agent_launcher.py -q
28 passed in 27.12s

python -m ruff check --select F401 backlot/server.py
All checks passed!
```

## Current offline regression (2026-09-13)

The full current working tree passed the offline regression suite. Live-provider
and HyperFrames QA tests were excluded by marker; no model or production API
request was made.

```text
python -m pytest tests -m "not live_provider and not hyperframes_qa" -q
1851 passed, 6 skipped, 3 deselected, 1 subtests passed in 580.40s
```

## Security scan tooling follow-up (2026-09-13)

A repository Deep Scan attempt failed before returning a scan identifier or
artifacts:

```text
Codex Exec exited with code 2: error: unexpected argument '--thread-source' found
codex-cli 0.146.0; `codex exec --help` does not list `--thread-source`
```

No scan findings or coverage are available, and the attempt is not counted as a
clean scan. Daybreak access was not granted, so protected results may also be
unavailable. No replacement scan was started.

## Local director preflight hardening (2026-09-13)

Antigravity's headless runner now requires the three exact OpenMontage MCP
allow rules before a model process can start. A matching Ask/Deny rule,
including `mcp(*)`, blocks the launch before any provider usage. Backlot reads
only the permission lists needed for this check and does not return other
settings. Claude Code's native per-user install path is discovered even when
the installer has not added it to `PATH`; it remains an interactive,
user-submitted handoff.

```text
python -m pytest tests/contracts/test_agent_launcher.py tests/contracts/test_local_director_mcp.py tests/backlot/test_run_pipeline_ui.py -q
48 passed in 28.30s

node --check backlot/ui/board.js
node --check backlot/ui/library.js
python -m ruff check --select F401 lib/local_director.py lib/agent_launcher.py lib/agent_mcp.py tests/contracts/test_agent_launcher.py
git diff --check
all exited 0; Git reported only the CRLF normalization warning for backlot/server.py
```

The preflight validates local configuration only. It does not prove account
sign-in, subscription allowance, production isolation, or provider availability,
and it does not change any PR-10G or PR-11G release-gate status.

## Current device availability and smoke (2026-09-13)

The current checkout's local director catalogue and CLI installations were
checked again:

- Codex CLI `0.146.0` is installed but signed out. Its ChatGPT sign-in still
needs to be completed before Backlot will launch it.
- Claude Code `2.1.236` is installed at
  `%USERPROFILE%\.local\bin\claude.exe`, although `claude` is not on `PATH`.
  It remains an interactive, user-submitted handoff.
- Antigravity CLI `1.1.26` is installed; its three exact OpenMontage MCP
  permissions pass preflight. The account cannot be introspected by Backlot.
  The existing `useG1Credits` setting was observed as `false`.

One no-op headless Antigravity prompt returned `SUCCESS` in 30.4 seconds and
reported 18,016 total tokens. This verifies cached-account headless access on
this device only. It is not an OpenMontage MCP, pipeline, production-provider,
or video-render test. No OpenAI production API request was made.

The existing Backlot listener on port `51506` initially returned the old
catalogue with Claude marked unavailable. Its eight projects had no live runs
or human approvals, and their IDs matched the current worktree's project data;
the listener was restarted from this checkout. The current `/api/health` and
`/api/local-directors` endpoints now return the updated build and discover
Claude Code. The already-open browser page may need a manual reload. This
restart does not upgrade the production or human review gates.

```text
python -m pytest tests/contracts/test_agent_launcher.py tests/contracts/test_local_director_mcp.py tests/contracts/test_local_director_capabilities.py tests/contracts/test_provider_approval_flow.py tests/backlot/test_run_pipeline_ui.py -q
53 passed in 28.15s

GET http://127.0.0.1:51506/api/health
200 {"ok":true,"app":"backlot"}

GET http://127.0.0.1:51506/api/local-directors
Codex: installed, not_signed_in
Claude Code: installed, interactive_only
Antigravity: installed, MCP permissions ready; account auth unverified
```

The Antigravity account smoke consumed included local CLI usage. No purchased
AI-credit overage was enabled, and no OpenAI API key was supplied to that
process. Production credential isolation remains uncertified; `SEC-08` stays
blocked until OS-level isolation and the credential-canary and hostile
cross-project tests pass.

## Sandboxed headless smoke (2026-09-13)

After adding Antigravity's `--sandbox` flag to every headless launch, a second
no-op prompt completed successfully from `%TEMP%` in 34.0 seconds and reported
18,208 total tokens. The exact requested response was returned. This confirms
the local CLI accepts the sandboxed headless invocation; it does not verify an
OpenMontage MCP call, a manifest stage, production-provider access, or a video
render. The direct smoke command made no production API request and did not
exercise Backlot's launcher or the MCP bridge. The first no-op smoke above
remains historical evidence for the unsandboxed invocation.

The focused launcher, MCP, capability, approval, and UI regression suite was
rerun after the sandbox flag change:

```text
python -m pytest tests/contracts/test_agent_launcher.py tests/contracts/test_local_director_mcp.py tests/contracts/test_local_director_capabilities.py tests/contracts/test_provider_approval_flow.py tests/backlot/test_run_pipeline_ui.py -q
53 passed in 26.68s
```

Ruff's unused-import checks, both Backlot JavaScript syntax checks, and
`git diff --check` also exited 0. The diff check reported only a CRLF
normalization warning for `backlot/server.py`.
