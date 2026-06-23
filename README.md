# IT Glue Backup to Google Drive (Python Rewrite)

This repository contains a native Python rewrite of the legacy PowerShell script for exporting IT Glue data and uploading the resulting ZIP archives to Google Drive. It leverages the official IT Glue Exports API and runs on Linux.

## Features
- **IT Glue Exports API Integration**: Triggers a full account export (which includes documents and attachments), polls asynchronously for completion, and downloads the ZIP file.
- **Google Drive Upload**: Authenticates using a Google Cloud Service Account and performs a chunked/resumable upload of the backup file.
- **SOLID Architecture**: Built using OOP principles, with separated concerns for API interaction, file uploading, and orchestration.
- **Automatic Environment Setup**: The included `run_backup.sh` wrapper script automatically manages a Python virtual environment and installs dependencies.
- **Resource Cleanup**: Deletes the local temporary ZIP file after upload (even if the upload fails) to prevent disk space exhaustion.

---

## Installation & Setup

### 1. Clone & Prepare Directory
Make sure this directory contains:
- [itglue_backup.py](file:///home/jeff/Projects/Work%20Projects/IT%20GLue%20backup%20tests/itglue_backup.py)
- [requirements.txt](file:///home/jeff/Projects/Work%20Projects/IT%20GLue%20backup%20tests/requirements.txt)
- [run_backup.sh](file:///home/jeff/Projects/Work%20Projects/IT%20GLue%20backup%20tests/run_backup.sh)
- [README.md](file:///home/jeff/Projects/Work%20Projects/IT%20GLue%20backup%20tests/README.md)

### 2. Configure Environment Variables
Copy `.env.example` to `.env` and update the values:
```bash
cp .env.example .env
```
Edit `.env` with your actual credentials:
- `ITGLUE_API_KEY`: Generate a key under **IT Glue > Admin > Settings > API Keys**. Make sure **Password Access** is checked.
- `ITGLUE_API_ENDPOINT`: Set to `https://api.itglue.com` (US) or `https://api.eu.itglue.com` (EU).
- `ITGLUE_ZIP_PASSWORD`: (Recommended) A password to encrypt the resulting ZIP file.
- `GDRIVE_CREDENTIALS_FILE`: The path to your Google Cloud Service Account credentials JSON file.
- `GDRIVE_FOLDER_ID`: The ID of the Google Drive folder where backups should be uploaded.

### 3. Google Drive Service Account Setup
To upload to Google Drive without user interaction:
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new Project (or select an existing one).
3. Enable the **Google Drive API**.
4. Go to **IAM & Admin > Service Accounts** and create a new Service Account.
5. Generate and download a **JSON key** for this service account. Save it to this directory as `service-account.json` (or match the filename in `.env`).
6. Copy the service account's email address (e.g., `backup-sa@project-id.iam.gserviceaccount.com`).
7. Open Google Drive in your browser, go to your backup folder, and **Share** the folder with the service account email, granting it **Editor** permissions.

---

## Running the Backup

You can run the backup manually at any time by executing the runner script:
```bash
./run_backup.sh [options]
```

### Options
- `--simple`: Skip post-processing/HTML report generation and keep/upload the raw ZIP archive directly.
- `--local`: Save the final ZIP file locally (under `./temp` directory) and skip uploading it to Google Drive. (Google Drive environment variables are not validated or required when this option is used).

### Examples
- **Standard Run (HTML reports + Google Drive upload)**:
  ```bash
  ./run_backup.sh
  ```
- **Local Run with HTML reports**:
  ```bash
  ./run_backup.sh --local
  ```
- **Local Run (raw ZIP archive only)**:
  ```bash
  ./run_backup.sh --local --simple
  ```

This script will:
1. Initialize the Python virtual environment (`venv`) if it doesn't exist.
2. Install/upgrade packages from `requirements.txt`.
3. Create a `./temp` folder.
4. Execute `itglue_backup.py` with any forwarded arguments.
5. Save the processed ZIP locally (if `--local` is active) or upload it to Google Drive and delete the temporary ZIP.

---

## Automation (Weekly Cron Job)

To automate this backup weekly (for example, every Sunday at 2:00 AM), add the following line to your system's crontab:

```cron
0 2 * * 0 "/home/jeff/Projects/Work Projects/IT GLue backup tests/run_backup.sh" >> "/home/jeff/Projects/Work Projects/IT GLue backup tests/cron.log" 2>&1
```

To edit your crontab, run:
```bash
crontab -e
```
and paste the line above at the end of the file.
