import argparse
import logging
import sys

from .config import Configuration
from .orchestrator import BackupOrchestrator

logger = logging.getLogger("itglue_export")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="itglue-export", description="Back up IT Glue with the Exports API.")
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup", help="Trigger an export, download the archive and store it.")
    backup.add_argument("--env", help="Path to a .env file (defaults to ./.env)")
    backup.add_argument("--local", action="store_true", help="Keep the archive under LOCAL_TEMP_DIR instead of uploading to Google Drive")
    backup.add_argument("--simple", action="store_true", help="Deprecated no-op: the raw archive is now always what gets stored")
    return parser


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("itglue_backup.log", encoding="utf-8")],
    )


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    _setup_logging()
    if args.simple:
        logger.warning("--simple is deprecated and has no effect: the raw archive is always what gets stored.")
    config = Configuration(env_path=args.env, local=args.local)
    try:
        BackupOrchestrator(config).run()
    except Exception:
        logger.exception("An error occurred during the backup pipeline execution:")
        sys.exit(1)


if __name__ == "__main__":
    main()
