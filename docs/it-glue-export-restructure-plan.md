# it-glue-export: Restructure Plan (Phase 2)

*2026-10-20; revised 2026-10-28 (wayfinder tickets #4 and #5). Goal: turn `TechCollective/it-glue-export` into a reusable, open-source package, restructured **in place**, that still runs on its own and that `itglue-vault` imports from a pinned tag. `itglue-vault` holds only TechCollective's AWS implementation.*

## Decisions this revision records

- **In place, not a new repo.** ADR-20261020-repo-split revision 3. The earlier "freeze and build `itglue-export-core`" plan is withdrawn; the `legacy-final` tag (pushed 2026-10-28) is the rollback point.
- **Public after audit.** The repo is opened under the MIT license once the publication audit (own ticket) passes. Nothing TechCollective-specific goes in.
- **One release.** `v1.0.0` carries the restructure *and* the behavior changes below; there is no "no behavior change" intermediate tag.
- **Default run stores the Raw Archive** (ticket #5). `html_report.py` is deleted, not ported. `--simple` is accepted as a deprecated no-op.
- Names: repo `TechCollective/it-glue-export`; distribution `it-glue-export`; import `itglue_export`; command `itglue-export` with `backup` and `index` subcommands.

## Current state (2026-10-28)

- `main` = `origin/main`, tagged `legacy-final`. It includes `generate_index.py` (1,269 lines, single-file index builder) and the `ITGLUE_INCLUDE_PASSWORDS` option (defaults to `true`).
- The weekly cron runs `run_backup.sh` from a checkout on one TI workstation and is healthy (about 18 minutes end to end).
- `.gitignore` has broken lines: `*.json` ignores all JSON, and an inline comment sits after `!requirements.txt`.
- On disk, ignored by git: `.env`, `service-account.json`, `temp/`, `venv/`, `itglue_backup.log`.

## Target layout

```
pyproject.toml              uv; name "it-glue-export", import "itglue_export"; requires-python >=3.13
                            extras: [gdrive], [s3]; console script itglue-export
LICENSE                     MIT
src/itglue_export/
  config.py                 Configuration (same env vars as today; ITGLUE_INCLUDE_PASSWORDS defaults to false)
  client.py                 ITGlueClient: trigger, reuse matching export, poll, download/stream
  storage/
    base.py                 Storage interface
    local.py                --local
    gdrive.py               Google Drive (standalone default)
    s3.py                   stream the presigned URL into S3 with a multipart upload
  verify.py                 archive verifier: full testzip, size and CRC checks; never modifies the archive
  index/
    payload.py              from generate_index.py: read the Extracted Export into a payload
    render.py               from generate_index.py: render the single-file index.html; every value HTML-escaped
  status.py                 status record model + writer (SDD §3.2); no CLI flag, used by itglue-vault
  orchestrator.py           BackupOrchestrator: trigger → poll → download → verify → store
  cli.py                    itglue-export backup [--env --local --simple(no-op)] | index <EXPORT_DIR | archive.zip> [--password] [--include-logs]
run_backup.sh               same usage; runs `uv run itglue-export backup "$@"`
tests/                      pytest, fake fixture data only
```

Removed from the previous layout: `html_report.py` (ticket #5), the `itglue_backup.py` and `generate_index.py` shims, `requirements.txt`.

## Steps

0. **Pin the cron (before anything else).** On the workstation that runs the cron: `git checkout legacy-final`. The cron keeps running frozen code until the AWS pipeline has produced two clean weekly backups; `main` is then free to change. Rollback at any point is "do nothing".
1. **Branch `restructure`.** Fix `.gitignore` (explicit credential names instead of `*.json`; add `*.zip`, `temp/`, `exports/`). Add `pyproject.toml`, `LICENSE`.
2. **Move code into modules.** Cut and paste with the behavior changes above and nothing else. Delete `HTMLReportGenerator`. Rewrite `run_backup.sh` to call the new command.
3. **New modules.** `storage/s3.py`, `verify.py`, `status.py`, the `index` command accepting a directory or a ZIP (a ZIP is extracted to a sibling folder; the ZIP is never modified).
4. **Tests.**
   - Client against mocked HTTP: trigger, 422 reuse, poll timeout, download
   - Orchestrator flow with a fake storage backend, including a verify failure that stores nothing
   - Verifier against a good ZIP and a corrupted one
   - Index builder against a small fake CSV fixture
   - CLI flags, including `--simple` as a no-op
5. **Verify against the real thing.** `./run_backup.sh --local` and compare the ZIP size with recent runs; one Drive run and confirm a new file appears. Build an index from the result and open it.
6. **Merge and tag `v1.0.0`.** Pin it in `itglue-vault`'s `pyproject.toml`.
7. **Publication audit** (separate ticket): history scan for secrets, remove internal paths and names, README for strangers; then make the repo public.

## Compatibility promise (stays true in every release)

- `./run_backup.sh` with `--env`, `--local` and `--simple` keeps running; `--simple` now describes the default and logs a deprecation line.
- Same `.env` variables. New ones are optional.
- Google Drive stays the standalone default upload.
- Backup filename format: `itglue-backup-YYYYMMDD-HHMMSS.zip`.
- The Raw Archive is never modified by any command.
