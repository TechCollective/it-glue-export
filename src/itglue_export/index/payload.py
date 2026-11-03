from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import quote
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# CSVs skipped entirely. logs.csv is the IT Glue activity audit trail -- 12k+
# rows and over half the total payload, and it is not documentation. Pass
# --include-logs to fold it back in.
SKIP_TABLES = {"logs"}

# Column headers whose values are masked behind a click-to-reveal control.
SECRET_PATTERN = re.compile(
    r"passphrase|shared[ _]?secret|\bsecret\b|\bpsk\b|api[ _]?key|"
    r"\btoken\b|credential|\bpassword\b",
    re.IGNORECASE,
)

# Headers that MATCH the pattern above but hold references, not values --
# e.g. server-overview "Passwords" holds password *names* ("Synology NAS
# Admin") and site-summary "Password Manager" holds "IT Glue". Add to this set
# to un-mask a column; remove from it to mask one.
NOT_SECRET = {"passwords", "password manager"}

# Columns rendered as long-form text rather than a table cell.
LONGTEXT_PATTERN = re.compile(r"notes|description|quick[ _]?notes|alert", re.IGNORECASE)

CSV_FIELD_LIMIT = 10 * 1024 * 1024


TAG_RE = re.compile(r"<[^>]+>")
BREAK_RE = re.compile(r"<\s*(br|/p|/div|/li|/tr)\s*/?\s*>", re.IGNORECASE)
WS_RE = re.compile(r"[ \t]+")
NL_RE = re.compile(r"\n{3,}")


def clean_cell(v: str) -> str:
    """Make one CSV cell readable.

    IT Glue exports rich-text fields as raw HTML and some relational fields as
    JSON. Both are unreadable in a table, so collapse them to plain text here
    rather than in the browser -- it keeps the payload smaller and means the
    rendered page never has to trust markup from the export.
    """
    if not v:
        return v

    stripped = v.lstrip()
    if stripped[:1] in "[{":
        flat = flatten_json(stripped)
        if flat is not None:
            return flat

    if "<" in v and TAG_RE.search(v):
        v = BREAK_RE.sub("\n", v)
        v = TAG_RE.sub("", v)
        v = unescape(v)
        v = WS_RE.sub(" ", v)
        v = NL_RE.sub("\n\n", v)
        return v.strip()

    return v


def flatten_json(v: str) -> str | None:
    """Render an embedded JSON value as compact, searchable text."""
    try:
        data = json.loads(v)
    except (ValueError, TypeError):
        return None

    def one(item):
        if isinstance(item, dict):
            bits = [f"{k}: {val}" for k, val in item.items()
                    if val not in (None, "", False, [], {})]
            return " \u00b7 ".join(bits)
        return "" if item is None else str(item)

    if isinstance(data, list):
        parts = [p for p in (one(i) for i in data) if p]
        return "\n".join(parts)
    if isinstance(data, dict):
        return one(data)
    return None


def is_secret(header: str) -> bool:
    if header.strip().lower() in NOT_SECRET:
        return False
    return bool(SECRET_PATTERN.search(header))


def nice_title(stem: str) -> str:
    """server-overview -> Server Overview; ssl-certificates -> SSL Certificates"""
    words = stem.replace("-", " ").replace("_", " ").split()
    acronyms = {"ssl", "dns", "lan", "wan", "vpn", "ldap", "pbx", "ad", "bcdr", "radius", "wifi"}
    out = []
    for w in words:
        out.append(w.upper() if w.lower() in acronyms else w.capitalize())
    return " ".join(out)


def find_org_column(headers: list[str]) -> int | None:
    for i, h in enumerate(headers):
        if h.strip().lower() in ("organization", "organisation", "org", "organization_name"):
            return i
    return None


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh)
        try:
            headers = next(reader)
        except StopIteration:
            return [], []
        rows = [r for r in reader if any(c.strip() for c in r)]
    width = len(headers)
    norm = [[clean_cell(c) for c in (r + [""] * width)[:width]] for r in rows]
    return headers, norm


def build_document_map(export_dir: Path) -> dict[str, str]:
    """locator (DOC-3577506-4551208) -> relative path of its .html"""
    docs_dir = export_dir / "documents"
    mapping: dict[str, str] = {}
    if not docs_dir.is_dir():
        return mapping
    locator_re = re.compile(r"(DOC-\d+-\d+)")
    for path in docs_dir.rglob("*"):
        if not path.is_file():
            continue
        # The locator appears in the containing folder name, or the file name
        # itself for single-file documents.
        hay = f"{path.parent.name} {path.name}"
        m = locator_re.search(hay)
        if not m:
            continue
        loc = m.group(1)
        rel = path.relative_to(export_dir).as_posix()
        # Prefer the .html rendering of the document over a sibling attachment.
        if loc not in mapping or (path.suffix.lower() == ".html" and not mapping[loc].lower().endswith(".html")):
            mapping[loc] = rel
    return mapping


# Defence in depth: these documents are IT Glue's own rich text and contain no
# scripts today, but they get injected into the page as markup, so strip
# anything executable before it ever reaches the payload.
SCRIPTISH_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed|base|link|meta|form)\b[^>]*>.*?"
    r"<\s*/\s*\1\s*>|<\s*(script|style|iframe|object|embed|base|link|meta|form)\b[^>]*/?>",
    re.IGNORECASE | re.DOTALL,
)
HANDLER_RE = re.compile(r"""\son\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)
JS_URL_RE = re.compile(r"""((?:href|src)\s*=\s*)(["']?)\s*javascript:[^"'>\s]*\2""", re.IGNORECASE)
# Match each quote style separately: a single [^"']+ class truncates a
# double-quoted URL at the first apostrophe (e.g. "documents/C4's own .../x.png").
ASSET_RE = re.compile(
    r"""((?:src|href)\s*=\s*)(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE
)
ABSOLUTE_PREFIXES = ("http:", "https:", "mailto:", "tel:", "data:", "//", "#", "/")


def sanitize_document(html: str, doc_dir: str) -> str:
    """Make one document fragment safe to inline, and fix its asset paths.

    IT Glue writes image/file references relative to the document's own folder.
    Once the fragment is inlined into index.html at the export root those paths
    would resolve one level too high, so rewrite them to sit under doc_dir.
    """
    html = SCRIPTISH_RE.sub("", html)
    html = HANDLER_RE.sub("", html)
    html = JS_URL_RE.sub(r"\1\2#\2", html)

    # Percent-encode the folder prefix. Document folders are named from document
    # titles, so they contain spaces, apostrophes and ampersands -- dropped in raw
    # they terminate the attribute early and the browser sees a truncated path.
    prefix = "/".join(quote(seg, safe="") for seg in doc_dir.strip("/").split("/")) + "/"

    def fix(m):
        dq, sq = m.group(2), m.group(3)
        url = dq if dq is not None else sq
        if not url or url.startswith(ABSOLUTE_PREFIXES):
            return m.group(0)
        clean = url[2:] if url.startswith("./") else url
        # Always re-emit double-quoted, so an apostrophe in the path is inert.
        return f'{m.group(1)}"{prefix}{clean.replace(chr(34), "%22")}"' 

    return ASSET_RE.sub(fix, html).strip()


def build_document_bodies(export_dir: Path, doc_map: dict[str, str]) -> dict[str, str]:
    """locator -> inlined, sanitized document HTML (only for .html renderings)."""
    bodies: dict[str, str] = {}
    for loc, rel in doc_map.items():
        path = export_dir / rel
        if path.suffix.lower() != ".html" or not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        body = sanitize_document(raw, str(Path(rel).parent.as_posix()))
        if body:
            bodies[loc] = body
    return bodies


def build_attachment_map(export_dir: Path) -> dict[str, dict[str, list[list[str]]]]:
    """table -> record id -> [[filename, relative path], ...]"""
    att_dir = export_dir / "attachments"
    mapping: dict[str, dict[str, list[list[str]]]] = {}
    if not att_dir.is_dir():
        return mapping
    for table_dir in sorted(att_dir.iterdir()):
        if not table_dir.is_dir():
            continue
        table = table_dir.name
        for rec_dir in sorted(table_dir.iterdir()):
            if not rec_dir.is_dir():
                continue
            files = [
                [f.name, f.relative_to(export_dir).as_posix()]
                for f in sorted(rec_dir.rglob("*"))
                if f.is_file()
            ]
            if files:
                mapping.setdefault(table, {})[rec_dir.name] = files
    return mapping


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

# Display order for asset types; anything not listed is appended alphabetically.
PREFERRED_ORDER = [
    "site-summary", "server-overview", "configurations", "applications", "backup",
    "active-directory", "virtualization", "lan", "internet-wan", "wireless", "vpn",
    "dns", "domains", "email", "ssl-certificates", "licensing", "printing",
    "file-sharing", "cloud-hosted", "cloud-storage", "remote-access",
    "radius-servers", "ldap", "security", "voice-pbx", "website",
    "datto-bcdr-health", "account-management", "vendors", "locations", "contacts",
    "documents", "organizations",
]


def build_payload(export_dir: Path, include_logs: bool) -> dict:
    csv.field_size_limit(CSV_FIELD_LIMIT)

    skip = set() if include_logs else set(SKIP_TABLES)
    tables: dict[str, dict] = {}

    for path in sorted(export_dir.glob("*.csv")):
        stem = path.stem
        if stem in skip:
            print(f"  skip   {stem}.csv (use --include-logs to include)")
            continue
        headers, rows = read_csv(path)
        if not headers or not rows:
            print(f"  empty  {stem}.csv")
            continue

        lower = [h.strip().lower() for h in headers]
        secret_flags = [is_secret(h) for h in headers]
        n_secret = sum(secret_flags)

        tables[stem] = {
            "title": nice_title(stem),
            "cols": headers,
            "secret": secret_flags,
            "long": [bool(LONGTEXT_PATTERN.search(h)) for h in headers],
            "orgIdx": find_org_column(headers),
            "idIdx": lower.index("id") if "id" in lower else None,
            "nameIdx": lower.index("name") if "name" in lower else None,
            "locIdx": lower.index("locator") if "locator" in lower else None,
            "rows": rows,
        }
        flag = f"  [{n_secret} masked col{'' if n_secret == 1 else 's'}]" if n_secret else ""
        print(f"  ok     {stem}.csv  {len(rows):>6} rows{flag}")

    # --- organizations ---
    org_meta: dict[str, dict] = {}
    if "organizations" in tables:
        t = tables["organizations"]
        name_i = t["nameIdx"] if t["nameIdx"] is not None else 1
        for row in t["rows"]:
            org_meta[row[name_i]] = {
                h.strip().lower().replace(" ", "_"): row[i]
                for i, h in enumerate(t["cols"])
            }

    totals: dict[str, int] = {}
    for key, t in tables.items():
        # The organizations table is the org itself, not one of its records --
        # counting it would make every org read one higher than its rail sums to.
        if t["orgIdx"] is None or key == "organizations":
            continue
        for row in t["rows"]:
            name = row[t["orgIdx"]] or "(unassigned)"
            totals[name] = totals.get(name, 0) + 1

    all_names = sorted(set(org_meta) | set(totals), key=lambda s: s.lower())
    orgs = [
        {"name": n, "meta": org_meta.get(n, {}), "total": totals.get(n, 0)}
        for n in all_names
    ]

    order = [k for k in PREFERRED_ORDER if k in tables]
    order += sorted(k for k in tables if k not in order)

    docs = build_document_map(export_dir)
    bodies = build_document_bodies(export_dir, docs)
    atts = build_attachment_map(export_dir)
    n_att = sum(len(v) for v in atts.values())
    body_mb = sum(len(v) for v in bodies.values()) / 1048576
    print(f"  linked {len(docs)} documents, {n_att} records with attachments")
    print(f"  inlined {len(bodies)} document bodies ({body_mb:.1f} MB)")

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "orgs": orgs,
        "tables": tables,
        "order": order,
        "docs": docs,
        "bodies": bodies,
        "atts": atts,
        "totalRows": sum(len(t["rows"]) for t in tables.values()),
    }

