#!/usr/bin/env python3
"""
Single-file browsable index for an extracted IT Glue export.

Reads the root-level CSVs of an extracted export and emits ONE self-contained
index.html at the export root. The file is designed to sit at the export root
so that relative links into documents/ and attachments/ resolve.

The pristine export ZIP is never touched by this script -- it only reads an
already-extracted tree and writes a single new file into it.

Usage:
    python3 generate_index.py [EXPORT_DIR] [-o OUTPUT] [--include-logs]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
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
# Page template
# ---------------------------------------------------------------------------

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IT Glue Export &mdash; __GENERATED__</title>
<style>
:root {
  --bg: #f6f7f9; --panel: #ffffff; --ink: #1c2024; --muted: #656d76;
  --line: #d8dde3; --accent: #2b6cb0; --accent-soft: #e8f0fa;
  --warn: #9a3412; --warn-soft: #fef0e7; --shadow: 0 1px 2px rgba(16,24,40,.06);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a; --panel: #1b1f23; --ink: #e6e9ec; --muted: #8b949e;
    --line: #2d333b; --accent: #6aa6e8; --accent-soft: #1d2b3a;
    --warn: #f0a878; --warn-soft: #3a2415; --shadow: none;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 13px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
button { font: inherit; }

/* ---- top bar ---- */
header {
  position: sticky; top: 0; z-index: 30; display: flex; align-items: center; gap: 18px;
  padding: 9px 20px; background: var(--panel); border-bottom: 1px solid var(--line);
}
.brand {
  font-weight: 650; font-size: 14px; white-space: nowrap; color: var(--ink);
  cursor: pointer; background: none; border: 0; padding: 0;
}
.topnav { display: flex; gap: 2px; }
.topnav button {
  padding: 5px 12px; border: 0; border-radius: 6px; background: none;
  color: var(--muted); cursor: pointer; font-size: 13px;
}
.topnav button:hover { background: var(--bg); color: var(--ink); }
.topnav button.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
#global { flex: 1; max-width: 520px; margin-left: auto; }
input[type=search], input[type=text] {
  width: 100%; padding: 7px 10px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--bg); color: var(--ink); font: inherit;
}
input:focus { outline: 2px solid var(--accent-soft); border-color: var(--accent); }
.counts { color: var(--muted); font-size: 12px; white-space: nowrap; }

/* ---- breadcrumbs ---- */
.crumbs {
  position: sticky; top: 48px; z-index: 25; background: var(--bg);
  border-bottom: 1px solid var(--line); padding: 8px 24px;
  font-size: 12px; color: var(--muted);
}
.crumbs span.sep { margin: 0 7px; opacity: .6; }
.crumbs .here { color: var(--ink); font-weight: 600; }

/* ---- page shell ---- */
.page { max-width: 1680px; margin: 0 auto; padding: 18px 24px 80px; }
.split { display: flex; gap: 20px; align-items: flex-start; }
.col-main { flex: 1; min-width: 0; }
h1 { font-size: 21px; margin: 0 0 4px; }
h2 { font-size: 14px; margin: 0 0 10px; }
.sub-line { color: var(--muted); margin-bottom: 18px; font-size: 12px; }
.pill {
  display: inline-block; padding: 1px 8px; border-radius: 99px; font-size: 11px;
  background: var(--accent-soft); color: var(--accent); margin-left: 8px;
  font-weight: 600; vertical-align: 3px;
}
.pill.grey { background: var(--bg); color: var(--muted); border: 1px solid var(--line); }

/* ---- cards ---- */
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  box-shadow: var(--shadow); margin-bottom: 14px;
}
.card > h2 {
  padding: 10px 14px; margin: 0; border-bottom: 1px solid var(--line);
  font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted);
}
.card .body { padding: 14px; }
.dl { display: grid; grid-template-columns: minmax(130px, 200px) 1fr; gap: 9px 18px; }
.dl dt { color: var(--muted); font-size: 12px; }
.dl dd { margin: 0; white-space: pre-wrap; overflow-wrap: break-word; }
.note {
  background: var(--warn-soft); color: var(--warn); border: 1px solid currentColor;
  border-radius: 6px; padding: 8px 12px; margin-bottom: 14px; font-size: 12px;
}
.note.info { background: var(--accent-soft); color: var(--accent); }
.note b { font-weight: 650; }

/* ---- right rail ---- */
.rail {
  width: 252px; flex: none; position: sticky; top: 100px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  box-shadow: var(--shadow); overflow: hidden; max-height: calc(100vh - 124px);
  display: flex; flex-direction: column;
}
.rail h2 {
  padding: 10px 14px; margin: 0; border-bottom: 1px solid var(--line);
  font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted);
}
.rail .items { overflow-y: auto; padding: 4px 0 8px; }
.rail button {
  display: flex; justify-content: space-between; gap: 10px; width: 100%;
  text-align: left; padding: 5px 14px; border: 0; background: none;
  color: var(--ink); cursor: pointer; line-height: 1.4;
}
.rail button:hover { background: var(--bg); }
.rail button.on {
  background: var(--accent-soft); color: var(--accent); font-weight: 600;
  box-shadow: inset 3px 0 0 var(--accent);
}
.rail button.zero { color: var(--muted); cursor: default; }
.rail button.zero:hover { background: none; }
.rail .n { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 12px; }
.rail button.on .n { color: var(--accent); }
.rail .divider { border-top: 1px solid var(--line); margin: 6px 0; }

/* ---- tables ---- */
.scroll { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px;
  background: var(--panel); box-shadow: var(--shadow); }
.scroll table { border-collapse: collapse; width: auto; min-width: 100%; font-size: 12px; }
.scroll th, .scroll td {
  border-bottom: 1px solid var(--line); padding: 6px 11px; text-align: left;
  vertical-align: top; white-space: nowrap;
}
.scroll th {
  background: var(--panel); font-weight: 600; position: sticky; top: 0; z-index: 1;
  box-shadow: inset 0 -1px 0 var(--line); color: var(--muted);
  font-size: 11px; text-transform: uppercase; letter-spacing: .03em;
}
.scroll tbody tr:last-child td { border-bottom: 0; }
.scroll tbody tr:hover { background: var(--accent-soft); }
td.empty { color: var(--muted); }
td.longcell {
  white-space: pre-wrap; min-width: 220px; max-width: 420px; overflow-wrap: break-word;
}
/* Rich-text notes and flattened JSON can run to dozens of lines; clamp them so
   one verbose record can't make a row 400px tall. Click to expand. */
.clamp { max-height: 3.6em; overflow: hidden; position: relative; }
.clamp.more { cursor: pointer; padding-bottom: 1.2em; }
.clamp.more:not(.open)::after {
  content: "\2026 more"; position: absolute; inset: auto 0 0 0; text-align: right;
  font-size: 11px; font-weight: 600; color: var(--accent); padding-top: 10px;
  background: linear-gradient(to bottom, transparent, var(--panel) 60%);
}
.clamp.open { max-height: none; }
td.matched, th.matched {
  white-space: normal; min-width: 210px; max-width: 330px; background: var(--bg);
  border-right: 1px solid var(--line); position: sticky; left: 0; z-index: 1;
}
td.matched b { display: block; font-size: 10px; text-transform: uppercase;
  letter-spacing: .03em; color: var(--muted); font-weight: 600; }
td.matched .snip { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; }
.secret {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; cursor: pointer;
  background: var(--warn-soft); color: var(--warn); border: 1px solid currentColor;
  border-radius: 4px; padding: 1px 6px; font-size: 11px;
}
.secret:hover { text-decoration: underline; }
.att { color: var(--muted); font-size: 11px; }
.att a { margin-right: 8px; white-space: nowrap; }
.orgname { font-weight: 600; }

/* ---- inlined IT Glue document ---- */
.docbody {
  padding: 20px 22px; overflow-x: auto; font-size: 13px; line-height: 1.6;
  color: var(--ink);
}
.docbody img { max-width: 100%; height: auto; }
.docbody table {
  border-collapse: collapse; max-width: 100%; height: auto !important;
  margin: 10px 0; font-size: 13px;
}
.docbody table, .docbody td, .docbody th { border-color: var(--line) !important; }
.docbody td, .docbody th {
  padding: 6px 9px; vertical-align: top; text-align: left;
  white-space: normal; overflow-wrap: break-word;
}
.docbody th { background: var(--bg); font-weight: 600; }
/* Exported docs carry hard-coded widths, colours and fonts from Word; neutralise
   the ones that break the layout or vanish in dark mode. */
.docbody [width] { width: auto !important; }
.docbody [style*="background"] { background-color: transparent !important; }
.docbody font, .docbody span, .docbody p, .docbody div, .docbody td {
  color: inherit !important; font-family: inherit !important;
}
.docbody p { margin: 0 0 9px; }
.docbody h1, .docbody h2, .docbody h3 { margin: 18px 0 8px; line-height: 1.3; }
.docbody h1 { font-size: 19px; } .docbody h2 { font-size: 16px; text-transform: none;
  letter-spacing: 0; color: var(--ink); padding: 0; border: 0; }
.docbody h3 { font-size: 14px; }
.docbody ul, .docbody ol { margin: 0 0 10px; padding-left: 24px; }
.docbody pre, .docbody code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;
  background: var(--bg); border-radius: 4px;
}
.docbody pre { padding: 10px; overflow-x: auto; white-space: pre-wrap; }
.docbody code { padding: 1px 4px; }
.docbody a { color: var(--accent); }
.docbody hr { border: 0; border-top: 1px solid var(--line); margin: 16px 0; }
.docmeta { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; color: var(--muted); }
.docmeta b { color: var(--ink); font-weight: 600; }

/* ---- pagination ---- */
.pager {
  display: flex; align-items: center; gap: 10px; padding: 11px 2px;
  font-size: 12px; color: var(--muted); flex-wrap: wrap;
}
.pager button {
  padding: 4px 11px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--panel); color: var(--ink); cursor: pointer;
}
.pager button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.pager button:disabled { opacity: .4; cursor: default; }
.pager .range { font-variant-numeric: tabular-nums; }
.pager .spacer { flex: 1; }
.pager select {
  padding: 4px 6px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--panel); color: var(--ink); font: inherit; font-size: 12px;
}
.toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
.toolbar input { max-width: 340px; }

.empty-state { color: var(--muted); padding: 44px 0; text-align: center; }
.hit-org { font-weight: 650; margin: 20px 0 4px; font-size: 13px; }
.hit-tbl { color: var(--muted); font-size: 11px; text-transform: uppercase;
  letter-spacing: .03em; margin: 12px 0 4px; }
mark { background: #ffe9a8; color: #3a2d00; padding: 0 1px; border-radius: 2px; }
@media (prefers-color-scheme: dark) { mark { background: #6b5415; color: #ffe9a8; } }
@media (max-width: 1000px) {
  .split { flex-direction: column-reverse; }
  .rail { width: 100%; position: static; max-height: none; }
}
@media (max-width: 700px) {
  header { flex-wrap: wrap; gap: 10px; }
  #global { max-width: none; order: 3; flex-basis: 100%; margin-left: 0; }
  .page, .crumbs { padding-left: 14px; padding-right: 14px; }
}
</style>
</head>
<body>
<header>
  <button class="brand" id="home">IT&nbsp;Glue Export</button>
  <nav class="topnav">
    <button id="nav-orgs">Organizations</button>
    <button id="nav-types">Asset types</button>
  </nav>
  <div id="global"><input type="search" id="q" placeholder="Search everything&hellip;" autocomplete="off"></div>
  <div class="counts" id="counts"></div>
</header>
<div class="crumbs" id="crumbs"></div>
<div class="page" id="page"></div>
<script id="payload" type="application/json">__DATA__</script>
<script>
__SCRIPT__
</script>
</body>
</html>
"""


SCRIPT = r"""
'use strict';
var D = JSON.parse(document.getElementById('payload').textContent);
var PAGE = document.getElementById('page');
var CRUMBS = document.getElementById('crumbs');
var Q = document.getElementById('q');

/* The whole export browsed as if it were one organization. */
var ALL = '*';
var ALL_LABEL = 'All Organizations';

var state = { view: 'orgs', org: null, table: null, page: 1, per: 50, filter: '' };
var PER_OPTIONS = [25, 50, 100, 250];

/* ---------- index building ---------- */
var byOrg = {};      /* table -> org -> [rowIdx] */
var haystack = {};   /* table -> [lowercased row text] */
var orgByName = {};
var docRow = {};   /* locator -> row index in the documents table */

(function buildIndex() {
  Object.keys(D.tables).forEach(function (t) {
    var tbl = D.tables[t], hs = new Array(tbl.rows.length), map = {};
    for (var i = 0; i < tbl.rows.length; i++) {
      var row = tbl.rows[i];
      hs[i] = row.join(' \u0001 ').toLowerCase();
      if (tbl.orgIdx !== null) {
        var o = row[tbl.orgIdx] || '(unassigned)';
        (map[o] || (map[o] = [])).push(i);
      }
    }
    haystack[t] = hs;
    byOrg[t] = map;
  });
  D.orgs.forEach(function (o) { orgByName[o.name] = o; });

  var dt = D.tables['documents'];
  if (dt && dt.locIdx !== null) {
    for (var r = 0; r < dt.rows.length; r++) docRow[dt.rows[r][dt.locIdx]] = r;
  }
})();

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
function attr(s) { return esc(s).replace(/'/g, '&#39;'); }
function num(n) { return n.toLocaleString(); }

function rowsFor(tkey, org) {
  if (org === ALL) {
    var n = D.tables[tkey].rows.length, a = new Array(n);
    for (var i = 0; i < n; i++) a[i] = i;
    return a;
  }
  return (byOrg[tkey] || {})[org] || [];
}
function countFor(tkey, org) {
  return org === ALL ? D.tables[tkey].rows.length : ((byOrg[tkey] || {})[org] || []).length;
}

/* ---------- hash routing ---------- */
function go() {
  location.hash = [].slice.call(arguments).map(encodeURIComponent).join('/');
}
function hashParts() {
  var h = location.hash.replace(/^#/, '');
  if (!h) return [];
  return h.split('/').map(function (p) {
    try { return decodeURIComponent(p); } catch (e) { return p; }
  });
}

/* ---------- shared cell / table rendering ---------- */
function hl(v, term) {
  var e = esc(v);
  if (!term) return e;
  try {
    var re = new RegExp('(' + term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig');
    return e.replace(re, '<mark>$1</mark>');
  } catch (x) { return e; }
}

function cellHtml(tkey, tbl, row, c, term) {
  var v = row[c];
  if (v == null || v === '') return '<td class="empty">&mdash;</td>';

  if (tbl.secret[c]) {
    return '<td><span class="secret" data-reveal="' + attr(v) + '" title="Click to reveal">'
      + '•••••• reveal</span></td>';
  }
  /* documents table: open the document inline rather than navigating away */
  if (tkey === 'documents' && c === tbl.nameIdx && tbl.locIdx !== null) {
    var loc = row[tbl.locIdx];
    if (D.bodies && D.bodies[loc]) {
      return '<td><a href="#doc/' + encodeURIComponent(loc) + '">' + hl(v, term) + '</a></td>';
    }
    var href = D.docs[loc];
    return '<td>' + (href
      ? '<a href="' + attr(href) + '">' + hl(v, term) + '</a>'
        + ' <span class="att">(file)</span>'
      : hl(v, term) + ' <span class="att" title="Not present in this export">(no file)</span>')
      + '</td>';
  }
  /* organization column links back to that org */
  if (c === tbl.orgIdx) {
    return '<td><a class="orgname" href="#org/' + encodeURIComponent(v) + '">'
      + hl(v, term) + '</a></td>';
  }

  var multi = v.length > 60 || v.indexOf('\n') !== -1;
  if (!multi) return '<td>' + hl(v, term) + '</td>';
  return '<td class="longcell"><div class="clamp">' + hl(v, term) + '</div></td>';
}

/* Answer "why did this row match?" -- the hit is often in a column that sits
   far off the right edge of a wide table. Never echoes a masked value. */
function matchCell(tbl, row, term) {
  var t = term.toLowerCase();
  for (var c = 0; c < row.length; c++) {
    var v = row[c];
    if (!v) continue;
    var i = v.toLowerCase().indexOf(t);
    if (i === -1) continue;
    if (tbl.secret[c]) {
      return '<td class="matched"><b>' + esc(tbl.cols[c]) + '</b>'
        + '<span class="att">matched a masked value</span></td>';
    }
    var a = Math.max(0, i - 28), b = Math.min(v.length, i + term.length + 28);
    var snip = (a > 0 ? '…' : '') + v.slice(a, b).replace(/\s+/g, ' ')
      + (b < v.length ? '…' : '');
    return '<td class="matched"><b>' + esc(tbl.cols[c]) + '</b>'
      + '<span class="snip">' + hl(snip, term) + '</span></td>';
  }
  return '<td class="matched empty">&mdash;</td>';
}

function attachHtml(tkey, tbl, row) {
  if (tbl.idIdx === null) return '';
  var m = D.atts[tkey];
  if (!m) return '';
  var files = m[row[tbl.idIdx]];
  if (!files || !files.length) return '';
  return '<td class="att">' + files.map(function (f) {
    return '<a href="' + attr(f[1]) + '">' + esc(f[0]) + '</a>';
  }).join('') + '</td>';
}

function buildTable(tkey, idxs, term, hideOrg) {
  var tbl = D.tables[tkey];
  var hasAtt = !!D.atts[tkey];
  var cols = [];
  for (var c = 0; c < tbl.cols.length; c++) {
    if (hideOrg && c === tbl.orgIdx) continue;
    cols.push(c);
  }
  var h = '<div class="scroll"><table><thead><tr>';
  if (term) h += '<th class="matched">Matched in</th>';
  cols.forEach(function (c) { h += '<th>' + esc(tbl.cols[c]) + '</th>'; });
  if (hasAtt) h += '<th>Files</th>';
  h += '</tr></thead><tbody>';
  for (var i = 0; i < idxs.length; i++) {
    var row = tbl.rows[idxs[i]];
    h += '<tr>';
    if (term) h += matchCell(tbl, row, term);
    cols.forEach(function (c) { h += cellHtml(tkey, tbl, row, c, term); });
    if (hasAtt) h += (attachHtml(tkey, tbl, row) || '<td class="att"></td>');
    h += '</tr>';
  }
  return h + '</tbody></table></div>';
}

/* Flag only the cells that actually overflow, so the "more" affordance is
   honest. Two passes: measure everything, then write classes, to avoid
   forcing a reflow on every single cell. */
function markClamped() {
  var els = PAGE.querySelectorAll('.clamp'), over = [], i;
  for (i = 0; i < els.length; i++) {
    if (els[i].scrollHeight > els[i].clientHeight + 4) over.push(els[i]);
  }
  for (i = 0; i < over.length; i++) over[i].classList.add('more');
}

/* ---------- pagination ---------- */
function pageSlice(arr) {
  if (state.per === 0) return arr;
  var start = (state.page - 1) * state.per;
  return arr.slice(start, start + state.per);
}

function pagerHtml(total) {
  if (state.per === 0) return '';
  var pages = Math.max(1, Math.ceil(total / state.per));
  if (total <= PER_OPTIONS[0] && pages === 1) return '';
  var from = total ? (state.page - 1) * state.per + 1 : 0;
  var to = Math.min(total, state.page * state.per);
  var opts = PER_OPTIONS.map(function (n) {
    return '<option value="' + n + '"' + (n === state.per ? ' selected' : '') + '>' + n + '</option>';
  }).join('');
  return '<div class="pager">'
    + '<button data-pg="first"' + (state.page <= 1 ? ' disabled' : '') + '>&laquo; First</button>'
    + '<button data-pg="prev"' + (state.page <= 1 ? ' disabled' : '') + '>&lsaquo; Prev</button>'
    + '<span class="range">' + num(from) + '&ndash;' + num(to) + ' of ' + num(total) + '</span>'
    + '<button data-pg="next"' + (state.page >= pages ? ' disabled' : '') + '>Next &rsaquo;</button>'
    + '<button data-pg="last"' + (state.page >= pages ? ' disabled' : '') + '>Last &raquo;</button>'
    + '<span class="spacer"></span>'
    + '<span>Page ' + num(state.page) + ' of ' + num(pages) + '</span>'
    + '<select id="per">' + opts + '</select><span>per page</span>'
    + '</div>';
}

function wirePager(total, rerender) {
  var pages = Math.max(1, Math.ceil(total / (state.per || total || 1)));
  PAGE.querySelectorAll('.pager button[data-pg]').forEach(function (b) {
    b.addEventListener('click', function () {
      var w = b.dataset.pg;
      state.page = w === 'first' ? 1 : w === 'prev' ? Math.max(1, state.page - 1)
        : w === 'next' ? Math.min(pages, state.page + 1) : pages;
      rerender();
    });
  });
  var sel = document.getElementById('per');
  if (sel) sel.addEventListener('change', function () {
    state.per = parseInt(sel.value, 10);
    state.page = 1;
    rerender();
  });
}

/* ---------- breadcrumbs ---------- */
function crumbs(parts) {
  CRUMBS.innerHTML = parts.map(function (p, i) {
    var last = i === parts.length - 1;
    var label = esc(p.label);
    return (last || !p.hash ? '<span class="here">' + label + '</span>'
                            : '<a href="#' + p.hash + '">' + label + '</a>')
      + (last ? '' : '<span class="sep">&rsaquo;</span>');
  }).join('');
}

/* ---------- view: organizations list ---------- */
function viewOrgs() {
  state.view = 'orgs';
  crumbs([{ label: 'Organizations' }]);

  var f = state.filter.trim().toLowerCase();
  var list = D.orgs.filter(function (o) { return !f || o.name.toLowerCase().indexOf(f) !== -1; });
  var showAllRow = !f || ALL_LABEL.toLowerCase().indexOf(f) !== -1;

  function draw() {
    var shown = pageSlice(list);
    var h = '<h1>Organizations</h1>'
      + '<div class="sub-line">' + num(D.orgs.length) + ' organizations &middot; '
      + num(D.totalRows) + ' records &middot; export generated ' + esc(D.generated) + '</div>'
      + '<div class="toolbar"><input type="text" id="orgfilter" placeholder="Filter organizations&hellip;"'
      + ' value="' + attr(state.filter) + '" autocomplete="off"></div>'
      + '<div class="scroll"><table><thead><tr>'
      + '<th>Organization</th><th>Type</th><th>Status</th><th>Records</th></tr></thead><tbody>';

    if (showAllRow && state.page === 1) {
      h += '<tr><td><a class="orgname" href="#org/' + encodeURIComponent(ALL) + '">'
        + ALL_LABEL + '</a></td>'
        + '<td class="empty">Everything in this export, unfiltered</td>'
        + '<td class="empty">&mdash;</td><td>' + num(D.totalRows) + '</td></tr>';
    }
    shown.forEach(function (o) {
      h += '<tr><td><a class="orgname" href="#org/' + encodeURIComponent(o.name) + '">'
        + esc(o.name) + '</a></td>'
        + '<td>' + (o.meta.organization_type ? esc(o.meta.organization_type) : '<span class="empty">&mdash;</span>') + '</td>'
        + '<td>' + (o.meta.organization_status ? esc(o.meta.organization_status) : '<span class="empty">&mdash;</span>') + '</td>'
        + '<td>' + num(o.total) + '</td></tr>';
    });
    if (!shown.length && !showAllRow) {
      h += '<tr><td colspan="4" class="empty" style="padding:28px;text-align:center">'
        + 'No organizations match that filter.</td></tr>';
    }
    h += '</tbody></table></div>' + pagerHtml(list.length);

    PAGE.innerHTML = h;
    wirePager(list.length, draw);

    var fi = document.getElementById('orgfilter');
    fi.addEventListener('input', function () {
      state.filter = fi.value;
      state.page = 1;
      var pos = fi.selectionStart;
      viewOrgs();
      var nf = document.getElementById('orgfilter');
      if (nf) { nf.focus(); nf.setSelectionRange(pos, pos); }
    });
  }
  draw();
  window.scrollTo(0, 0);
}

/* ---------- view: one organization (or the whole export) ---------- */
function railHtml(org, activeTable) {
  var isAll = org === ALL;
  var h = '<div class="rail"><h2>Records</h2><div class="items">'
    + '<button data-t="" class="' + (activeTable ? '' : 'on') + '">'
    + '<span>Overview</span></button><div class="divider"></div>';

  D.order.forEach(function (tkey) {
    if (!isAll && tkey === 'organizations') return;
    var n = countFor(tkey, org);
    var cls = n === 0 ? 'zero' : (tkey === activeTable ? 'on' : '');
    h += '<button data-t="' + attr(tkey) + '" class="' + cls + '"'
      + (n === 0 ? ' disabled' : '') + '>'
      + '<span>' + esc(D.tables[tkey].title) + '</span>'
      + '<span class="n">' + num(n) + '</span></button>';
  });
  return h + '</div></div>';
}

function overviewHtml(org) {
  if (org === ALL) {
    var types = D.order.length;
    return '<div class="card"><h2>Export contents</h2><div class="body"><dl class="dl">'
      + '<dt>Generated</dt><dd>' + esc(D.generated) + '</dd>'
      + '<dt>Organizations</dt><dd>' + num(D.orgs.length) + '</dd>'
      + '<dt>Records</dt><dd>' + num(D.totalRows) + ' across ' + types + ' asset types</dd>'
      + '<dt>Documents</dt><dd>' + num(Object.keys(D.docs).length) + ' linked</dd>'
      + '<dt>Records with attachments</dt><dd>'
      + num(Object.keys(D.atts).reduce(function (a, k) { return a + Object.keys(D.atts[k]).length; }, 0))
      + '</dd></dl></div></div>'
      + '<div class="note info">Pick an asset type on the right to browse every record in the '
      + 'export, across all organizations.</div>';
  }

  var o = orgByName[org], h = '';

  /* Site Summary is a flexible asset most orgs do not have; when present it is
     the most useful thing to lead with. */
  var ss = rowsFor('site-summary', org);
  if (ss.length) {
    var t = D.tables['site-summary'];
    h += '<div class="card"><h2>Site Summary</h2><div class="body"><dl class="dl">';
    ss.forEach(function (ri) {
      var row = t.rows[ri];
      t.cols.forEach(function (col, c) {
        var key = col.toLowerCase();
        if (key === 'id' || key === 'organization' || key === 'title' || key === 'archived') return;
        if (!row[c]) return;
        h += '<dt>' + esc(col) + '</dt><dd>'
          + (t.secret[c]
              ? '<span class="secret" data-reveal="' + attr(row[c]) + '" title="Click to reveal">'
                + '•••••• reveal</span>'
              : esc(row[c]))
          + '</dd>';
      });
    });
    h += '</dl></div></div>';
  }

  if (o) {
    var fields = [['organization_type', 'Type'], ['organization_status', 'Status'],
                  ['short_name', 'Short name'], ['description', 'Description'],
                  ['quick_notes', 'Quick notes'], ['alert', 'Alert'], ['id', 'IT Glue ID']];
    var body = fields.filter(function (f) { return o.meta[f[0]]; })
      .map(function (f) { return '<dt>' + f[1] + '</dt><dd>' + esc(o.meta[f[0]]) + '</dd>'; })
      .join('');
    if (body) h += '<div class="card"><h2>Organization</h2><div class="body">'
      + '<dl class="dl">' + body + '</dl></div></div>';
  }

  if (!h) h += '<div class="empty-state">No organization details in this export.</div>';
  return h;
}

function viewOrg(org, tkey) {
  state.view = 'org'; state.org = org; state.table = tkey || null;
  var label = org === ALL ? ALL_LABEL : org;
  var o = org === ALL ? null : orgByName[org];

  var cr = [{ label: 'Organizations', hash: 'orgs' },
            { label: label, hash: 'org/' + encodeURIComponent(org) }];
  if (tkey) cr.push({ label: D.tables[tkey].title });
  crumbs(cr);

  function draw() {
    var head = '<h1>' + esc(label)
      + (o && o.meta.organization_status
          ? '<span class="pill">' + esc(o.meta.organization_status) + '</span>' : '')
      + '</h1><div class="sub-line">'
      + (org === ALL
          ? num(D.orgs.length) + ' organizations &middot; ' + num(D.totalRows) + ' records'
          : (o && o.meta.organization_type ? esc(o.meta.organization_type) + ' &middot; ' : '')
            + num(o ? o.total : 0) + ' records')
      + '</div>';

    var body;
    if (tkey) {
      var idxs = rowsFor(tkey, org);
      body = '<h2>' + esc(D.tables[tkey].title)
        + '<span class="pill grey">' + num(idxs.length) + '</span></h2>'
        + buildTable(tkey, pageSlice(idxs), '', org !== ALL)
        + pagerHtml(idxs.length);
    } else {
      body = overviewHtml(org);
    }

    PAGE.innerHTML = '<div class="split">' + railHtml(org, tkey)
      + '<div class="col-main">' + head + body + '</div></div>';

    if (tkey) wirePager(rowsFor(tkey, org).length, draw);
    PAGE.querySelectorAll('.rail button:not(.zero)').forEach(function (b) {
      b.addEventListener('click', function () {
        state.page = 1;
        if (b.dataset.t) go('org', org, b.dataset.t); else go('org', org);
      });
    });
    markClamped();
  }
  draw();
  window.scrollTo(0, 0);
}

/* ---------- view: one document, rendered inline ---------- */
function viewDoc(loc) {
  var tbl = D.tables['documents'];
  var ri = docRow[loc];
  var row = ri === undefined ? null : tbl.rows[ri];
  var org = row && tbl.orgIdx !== null ? row[tbl.orgIdx] : null;
  var name = row && tbl.nameIdx !== null ? row[tbl.nameIdx] : loc;
  var body = D.bodies ? D.bodies[loc] : null;
  var file = D.docs[loc];

  state.view = 'doc'; state.org = org; state.table = 'documents';

  var cr = [{ label: 'Organizations', hash: 'orgs' }];
  if (org) {
    cr.push({ label: org, hash: 'org/' + encodeURIComponent(org) });
    cr.push({ label: 'Documents', hash: 'org/' + encodeURIComponent(org) + '/documents' });
  }
  cr.push({ label: name });
  crumbs(cr);

  var meta = [];
  if (row) {
    tbl.cols.forEach(function (col, c) {
      var k = col.toLowerCase();
      if (k === 'id' || k === 'name' || k === 'organization' || !row[c]) return;
      meta.push('<span><b>' + esc(col) + ':</b> ' + esc(row[c]) + '</span>');
    });
  }
  if (file) meta.push('<span><a href="' + attr(file) + '">Open original file</a></span>');

  var main = '<h1>' + esc(name) + '</h1>'
    + '<div class="sub-line">' + (org ? esc(org) + ' &middot; ' : '') + 'Document</div>'
    + (meta.length ? '<div class="card"><div class="body"><div class="docmeta">'
        + meta.join('') + '</div></div></div>' : '')
    + '<div class="card"><div class="docbody">'
    + (body || '<span class="empty">This document has no rendered HTML in the export.'
        + (file ? ' Use the original file link above.' : '') + '</span>')
    + '</div></div>';

  PAGE.innerHTML = '<div class="split">'
    + (org ? railHtml(org, 'documents') : '')
    + '<div class="col-main">' + main + '</div></div>';

  PAGE.querySelectorAll('.rail button:not(.zero)').forEach(function (b) {
    b.addEventListener('click', function () {
      state.page = 1;
      if (b.dataset.t) go('org', org, b.dataset.t); else go('org', org);
    });
  });
  window.scrollTo(0, 0);
}

/* ---------- view: search ---------- */
function viewSearch(term) {
  state.view = 'search';
  crumbs([{ label: 'Organizations', hash: 'orgs' }, { label: 'Search: ' + term }]);
  var t = term.toLowerCase();

  var hits = [];
  D.order.forEach(function (tkey) {
    var hs = haystack[tkey], tbl = D.tables[tkey];
    for (var i = 0; i < hs.length; i++) {
      if (hs[i].indexOf(t) === -1) continue;
      hits.push({
        org: tbl.orgIdx !== null ? (tbl.rows[i][tbl.orgIdx] || '(unassigned)') : '(account-wide)',
        tkey: tkey, i: i
      });
    }
  });
  hits.sort(function (a, b) {
    return a.org.toLowerCase() < b.org.toLowerCase() ? -1
         : a.org.toLowerCase() > b.org.toLowerCase() ? 1 : 0;
  });
  var orgCount = Object.keys(hits.reduce(function (m, x) { m[x.org] = 1; return m; }, {})).length;

  function draw() {
    var shown = pageSlice(hits), h = '', curOrg = null, curTbl = null, batch = [];

    function flush() {
      if (!batch.length) return;
      h += buildTable(curTbl, batch, term, true);
      batch = [];
    }
    shown.forEach(function (x) {
      if (x.org !== curOrg) {
        flush(); curTbl = null;
        curOrg = x.org;
        h += '<div class="hit-org"><a href="#org/' + encodeURIComponent(x.org) + '">'
          + esc(x.org) + '</a></div>';
      }
      if (x.tkey !== curTbl) {
        flush();
        curTbl = x.tkey;
        h += '<div class="hit-tbl">' + esc(D.tables[x.tkey].title) + '</div>';
      }
      batch.push(x.i);
    });
    flush();

    PAGE.innerHTML = '<h1>Search</h1><div class="sub-line">'
      + num(hits.length) + ' match' + (hits.length === 1 ? '' : 'es')
      + ' for &ldquo;' + esc(term) + '&rdquo; in ' + num(orgCount) + ' organization'
      + (orgCount === 1 ? '' : 's') + '</div>'
      + (hits.length ? h + pagerHtml(hits.length)
                     : '<div class="empty-state">Nothing found.</div>');
    wirePager(hits.length, draw);
    markClamped();
  }
  draw();
  window.scrollTo(0, 0);
}

/* ---------- routing ---------- */
function route() {
  var p = hashParts();
  state.page = 1;

  if (p[0] === 'doc' && p[1]) {
    if (Q.value) Q.value = '';
    viewDoc(p[1]);
  } else if (p[0] === 'org' && p[1]) {
    if (Q.value) Q.value = '';
    viewOrg(p[1], p[2] && D.tables[p[2]] ? p[2] : null);
  } else if (p[0] === 'q' && p[1]) {
    if (Q.value !== p[1]) Q.value = p[1];
    viewSearch(p[1]);
  } else {
    if (Q.value) Q.value = '';
    viewOrgs();
  }

  document.getElementById('nav-orgs').classList.toggle('on', state.view === 'orgs');
  document.getElementById('nav-types').classList.toggle('on',
    state.view === 'org' && state.org === ALL);
}

/* ---------- events ---------- */
PAGE.addEventListener('click', function (e) {
  var cl = e.target.closest('.clamp.more');
  if (cl) { cl.classList.toggle('open'); return; }
  var s = e.target.closest('.secret');
  if (!s) return;
  if (s.dataset.shown === '1') {
    s.textContent = '•••••• reveal';
    s.dataset.shown = '0';
  } else {
    s.textContent = s.dataset.reveal;
    s.dataset.shown = '1';
  }
});

document.getElementById('home').addEventListener('click', function () { go('orgs'); });
document.getElementById('nav-orgs').addEventListener('click', function () {
  state.filter = ''; go('orgs');
});
document.getElementById('nav-types').addEventListener('click', function () { go('org', ALL); });

var qt;
Q.addEventListener('input', function () {
  clearTimeout(qt);
  var v = Q.value.trim();
  qt = setTimeout(function () {
    if (v.length < 2) {
      if (location.hash.indexOf('#q/') === 0) go('orgs');
      return;
    }
    go('q', v);
  }, 200);
});
window.addEventListener('hashchange', route);

document.getElementById('counts').textContent =
  num(D.orgs.length) + ' orgs · ' + num(D.totalRows) + ' records';
route();
"""


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


def render(payload: dict) -> str:
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    # Keep the payload from terminating the enclosing <script> element.
    data = data.replace("</", "<\\/")
    html = TEMPLATE.replace("__GENERATED__", payload["generated"])
    html = html.replace("__SCRIPT__", SCRIPT)
    return html.replace("__DATA__", data)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export_dir", nargs="?", default="temp",
                    help="Root of the extracted IT Glue export (default: temp)")
    ap.add_argument("-o", "--output", default=None,
                    help="Output path (default: <export_dir>/index.html)")
    ap.add_argument("--include-logs", action="store_true",
                    help="Include logs.csv (activity audit trail, ~12k rows)")
    args = ap.parse_args()

    export_dir = Path(args.export_dir).expanduser().resolve()
    if not export_dir.is_dir():
        print(f"error: {export_dir} is not a directory", file=sys.stderr)
        return 1
    if not list(export_dir.glob("*.csv")):
        print(f"error: no CSVs at {export_dir} -- is this an extracted export root?",
              file=sys.stderr)
        return 1

    print(f"Reading {export_dir}")
    payload = build_payload(export_dir, args.include_logs)
    html = render(payload)

    out = Path(args.output) if args.output else export_dir / "index.html"
    out.write_text(html, encoding="utf-8")

    size_mb = out.stat().st_size / 1048576
    print(f"\nWrote {out}")
    print(f"  {size_mb:.1f} MB  |  {len(payload['orgs'])} orgs  |  "
          f"{payload['totalRows']:,} records  |  {len(payload['order'])} asset types")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
