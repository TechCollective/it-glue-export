# it-glue-export

Back up an [IT Glue](https://www.itglue.com/) account with the official Exports API, verify the archive, store it, and build a single-file browsable index of what it contains.

It is a small Python package with one command, `itglue-export`, and two subcommands:

- `backup`: trigger an export, wait for it, download the ZIP, verify every member's CRC, and store it locally, in Google Drive, or in S3.
- `index`: turn an extracted export (or the ZIP itself) into one self-contained `index.html` you can open in a browser and search offline.

It runs unattended from cron and has been used weekly on exports of several hundred megabytes with thousands of documents and attachments.

## Requirements

- Linux or macOS
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.13+ and every dependency on first run)
- An IT Glue API key with **Password Access** enabled if you want passwords in the export

## Install

```bash
git clone https://github.com/TechCollective/it-glue-export.git
cd it-glue-export
cp .env.example .env    # then fill in the values described below
```

There is no separate install step. `uv run itglue-export ...` creates `.venv` and installs the package the first time it runs. Use `uv sync --extra gdrive` or `--extra s3` to install the optional storage backend dependencies up front; `run_backup.sh` does this for Google Drive on your behalf.

## Configuration

All settings live in `.env` (or a file you point at with `--env PATH`). Only `ITGLUE_API_KEY` is required for a local run.

| Variable | Required | Meaning |
| --- | --- | --- |
| `ITGLUE_API_KEY` | yes | Generate under **IT Glue > Admin > Settings > API Keys**. |
| `ITGLUE_API_ENDPOINT` | no | `https://api.itglue.com` (default, US) or `https://api.eu.itglue.com` (EU). |
| `ITGLUE_ZIP_PASSWORD` | no | Password IT Glue applies to the ZIP. Strongly recommended; the verifier and `index --password` use the same value. |
| `ITGLUE_ORG_ID` | no | Limit the export to one organization. Empty means the whole account. |
| `ITGLUE_INCLUDE_LOGS` | no | `true` to include the activity log. Default `false`. |
| `ITGLUE_INCLUDE_PASSWORDS` | no | `true` to include passwords in the export. Default `false`. |
| `GDRIVE_CREDENTIALS_FILE` | for Drive | Path to a Google service account JSON key. Default `service-account.json`. |
| `GDRIVE_FOLDER_ID` | for Drive | The Drive folder that receives backups. |
| `S3_BUCKET` | for S3 | Setting this selects the S3 backend instead of Google Drive. |
| `S3_PREFIX` | no | Key prefix inside the bucket, for example `itglue/`. |
| `LOCAL_TEMP_DIR` | no | Where the ZIP is downloaded. Default `./temp`. |

AWS credentials and region for the S3 backend come from the usual sources: `AWS_*` variables (which may also live in `.env`), a profile, or an instance role.

### Google Drive service account

To upload without user interaction:

1. In the [Google Cloud Console](https://console.cloud.google.com/), create or pick a project and enable the **Google Drive API**.
2. Under **IAM & Admin > Service Accounts**, create a service account and download a **JSON key**. Save it as `service-account.json` next to `.env` (or set `GDRIVE_CREDENTIALS_FILE`).
3. In Google Drive, share the destination folder with the service account's email address as an **Editor**, and put the folder's ID in `GDRIVE_FOLDER_ID`.

## Running a backup

```bash
./run_backup.sh            # Google Drive upload (the default)
./run_backup.sh --local    # keep the ZIP under LOCAL_TEMP_DIR, no upload
```

`run_backup.sh` is a thin wrapper around `uv run --extra gdrive itglue-export backup "$@"` that also creates the temp folder; call the command directly if you prefer.

Flags:

- `--env PATH`: use a `.env` file other than `./.env`.
- `--local`: store the ZIP locally and skip the upload. Google Drive settings are not required.
- `--simple`: deprecated no-op kept for old cron lines. The raw archive is always what gets stored.

What a run does:

1. Triggers an export. If IT Glue reports that an identical export is already available, the newest matching one is reused.
2. Polls every minute (up to two hours) until the download URL appears.
3. Downloads the ZIP to `LOCAL_TEMP_DIR` as `itglue-backup-YYYYMMDD-HHMMSS.zip`.
4. Verifies it: size against the server's `Content-Length`, ZIP structure, and a CRC check of every member using `ITGLUE_ZIP_PASSWORD`. A failed check stores nothing and exits non-zero. On an encrypted archive this step is CPU-bound and can take several minutes for a multi-hundred-megabyte export.
5. Stores it. With Google Drive the upload is resumable and the local copy is deleted afterwards. With `--local` the file stays where it was downloaded.

The S3 backend is different: it streams the export from IT Glue's download URL straight into the bucket as a multipart upload, with nothing written to local disk. Because there is no local copy, step 4 is skipped on that path and verification is left to whatever consumes the bucket. A failed upload aborts the multipart upload so no partial object is left behind.

The archive is never modified by any command.

Logs go to stdout and to `itglue_backup.log` in the working directory.

## Building a browsable index

```bash
uv run itglue-export index path/to/extracted-export
uv run itglue-export index itglue-backup-20260101-020000.zip --password "$ITGLUE_ZIP_PASSWORD"
```

The result is one `index.html` at the export root: organizations down the side, every asset type as a searchable table, documents and attachments linked relatively so they open from the same folder. Password-like columns are masked behind a click-to-reveal control. Everything is rendered client-side from an embedded payload; there is no server and nothing leaves your machine.

Given a ZIP, the command extracts it into a sibling folder named after the archive and builds the index there. The ZIP is only read.

Flags:

- `-o PATH`: write the index somewhere other than `<export_dir>/index.html`.
- `--password PW`: password of the ZIP, when extracting one.
- `--include-logs`: include `logs.csv`, the activity audit trail, which is usually the largest table and is skipped by default.

## Running weekly from cron

```cron
0 2 * * 0 /path/to/it-glue-export/run_backup.sh >> /path/to/it-glue-export/cron.log 2>&1
```

Cron runs with a minimal `PATH`. If `uv` lives in `~/.local/bin`, either add that to `PATH` at the top of the crontab or call `uv` by its full path inside `run_backup.sh`.

## Using it as a library

The pieces behind the CLI are importable for pipelines that need only part of the flow:

- `itglue_export.client.ITGlueClient`: trigger, reuse, poll, and download exports.
- `itglue_export.verify.verify_archive`: the read-only archive check.
- `itglue_export.storage`: the `LocalStorage`, `GoogleDriveStorage`, and streaming `S3Storage` backends.
- `itglue_export.index`: `build_payload` and `render` for the index.
- `itglue_export.status`: a small model and writer for a `status.json` inventory of stored backups (filename, creation date, size, whether it was verified), meant for monitoring.

## Development

```bash
uv sync --extra gdrive --extra s3
uv run pytest
```

Tests use fake fixture data under `tests/fixtures` and mocked HTTP and S3 clients; nothing in the test suite talks to IT Glue, Google, or AWS.

## License

MIT. See [LICENSE](LICENSE).
