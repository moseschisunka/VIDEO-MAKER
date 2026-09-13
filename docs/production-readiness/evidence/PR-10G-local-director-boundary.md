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
files, inspect a same-user Backlot process, or call loopback APIs using the
global authorization model. The working directory is a convenience and CLI
workspace boundary, not a filesystem sandbox.

Backlot also does not verify Claude's saved billing mode or Antigravity's
account/plan. Operators must confirm that Claude Code is signed in with a
subscription rather than Anthropic Console API billing, and that Antigravity
has an authenticated account. The current machine is not yet ready to launch
Codex or Claude Code: Codex needs ChatGPT sign-in and Claude Code needs
installation.

Until OS-enforced isolation and a short-lived capability limited to one
project, run, and set of Backlot operations are implemented and tested, the
production secret boundary remains unproven. Keep `INV-13` and `SEC-08`
blocked.

## Required closure

1. Move production provider credentials out of files and process state
   accessible to the director identity.
2. Run each CLI under an OS-enforced isolated identity or sandbox with only its
   project workspace and explicitly approved local account profile available.
3. Replace shared Backlot authorization with a short-lived run capability.
4. Verify local account auth, blocked credential canary and sibling-process
   access, allowed project edits, denied cross-project requests, and clean
   failure for missing or signed-out CLIs on every supported OS.
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
