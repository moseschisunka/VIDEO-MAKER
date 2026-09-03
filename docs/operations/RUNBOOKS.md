# OpenMontage operator and incident runbooks

Status: `PR-1010` implementation. These procedures are release evidence, not
permission to declare production. The service remains ineligible until the
complete Phase 10 gate and `PR-11G` have passed.

The procedures assume an operator has an approved release/change record,
access to the deployment host, and permission to inspect only the affected
project. They preserve durable evidence first and avoid hand-editing state.
Use placeholders such as `<PROJECT_ID>`, `<IMAGE_DIGEST>`, and
`<BACKUP_ARCHIVE>` literally as values to be supplied by the operator; never
paste credentials into a command that will be retained in a ticket or shell
history.

## 1. Severity, ownership, and universal stop rules

| Severity | Examples | Initial response | Escalation target |
|---|---|---|---|
| P0 | unauthorized access, secret exposure, wrong/stale deliverable presented as current, duplicate paid generation, cross-project contamination, unrecoverable state corruption | stop affected dispatch, preserve evidence, roll back or disable the affected capability immediately | release owner + engineering + security |
| P1 | selected provider/voice/runtime not honoured, critical QA defect, repeated stuck jobs, failed restore, SLO page condition | pause the affected pipeline/provider, open an incident, recover within the declared objective | release owner + engineering |
| P2 | degraded latency, provider quota pressure, elevated retries, UI reconnect churn without data loss | monitor, rate-limit, or move to an approved degraded lane; create a follow-up | operations + owning engineer |
| P3 | cosmetic or non-blocking maintenance issue | backlog with evidence | owning team |

Stop immediately and escalate when any procedure would require:

- silently changing the selected provider, model, voice, runtime, output
  profile, media type, or factual-claim policy;
- editing `work_order.json`, checkpoints, run records, approval records, or
  event history by hand;
- overwriting a final output before the current candidate has passed media and
  provenance validation;
- placing a token, service-account file, signed URL, prompt, script, or
  transcript in logs, tickets, screenshots, backups, or source control;
- deleting a project, run, artifact, or backup without a recorded user/change
  approval and a verified backup.

Automatic rollback/feature-disable triggers are the conditions in the
[acceptance matrix](../production-readiness/ACCEPTANCE_MATRIX.md), including
wrong or stale output, provider/runtime identity drift, duplicate spend,
critical QA escape, unauthorized access, secret exposure, and durable-state
corruption.

## 2. Pre-deploy and deploy

### 2.1 Pre-deploy checklist

1. Record the release candidate ref, build timestamp, operator, intended
   pipeline scope, image digest (if containerized), and the exact environment
   versions. Do not call a working tree a release candidate; `PR-1100` freezes
   the ref later.
2. Confirm that the acceptance matrix has current evidence for the intended
   scope. At minimum, inspect `PR-1003` Docker proof, `PR-1009` load/soak JSON,
   and all rows that the change can affect.
3. Run the offline contract gates from the repository root:

   ```text
   python -m pytest tests/contracts/ -m "release_blocker and not live_provider and not hyperframes_qa" -q
   python scripts/measure_slos.py > slo-baseline.json
   python scripts/measure_load_soak.py --output pr1009-load-soak.json
   ```

   A failed or missing sample is a stop condition. Do not turn a skipped live
   or HyperFrames check into a pass.
4. Validate package/container inputs without exposing secrets:

   ```text
   python -m pip check
   python -m compileall -q backlot lib schemas tools
   docker compose config
   ```

5. Verify persistent storage is mounted and writable only where intended
   (`projects/` and `output/`), the deployment is bound to the approved host,
   and a recent encrypted backup exists outside the project tree. `.env`,
   service-account JSON, private keys, and provider tokens must not be in the
   image, archive, or repository.
6. Confirm the chosen launch lane and provider set. A missing dependency,
   quota, or live-provider smoke result is `BLOCKED`, not a reason to silently
   substitute another provider.

### 2.2 Container deployment

Build and record the immutable image digest in the change record:

```text
docker build --pull --tag openmontage:<RELEASE_TAG> .
docker image inspect openmontage:<RELEASE_TAG> --format "{{index .RepoDigests 0}}"
docker compose up -d --no-build openmontage-backlot
docker compose ps
```

The runtime secret store supplies `BACKLOT_AUTH_TOKEN`; it is not passed as a
CLI argument. For a remote binding, require authentication and test the health
endpoint with a short-lived operator token held outside the command history:

```text
curl.exe --fail-with-body -H "Authorization: Bearer <RUNTIME_TOKEN>" http://127.0.0.1:4750/api/health
curl.exe --fail-with-body -H "Authorization: Bearer <RUNTIME_TOKEN>" http://127.0.0.1:4750/api/release-status
```

If the service does not become healthy within the declared start window, stop
the deployment, collect container logs (redacted), and use the rollback
procedure. Never widen the network bind or disable authentication as a quick
fix.

### 2.3 Local/non-container start

Loopback-only development may use:

```text
python -m backlot serve --port 4750
```

This is not a remote deployment substitute. `BACKLOT_HOST=0.0.0.0` requires a
runtime token and the same reverse-proxy, authorization, CSRF/CORS, and rate
limit review as a container.

## 3. Rollback and bad deploy

Use rollback first when the trigger affects data, spend, identity, access, or
deliverable correctness. Diagnose after service safety is restored.

1. Announce the incident/change ID and freeze new runs for the affected lane.
2. Capture `docker compose ps`, health output, the bounded `/api/metrics`
   snapshot, and redacted logs. Preserve project `work_order.json`, `run.json`,
   checkpoints, `events.jsonl`, and candidate files; do not edit or delete
   them.
3. Stop only the affected service gracefully:

   ```text
   docker compose stop openmontage-backlot
   ```

   If a worker is external, stop dispatch and let active work reach a durable
   terminal/resumable state before killing it. Record any forced termination.
4. Select the last known-good immutable image digest from the deployment
   record. Do not use an unreviewed local tag or a dirty working tree:

   ```text
   docker image inspect openmontage@<KNOWN_GOOD_DIGEST>
   docker compose up -d --no-build openmontage-backlot
   ```

5. Verify health, authentication, project scope, and a tiny local/fake smoke
   project. Verify that existing state and the last known-good final remain
   present and that no failed candidate was promoted.
6. Keep the failed image, logs, and evidence available for investigation. Do
   not garbage-collect them until the incident owner signs off.
7. If state is suspect, follow the staged restore procedure in §7 before
   accepting new work. A rollback that restores code but loses state is a
   failed rollback (`REC-03`).

## 4. Provider outage, quota, or throttling

### Detection

Look for structured provider `attempt_error`, `backoff`, `circuit_open`, and
`failed` events, the provider latency/error counters, queue wait, and the
provider's own status/quota notice. Correlate by project, run, stage, attempt,
and provider; never use prompts, scripts, tokens, or signed URLs as dimensions.

### Response

1. Classify the symptom as transient/429, permanent 4xx, timeout, malformed
   response, quota, or provider outage. Preserve the exact structured error
   and idempotency key (the key is safe evidence; the request payload may not
   be).
2. Pause new dispatch to the affected provider/capability at the scheduler or
   deployment boundary. Let the central `ProviderExecutor` enforce its
   bounded timeout, retry, backoff, circuit, and rate policy. Do not add an
   ad-hoc retry loop inside a tool.
3. Do not silently switch provider, model, voice, runtime, or media type. If
   the approved plan declares a compatible non-material fallback, use only
   that plan. A material provider/media fallback requires a new explicit human
   approval and a new decision/event record.
4. For queued work, leave the durable order queued or failed with a structured
   blocker. For an active lease, heartbeat while work is genuinely alive;
   otherwise cancel/restart through the API so the lease can expire/reclaim
   safely. Never claim a second run to “unstick” a provider call.
5. Use local/fake providers for recovery verification. A passing fake probe is
   not evidence that the cloud provider is healthy; re-enable the provider only
   after an authorized, cost-bounded smoke check in Phase 11.
6. Close the incident only when error rate, latency, queue depth, spend, and
   identity/fallback checks are back within the SLO/error budget and the
   affected runs have an inspectable terminal or resume state.

## 5. Stuck, abandoned, or duplicate job

1. Read the project state and durable work order before touching a process:

   ```text
   curl.exe --fail-with-body -H "Authorization: Bearer <RUNTIME_TOKEN>" http://127.0.0.1:4750/api/project/<PROJECT_ID>/state
   curl.exe --fail-with-body -H "Authorization: Bearer <RUNTIME_TOKEN>" http://127.0.0.1:4750/api/project/<PROJECT_ID>/work-order
   curl.exe --fail-with-body -H "Authorization: Bearer <RUNTIME_TOKEN>" http://127.0.0.1:4750/api/project/<PROJECT_ID>/execution
   ```

2. Compare `status`, `current_stage`, `next_stage`, lease owner/expiry,
   `resume`, last successful checkpoint, `run.json`, and recent events. A live
   worker must heartbeat; an expired lease is reclaimable. A second `/run`
   request must return the active order/idempotent replay, not create another
   paid or render operation.
3. If the worker is alive, do not reclaim it. If it is dead and the lease has
   expired, use the authenticated resume endpoint with a new operator/agent
   ID:

   ```text
   curl.exe --fail-with-body -X POST -H "Authorization: Bearer <RUNTIME_TOKEN>" -H "Content-Type: application/json" -d "{\"agent_id\":\"<RECOVERY_AGENT>\",\"lease_seconds\":300}" http://127.0.0.1:4750/api/project/<PROJECT_ID>/resume
   ```

4. If the run is actively wedged or must be stopped, cancel first. Cancellation
   preserves checkpoints, run records, paid artifacts, and history:

   ```text
   curl.exe --fail-with-body -X POST -H "Authorization: Bearer <RUNTIME_TOKEN>" -H "Content-Type: application/json" -d "{\"agent_id\":\"<OWNER_OR_RECOVERY_AGENT>\",\"reason\":\"<INCIDENT_ID>\"}" http://127.0.0.1:4750/api/project/<PROJECT_ID>/cancel
   ```

5. After the cause is understood, reopen a cancelled/failed order only through
   the restart endpoint. Confirm the returned `next_stage` is manifest-derived
   and that completed stages are not repeated. Do not delete a run directory
   to clear a lock.
6. Validate one active owner, one run ID, no duplicate idempotency key spend,
   and a structured terminal/resume reason. Record the before/after order
   snapshots in the incident evidence with sensitive fields removed.

## 6. Corrupt, partial, or wrong artifact

1. Stop presentation/publication of the affected output and mark the incident
   P0/P1 according to whether a user saw it. Keep the previous known-good final
   separate; never use it as proof that the current run succeeded.
2. Inspect the current run's candidate, render report, media probe, checksum,
   timestamps, `run_id`, and final-review result. A zero-byte, partial,
   decode-failing, wrong-duration/profile, wrong-voice, or wrong-runtime output
   is a failure even if the process exited zero.
3. Preserve the candidate and logs for evidence. Quarantine it by moving it to
   an incident-specific evidence location under the same project boundary;
   do not overwrite or delete it. If a safe move is impossible, stop and
   escalate rather than using a broad cleanup command.
4. Check the asset manifest and cache entry. Remove only the affected corrupt
   cache entry through its supported cache operation; a partial `.part` file
   is never an approved asset. Re-run the asset validation/probe locally before
   allowing regeneration.
5. If durable state or history is damaged, stop workers and follow §7. Restore
   to a new root first, validate identity/checksums, then perform an explicit
   overwrite that preserves the old tree under `.pre-restore-*`.
6. Re-run technical QA and the required human review. Publication remains
   blocked until the current run has fresh provenance and a passing final
   review. Record the quality-escape outcome even when rollback prevented user
   exposure.

## 7. Backup, restore, and state migration

Backups are integrity-checked ZIPs, not encrypted by OpenMontage. Store them in
an approved encrypted location with access control and the documented
retention period. Never include `.env`, keys, tokens, lock/temp/partial files,
or symlinks.

Never restore a project archive over a live project until its worker is stopped,
the staged restore has passed identity/checksum validation, and the incident or
change record authorizes the promotion.

### 7.1 Create and inspect

```text
python scripts/state_backup.py backup projects/<PROJECT_ID> backups/<PROJECT_ID>-<INCIDENT_ID>.zip
python scripts/state_backup.py inspect backups/<PROJECT_ID>-<INCIDENT_ID>.zip
```

The archive must be outside the project being walked. Retain the manifest,
operator, timestamp, and storage reference in the incident record—not the
secret-bearing environment.

### 7.2 Restore drill and promotion

1. Stop affected workers and freeze writes. Confirm the target root is a new,
   exact path, not the live project.
2. Restore to a disposable root and inspect the result:

   ```text
   python scripts/state_backup.py restore backups/<PROJECT_ID>-<INCIDENT_ID>.zip projects-restore-drill --project-id <PROJECT_ID>
   python scripts/state_backup.py inspect backups/<PROJECT_ID>-<INCIDENT_ID>.zip
   ```

3. Compare marker/work-order/run/checkpoint identity, manifest hashes, file
   sizes, SHA-256 values, and required project directories. Run the backup and
   identity contract tests if this is a release/recovery drill.
4. Only after the staged tree passes validation, use the explicit
   `--overwrite` restore against the live root. The tool preserves the previous
   tree under `.pre-restore-<id>-<nonce>`; do not delete that tree until the
   incident owner signs off.
5. Start the service, verify health/auth/scope, read the work order, and run a
   local/fake smoke. Confirm queued/running work resumes from the durable
   manifest-derived pointer and no paid operation is replayed without its
   idempotency key.

### 7.3 State migration

Always dry-run, back up, then migrate. Unknown versions fail closed:

```text
python scripts/state_backup.py migrate projects/<PROJECT_ID> --dry-run
python scripts/state_backup.py backup projects/<PROJECT_ID> backups/<PROJECT_ID>-pre-migration.zip
python scripts/state_backup.py migrate projects/<PROJECT_ID>
```

Every changed file is preserved under `history/migrations/<migration-id>/`.
Re-running current state is a validated `NOOP`; a migration that cannot
validate or roll back is an incident, not a partial success.

## 8. Secret rotation and privacy incident

1. Declare a security incident if a token, service-account file, private key,
   authorization header, signed media URL, or raw sensitive creative payload
   may have been exposed. Revoke the suspected secret at the provider/secret
   manager immediately; do not wait for a code fix.
2. Generate a replacement in the approved secret manager. Do not put it in a
   ticket, source file, Docker build argument, command line, or persistent
   `.env` on a shared host.
3. Update the runtime secret reference, restart the service through the normal
   deployment path, and verify:

   ```text
   curl.exe --fail-with-body -H "Authorization: Bearer <NEW_RUNTIME_TOKEN>" http://127.0.0.1:4750/api/health
   curl.exe -i -H "Authorization: Bearer <OLD_RUNTIME_TOKEN>" http://127.0.0.1:4750/api/health
   ```

   The old token must be rejected where rotation semantics require revocation;
   the new token must not appear in response bodies, logs, metrics, events,
   crash dumps, or backup manifests.
4. Scan retained logs/events/diagnostics for the old value and common secret
   patterns using the repository redaction tests. Restrict and preserve the
   evidence without copying the value into the incident record. If exposure is
   confirmed, rotate all related credentials and notify the affected provider
   and users according to policy.
5. Re-run the authenticated health, project authorization, and local/fake
   smoke checks. Close only after the old credential is revoked, the new one
   works, redaction is verified, and retention/deletion actions are recorded.

## 9. Incident evidence and closure

Every P0/P1 incident record must contain:

1. incident/change ID, severity, operator, start/end times, release ref/image
   digest, affected projects/pipelines/providers, and user impact;
2. redacted health, metrics, event, work-order, run-record, checkpoint, and
   render/QA evidence correlated by project/run/stage/attempt;
3. the exact stop, rollback, cancellation, restore, or rotation actions and
   their results;
4. spend/idempotency review for every provider attempt;
5. validation that no wrong/stale output was presented, or a quality-escape
   record if one was presented;
6. recovery verification, follow-up owner/due date, and the release decision.

Do not close a P0/P1 solely because the process is healthy. The durable state,
provider/runtime identity, visual/audio/voice/design correctness, security
boundary, and user communication must also be verified. Attach the evidence to
the relevant acceptance row (`RUNBOOK-*`, `REC-03`, `OBS-03`, or release gate).

## 10. Runbook verification commands

The documentation contract is checked offline with:

```text
python -m pytest tests/contracts/test_phase10_runbooks.py -q
python scripts/run_operations_drill.py --output pr10g-operations-drill.json
```

The runbook test checks that every required procedure has a concrete command,
stop condition, evidence requirement, and `PR-11G`/production lock reference.
The offline operations drill exercises fake-provider throttling/circuit
opening, duplicate claims, cancellation/restart, corrupt-artifact rejection,
and bearer-token rotation without network access or spend. It does not claim
that a deployment, live provider, or rollback has been executed; those are
separate Phase 10/11 drills.
