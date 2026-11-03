"""Single-file browsable index for an extracted IT Glue export.

Reads the root-level CSVs of an Extracted Export and emits one self-contained
index.html, designed to sit at the export root so relative links into
documents/ and attachments/ resolve. The export is only read, never modified.
"""

from .payload import build_payload
from .render import render

__all__ = ["build_payload", "render"]
