# PR-7G — Phase 7 integration gate

Status: **COMPLETE — Phase 7 integration gate passed**

The OpenMontage adapter contract and the real HyperFrames runtime now pass the
full Phase 7 gate. The production decision remains **Not eligible** and the
global lock remains `PR-11G`.

Evidence:

```text
python -m pytest -q tests/contracts/test_phase7_gate.py tests/tools/test_hyperframes_compose.py
51 passed

real offline HyperFrames 0.8.25:
- lint: 0 errors / 0 warnings
- validate: ok; 0 errors / 0 warnings / 0 contrast failures
- inspect --strict: ok; 0 issues
- render: 90/90 frames, 3.0s MP4, H.264 output validated
- audio fixture: 8.0s H.264 + AAC 48 kHz stereo, offsets/fades/ducking preserved
- two concurrent isolated workspaces: both exit 0, distinct final MP4s
```

The remaining optional doctor findings (local whisper/TTS/MusicGen and Docker)
are explicitly recorded and do not block the certified browser/FFmpeg path.
Phase 8 is the next roadmap phase; production remains locked until `PR-11G`.
