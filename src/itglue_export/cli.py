import argparse
import logging
import sys
from pathlib import Path

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

    index = sub.add_parser("index", help="Build a single-file index.html from an extracted export.")
    index.add_argument("export_dir", nargs="?", default="temp", help="Root of the extracted IT Glue export (default: temp)")
    index.add_argument("-o", "--output", help="Output path (default: <export_dir>/index.html)")
    index.add_argument("--include-logs", action="store_true", help="Include logs.csv (activity audit trail, ~12k rows)")
    return parser


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("itglue_backup.log", encoding="utf-8")],
    )


def _backup(args: argparse.Namespace) -> int:
    _setup_logging()
    if args.simple:
        logger.warning("--simple is deprecated and has no effect: the raw archive is always what gets stored.")
    config = Configuration(env_path=args.env, local=args.local)
    try:
        BackupOrchestrator(config).run()
    except Exception:
        logger.exception("An error occurred during the backup pipeline execution:")
        return 1
    return 0


def _index(args: argparse.Namespace) -> int:
    from .index import build_payload, render

    export_dir = Path(args.export_dir).expanduser().resolve()
    if not export_dir.is_dir():
        print(f"error: {export_dir} is not a directory", file=sys.stderr)
        return 1
    if not list(export_dir.glob("*.csv")):
        print(f"error: no CSVs at {export_dir} -- is this an extracted export root?", file=sys.stderr)
        return 1

    print(f"Reading {export_dir}")
    payload = build_payload(export_dir, args.include_logs)
    out = Path(args.output) if args.output else export_dir / "index.html"
    out.write_text(render(payload), encoding="utf-8")

    size_mb = out.stat().st_size / 1048576
    print(f"\nWrote {out}")
    print(f"  {size_mb:.1f} MB  |  {len(payload['orgs'])} orgs  |  "
          f"{payload['totalRows']:,} records  |  {len(payload['order'])} asset types")
    return 0


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    code = _backup(args) if args.command == "backup" else _index(args)
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
