# PR-3G — Phase 3 integration gate and contract freeze

Status: **COMPLETE**

The Phase 3 provider contract is frozen for downstream phases. Selectors use
the common executor bridge, direct identity-bearing provider calls are
automatically routed by `BaseTool`, material fallback and paid approval are
fail-closed, provider attempts are structured, and capability catalogs use the
fast local preflight path.

Evidence:

```text
python -m pytest -q tests/contracts/test_phase3_contracts.py tests/contracts/test_phase3_provider_contracts.py tests/contracts/test_phase3_provider_executor.py tests/contracts/test_phase3_provider_faults.py tests/contracts/test_phase3_provider_migration.py tests/contracts/test_phase3_preflight.py tests/contracts/test_phase3_cost_tracker.py tests/contracts/test_phase3_selector_plans.py tests/contracts/test_phase3_gate.py
114 passed in 6.19s

python -m py_compile lib/providers/bridge.py lib/providers/contracts.py lib/providers/executor.py lib/providers/fallback.py lib/providers/plans.py lib/providers/preflight.py tools/base_tool.py tools/cost_tracker.py tools/tool_registry.py tools/audio/music_selector.py tools/audio/music_gen.py tools/audio/tts_selector.py tools/graphics/image_selector.py tools/video/video_selector.py
git diff --check
```

The warm and cold preflight measurements are recorded in `PR-307`. Phase 4 may
now begin. This gate does not change the global release decision: production
remains locked until every later phase and `PR-11G` pass.
