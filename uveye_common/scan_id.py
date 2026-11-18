"""Scan IDs. A scan folder is named  <UTC start>_<uuid>, e.g.
    2026-09-30T10-22-40.000Z_855810f0-7ced-4d64-8654-f185035f9d78

Two questions, two functions (moved verbatim from the tools that asked them):
  parse_folder_name(name) -> (scan_id, uuid)   MM Viewer: the name of ONE scan folder
  from_path(path)         -> scan_id           IQ toolkit: any file path inside a scan
"""
import os
import re

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
SCAN_ID_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.\d+Z_[0-9a-fA-F-]{36}")
SCAN_PART_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}[^\\/]*")   # a timestamp-looking path part


def parse_folder_name(folder_name):
    """Return (scan_id, scan_uuid) parsed from a scan-folder name (was mm_viewer parse_scan_id)."""
    m = SCAN_ID_RE.search(folder_name)
    if m:
        sid = m.group(0)
    else:
        sid = folder_name[3:] if folder_name.lower().startswith("mm_") else folder_name
    mu = UUID_RE.search(sid)
    return sid, (mu.group(0) if mu else None)


def from_path(path):
    """Scan folder name = the LAST timestamp-looking path part (an evidence path may contain
    another one); fallback = the grand-parent folder (was mm_iq_core._scan_id_from_path)."""
    m = SCAN_PART_RE.findall(path)
    return m[-1] if m else os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(path))))
