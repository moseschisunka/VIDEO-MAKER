# Proposal Music Decision Contract

Every audio-capable proposal must resolve music before assets are generated.
The decision is explicit even when the answer is **no music**.

At proposal stage:

1. Inspect `music_library/` and report available tracks and measured durations.
2. Report music-generation providers from the runtime registry honestly.
3. Present four mutually exclusive choices: `user_library`, `ai_generated`,
   `bring_your_own`, or `none`.
4. Record the selected object in
   `proposal_packet.production_plan.music_source` and append a
   `decision_log` entry with `category: "music_source"`.
5. Validate the proposal before any asset generation or paid operation.

Required fields:

- `none`: `source_type` and a human-readable `reason`; no `track_path`.
- `user_library`: `source_type` and the selected relative `track_path`.
- `bring_your_own`: `source_type`, `track_path`, and a rights basis (`license`
  or `reason`).
- `ai_generated`: `source_type`, `provider`, and `prompt` or
  `mood_direction`; include the exact `model` when the provider exposes one.

Never infer a music choice from a cost line item, a previous run, or a missing
asset. A missing or ambiguous choice is a proposal-stage blocker. Downstream
asset manifests add the provider/license/prompt/model/duration/loop/edit-rights
provenance required for release.
