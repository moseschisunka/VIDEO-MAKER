# PR-10G — HyperFrames QA evidence

Status: **PASS (local cached-runtime diagnostic and supported Ubuntu certification; frozen-RC proof still required)**

The opt-in HyperFrames QA harness now accepts `HYPERFRAMES_QA_OFFLINE=1` so a
release check can use the installed package cache without silently querying the
npm registry. This is an execution-path improvement, not a production
certification or a runtime substitution.

## Verification

Command from the repository root:

```text
python -c "import os,pytest; os.environ['HYPERFRAMES_QA']='1'; os.environ['HYPERFRAMES_QA_RENDER']='1'; os.environ['HYPERFRAMES_QA_OFFLINE']='1'; raise SystemExit(pytest.main(['tests/qa/test_09_hyperframes_compose.py','-q']))"
```

Earlier result:

```text
2 passed in 30.31s
```

The current cached runtime probe reports HyperFrames **0.8.25**, Node major
22, FFmpeg available, and no runtime-check reasons. The production-mode
fixture completed scaffold → lint → validate → inspect → render; the render
produced a non-empty 5-second MP4 (150 frames) from the cached browser and
vendored GSAP runtime.

The standard online probe remains available for the opt-in CI job. The core
supported run `33710765514` intentionally skipped that opt-in job, but the
dedicated supported Ubuntu certification is now recorded below. A frozen
release-candidate rerun remains required before `RUN-07`, `RUN-08`, or `PR-10G`
can be marked complete.

## Contract-hardening follow-up (2026-09-03)

The adapter now applies the public `offline` flag consistently to direct
`lint`, `validate`, `inspect`, and `add_block` operations, not only to
`doctor` and `render`. Its lint-report parser also treats an omitted
`findings`/`issues` array as an empty list, so a valid empty JSON report cannot
surface a `TypeError` or accidentally bypass the operation result contract.

Verification:

```text
python -m pytest tests/tools/test_hyperframes_compose.py -q
```

Result: **46 passed in 76.21s**. The supported CI/container and frozen-RC
requirements above are unchanged.

The same offline QA command was rerun after this hardening with the current
tree: **2 passed in 29.98s**.

Latest local rerun (2026-09-03) used the same explicit cached-runtime flags and
completed the full scaffold → lint → validate → inspect → render path again:

```text
python -c "import os,pytest; os.environ['HYPERFRAMES_QA']='1'; os.environ['HYPERFRAMES_QA_RENDER']='1'; os.environ['HYPERFRAMES_QA_OFFLINE']='1'; raise SystemExit(pytest.main(['tests/qa/test_09_hyperframes_compose.py','-q']))"
2 passed in 62.51s
```

This confirms the current Windows cache remains executable. The supported
Ubuntu opt-in run is recorded below; the frozen-release-candidate rerun is
still outstanding.

## Supported clean-runtime certification (2026-09-03)

The opt-in CI workflow now pins Node 22 for this job, enables both QA switches,
and retains the exact pytest output as a downloadable artifact. The supported
run on commit `671a8dc` completed the real render path:

```text
HYPERFRAMES_QA=1 HYPERFRAMES_QA_RENDER=1
.venv/bin/python -m pytest tests -m hyperframes_qa -q
2 passed, 1 skipped, 1512 deselected, 1 warning in 110.51s
```

The evidence-retention rerun on commit `2bf54ae` is an earlier authoritative
record:

```text
2 passed, 1 skipped, 1512 deselected, 1 warning in 88.59s
```

It passed the scaffold/lint/validate test and the full HyperFrames MP4 render,
and uploaded the raw log as [artifact `openmontage-hyperframes-qa`](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33718631193/artifacts/9879467068).
The immutable [workflow run](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33718631193)
also passed release-blocker contracts, clean install, offline regression,
container/browser rendering, and Phase 10 SLO/load/operations. The single
skipped item is retained in the raw log for follow-up; no failure was hidden.

This closes the supported HyperFrames sub-check only. The frozen release
candidate must still repeat the acceptance matrix, and `PR-10G` remains blocked
by deployment rollback, external monitoring/alert delivery, and trusted-edge
security proof.

The latest supported workflow-dispatch certification is on commit `ac4fd9b`
with the HyperFrames switch enabled:

```text
HYPERFRAMES_QA=1 HYPERFRAMES_QA_RENDER=1
.venv/bin/python -m pytest tests -m hyperframes_qa -q
2 passed, 1 skipped, 1514 deselected, 1 warning in 72.92s
```

It completed the real scaffold/lint/validate/inspect/render path and retained
the raw output as [artifact `openmontage-hyperframes-qa`](https://github.com/moseschisunka/VIDEO-MAKER/actions/runs/33724899296/artifacts/9881651965).
The same supported run passed the release-blocker, clean-install, offline
regression, container/browser, and Phase 10 SLO/load/operations jobs; its live
provider job was intentionally skipped. This is the current supported
checkpoint, not the frozen release-candidate certification.

## Runtime preflight timeout hardening (2026-09-03)

The Windows runtime probe previously used `subprocess.run()` with an npm
`.CMD` wrapper. When the npm registry was unreachable, the wrapper's Node
child could retain inherited stdout/stderr pipe handles after the nominal
five-second timeout, causing `VideoCompose.get_info()` to block for more than
one minute. The shared `BaseTool` CLI boundary now runs commands in a bounded
process group, terminates the complete wrapper tree, and captures output
through temporary files so cleanup never drains descendant-owned pipes
indefinitely. HyperFrames inherits this boundary, as do Remotion, FFmpeg,
analysis, and other CLI-backed tools.

Verification:

```text
python -u -c "import time; from tools.video.video_compose import VideoCompose; started=time.perf_counter(); info=VideoCompose().get_info(); print({'elapsed_seconds':round(time.perf_counter()-started,3),'engines':info['render_engines']})"
```

Result: **5.447 seconds**, with a structured HyperFrames-unavailable status
(`ffmpeg` and Remotion remained available). The focused adapter suite is now
**47 passed**, including a regression test that asserts timeout cleanup does
not use pipe-backed output or an unbounded `communicate()` call. The base-tool
timeout regression also confirms callers still receive `TimeoutExpired` in
under four seconds. Phase 7/8 contracts remain **24 passed**, the combined
base/adapter unit suites are **50 passed**, and the explicit cached-runtime QA
remains **2 passed in 56.15s**.

This is a reliability improvement to the diagnostic and render subprocess
boundary. The supported Ubuntu HyperFrames certification is recorded above;
the frozen-RC evidence required to close `PR-10G` remains outstanding.
