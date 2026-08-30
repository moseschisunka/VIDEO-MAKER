# Personal Video Creator Setup

Status as of this setup pass. Re-run the preflight command below any time to refresh.

## What's configured

| Capability | Status | Notes |
|---|---|---|
| Composition | ffmpeg + Remotion + HyperFrames all available | 3/3 render runtimes working |
| TTS | `edge_tts` + `piper_tts` available | zero-key, see Voice below |
| Real-footage sourcing | `direct_clip_search` available | Archive.org/Wikimedia/NASA, zero-key |
| Subtitles | available | word-level captions |
| Video post (mix/stitch/trim) | 9/9 available | |
| Image generation | 0/12 | no AI images or stock images yet — see Upgrades |
| Video generation | 0/20 | no AI video clips yet — see Upgrades |
| Avatar / talking head | 0/4 | needs GPU or a cloud key — see Talking Avatar below |

## Voice — the two paths you asked for

- **`edge_tts`** — Microsoft's neural voices (`en-US-AriaNeural`, `en-US-GuyNeural`, `en-US-ChristopherNeural`, `en-US-JennyNeural`, etc.), free, no key, needs internet. Use for explanations/tutorials.
- **`piper_tts`** — open-source, fully offline neural TTS, free, no key, no internet needed. Use for narrative/documentary-style pieces or when you want zero network dependency. First run needs a voice model:
  ```
  piper --download-dir ~/.piper/models --model en_US-lessac-medium
  ```

Both route through `tts_selector`, or you can name either one explicitly at the script stage. Per Rule Zero, the agent will surface the choice and its reasoning before locking it in — you don't need to hardcode a default.

## Talking avatar — the honest limitation

`talking_head` (SadTalker/MuseTalk) needs an NVIDIA GPU with CUDA. This sandbox has none (2 vCPU, no GPU detected). Two ways forward:

1. **Run OpenMontage on your own machine** if it has an NVIDIA GPU — install SadTalker, set `SADTALKER_PATH`, and `talking_head` becomes available locally, free.
2. **Use a cloud avatar provider** instead — `kling_avatar` / `kling_lip_sync` (needs `KLING_API_KEY`) or HeyGen via `HEYGEN_API_KEY`. No GPU needed, costs per generation.

Until one of those is set up, avatar-led videos aren't available — the `avatar-spokesperson`/`talking-head` pipelines will report this at preflight rather than silently substituting something else.

## Recommended free upgrade

Grab one free key to unlock illustrative visuals for topics that aren't real-footage documentaries — pick any of `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `UNSPLASH_ACCESS_KEY` (all free signup, no card). Without one, only the real-footage documentary path has visuals right now.

## How to make a video

This is agent-driven, not scripted — you ask, the agent drives the pipeline. Per video:

1. Say what you want: topic, duration, tone, platform. Mention if you want a talking avatar vs. narrated visuals vs. real-footage documentary.
2. The agent runs preflight, picks a pipeline from `pipeline_defs/`, and shows you the capability menu + voice/music/runtime choices.
3. It proceeds stage by stage — `research → proposal → script → scene_plan → assets → edit → compose → publish` — pausing for your approval at proposal, script, scene_plan, assets, and publish.
4. Final video lands at `projects/<project-name>/renders/final.mp4`, watchable live in Backlot: `python -m backlot open <project-id>`.

Re-run preflight any time to see your current capability envelope:

```bash
python -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.provider_menu_summary(), indent=2))"
```
