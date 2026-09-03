# OpenMontage secrets, privacy, retention, and deletion policy

Status: documented production contract. Automated purge and self-service
deletion remain future operational work; the `PR-1008` backup/restore and
migration drill is documented separately. All operational gates must pass
before a production label is permitted. The release gate remains `PR-11G`.

## Secrets

Provider credentials and the Backlot bearer token are runtime configuration,
not project artifacts. Use the environment or an external secret manager in
deployed environments. `.env` is permitted only for unpublished local
development and is ignored by Git and the container build context.

Never put credentials in prompts, JSON artifacts, event payloads, issue text,
URLs, or shell history. The shared redaction boundary masks configured secret
values, bearer credentials, common secret assignments, and signed URL query
parameters before they enter tool results, provider errors, or
`projects/<id>/events.jsonl`. Redaction is a safety net, not permission to
send secrets to a creative provider.

For a remotely reachable Backlot, set `BACKLOT_HOST=0.0.0.0` and provide
`BACKLOT_AUTH_TOKEN` through the runtime secret store. The service refuses
remote requests when the token is missing. `BACKLOT_PROJECT_SCOPE` can limit a
process to exact project ids or trailing-`*` tenant prefixes. Do not pass the
token in a query string or commit it to Compose, CI, or an image layer.

## Privacy and user media

Projects, source uploads, generated assets, narration, transcripts, renders,
and review frames are private production data. The default product posture is
local-first: local FFmpeg/Remotion/HyperFrames/Piper/ComfyUI operations do not
send media to a third party. Remote Backlot media access is authenticated,
project-scoped, and confined to the project root; symlink escapes are rejected.

Identifiable teachers require consent covering YouTube, Shorts, social
repurposing, thumbnails, promotional clips, and iLearnZed use. Identifiable
students, especially minors, are excluded by default until the appropriate
consent evidence is recorded. A user-media asset may leave the local runtime
only after the selected operation and provider are disclosed and the required
approval/consent is present.

## Provider disclosure

Before an external operation, the run/asset record must identify the strategy,
provider, model, data class sent, task/source reference, timestamp, and the
applicable license or provider-terms reference. The disclosure must be honest:
OpenMontage does not promise a provider's retention, training, geographic
processing, or deletion behavior. Those terms can change and must be checked
for the selected provider at release time.

Typical data classes are:

| Strategy | What may leave the runtime | User-media rule |
|---|---|---|
| Local render/analysis | Nothing to a provider | Allowed subject to consent and path validation |
| Stock search/download | Search query and request metadata | User media is not sent by default |
| AI image/video | Prompt and any declared reference images/video | Explicit approval required |
| TTS/STT | Script, audio, or transcript input as declared | Explicit approval for user recordings |
| Avatar/lip-sync | Face/voice media when selected | Explicit approval and consent required |

## Retention and deletion defaults

These are product defaults, not a legal guarantee. They become enforceable only
after the purge and recovery tasks certify the implementation:

- user source media: 30 days after a terminal run;
- generated derivatives: 30 days after a terminal run;
- final outputs: 90 days after a terminal run;
- events and diagnostics: 90 days after a terminal run;
- provider-result cache: 30 days;
- failed-run workspaces: 24 hours; and
- backups: 180 days.

Until automated deletion is certified, operators must not imply that a project
has been purged. A deletion request covers source and derived media. Stop
active runs, identify the exact project root, export/backup when required, and
delete only that project’s source/derivative paths. Preserve a minimal audit
record (project/run ids, deletion timestamps/scope, and hashes rather than
content) until the audit-retention period expires. Provider-side deletion is
best effort and remains subject to the provider's current terms.

The machine-readable contract is
[`config/security_policy.yaml`](../../config/security_policy.yaml). Changes to
these defaults require an evidence update and a re-run of the security,
retention, and recovery gates.
