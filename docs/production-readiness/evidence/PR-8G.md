# PR-8G — Phase 8 integration gate

Status: **COMPLETE — Phase 8 integration gate passed**

The music, mix, caption, and accessibility contracts are now complete. The
gate covers explicit no-music selection and provenance, independently
addressable stems, measured loudness/true peak/silence/clipping/channel facts,
verified transcript captions, runtime-specific rendering, safe-area/wrapping,
contrast/font/read-speed policy, and sidecar packaging.

Evidence:

```text
python -m pytest -q tests/contracts/test_phase8_gate.py
2 passed in 2.06s

python -m pytest -q tests/contracts/test_phase8_caption_render.py tests/contracts/test_phase8_audio.py
19 passed in 3.12s
```

The gate's generated fixture runs the audio mixer and quality probe, verified
SubtitleGen, the Remotion caption burner (FFmpeg fallback for deterministic
test speed), FFmpeg standalone burn-in, and HyperFrames caption scaffolding.
Separate real runtime proof is recorded in `PR-805.md` (Remotion render and
strict offline HyperFrames render). Production remains locked until `PR-11G`.
