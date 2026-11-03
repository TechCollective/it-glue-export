import os
from pathlib import Path

from dotenv import load_dotenv


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("true", "1", "yes")


class Configuration:
    """Load and validate settings from environment variables and CLI flags."""

    def __init__(self, env_path: str | None = None, local: bool = False):
        if env_path and os.path.exists(env_path):
            load_dotenv(env_path)
        else:
            load_dotenv()

        self.api_key = os.getenv("ITGLUE_API_KEY")
        self.api_endpoint = os.getenv("ITGLUE_API_ENDPOINT", "https://api.itglue.com").rstrip("/")
        self.zip_password = os.getenv("ITGLUE_ZIP_PASSWORD")

        org_id = os.getenv("ITGLUE_ORG_ID")
        self.org_id = int(org_id) if org_id and org_id.strip() else None

        self.include_logs = _flag("ITGLUE_INCLUDE_LOGS", "false")
        self.include_passwords = _flag("ITGLUE_INCLUDE_PASSWORDS", "false")

        self.gdrive_credentials_file = os.getenv("GDRIVE_CREDENTIALS_FILE", "service-account.json")
        self.gdrive_folder_id = os.getenv("GDRIVE_FOLDER_ID")

        self.local_temp_dir = Path(os.getenv("LOCAL_TEMP_DIR", "./temp"))
        self.local = local

    def validate(self) -> None:
        missing = []
        if not self.api_key:
            missing.append("ITGLUE_API_KEY")
        if not self.local:
            if not self.gdrive_folder_id:
                missing.append("GDRIVE_FOLDER_ID")
            if not os.path.exists(self.gdrive_credentials_file):
                missing.append(f"GDRIVE_CREDENTIALS_FILE ({self.gdrive_credentials_file} not found)")
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")
