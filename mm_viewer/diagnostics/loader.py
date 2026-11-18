"""Read the diagnostics (laser-groove ALGO) tree and the raw LMI frame grooves.

PURE: filesystem + CSV/JSON only, no Qt. Everything the panel and the Excel export
need to build an algo-vs-LMI comparison is produced here.

ON-DISK LAYOUT (one complete scan)
  diagnostics/
    laser_tread/
      LMI_0_FRONT_LEFT/
        truck_axle1/                         one folder per axle THIS sensor saw
          LMI_0_FRONT_LEFT_finalized_groove_depths.csv   <- the algo result
          LMI_0_FRONT_LEFT_groove_depths.csv             <- pre-finalise (kept, unused)
          request.json                        <- ScanId, SensorId, AxleIndex, frame window
          00001_split_segments_f0.png ...      <- step-by-step algo visualisations
          00021_finalized_grooves.png
        truck_axle2/ ...
      LMI_1_FRONT_RIGHT/ ...
      LMI_2_REAR_LEFT/ ...

The raw LMI firmware depths live in a SIBLING of diagnostics/ in a full scan:
  tire_laser_groove_data/LMI_0_FRONT_LEFT/frame_<N>_grooves.csv
with columns  Depth, Device_Timestamp_usec, Host_Timestamp_usec, Width, X, Z
(older firmware: sensor_timestamp_msec / timestamp_msec). `-1.797e308` = no groove.

request.json's FirstFrameIndex..LastFrameIndex is the EXACT raw-frame window the algo
consumed for that axle - so the two sources are joined per axle by that window, never
by guessing which frames belong to which axle.
"""

import json
import math
import os

from mm_viewer.common import lp_isdir, lp_isfile, lp_listdir, lp_open
from mm_viewer.config import LASER_SENTINEL_ABS


# diagnostics/<DIAG_SUBDIR>/LMI_* - the only shape written so far. Both names are
# matched case-insensitively so a re-cased upload still loads.
DIAG_DIRNAME = "diagnostics"
DIAG_SUBDIR = "laser_tread"
RAW_GROOVE_DIRNAME = "tire_laser_groove_data"

FINALIZED_SUFFIX = "_finalized_groove_depths.csv"
REQUEST_NAME = "request.json"

# Canonical 4-wheel order, same strings the rest of the app uses (grooves.loader).
POS_ORDER = ["FRONT LEFT", "FRONT RIGHT", "REAR LEFT", "REAR RIGHT"]


def _finite(v):
    """True for a real measurement: finite and inside the sentinel guard."""
    return v is not None and math.isfinite(v) and abs(v) < LASER_SENTINEL_ABS


def _to_float(s):
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _to_bool(s):
    return str(s).strip().lower() in ("true", "1", "yes")


def pos_from_lmi_name(name):
    """'LMI_0_FRONT_LEFT' -> ('FRONT LEFT', 0); tolerant of case and extra tokens."""
    parts = str(name).replace("-", "_").split("_")
    idx = None
    for i, p in enumerate(parts):
        if p.upper() == "LMI" and i + 1 < len(parts) and parts[i + 1].isdigit():
            idx = int(parts[i + 1])
            break
    up = str(name).upper()
    for pos in POS_ORDER:
        if pos.replace(" ", "_") in up or pos.replace(" ", "") in up.replace("_", ""):
            return pos, idx
    return None, idx


def axle_num_from_name(name):
    """'truck_axle2' -> 2, else None (request.json's AxleIndex is preferred)."""
    digits = "".join(ch for ch in str(name) if ch.isdigit())
    return int(digits) if digits else None


# ----------------------------------------------------------------------------
# finding the tree
# ----------------------------------------------------------------------------
def find_laser_tread_root(folder):
    """
    Resolve the folder that directly holds the LMI_* sensor folders, from any of:
      a scan root            -> scan/diagnostics/laser_tread
      a diagnostics/ folder  -> diagnostics/laser_tread
      a laser_tread/ folder  -> itself
    Returns the abspath, or None when none of these holds LMI_* folders.
    """
    if not folder or not lp_isdir(folder):
        return None
    folder = os.path.abspath(folder)

    def _has_lmi(d):
        try:
            return any(n.upper().startswith("LMI_") and lp_isdir(os.path.join(d, n))
                       for n in lp_listdir(d))
        except OSError:
            return False

    cands = [
        os.path.join(folder, DIAG_DIRNAME, DIAG_SUBDIR),   # scan root
        os.path.join(folder, DIAG_SUBDIR),                 # diagnostics/
        folder,                                            # laser_tread/ itself
    ]
    for c in cands:
        if lp_isdir(c) and _has_lmi(c):
            return os.path.abspath(c)
    return None


def diagnostics_present(folder):
    """Cheap check used by the model: does this folder carry a diagnostics tree?"""
    return find_laser_tread_root(folder) is not None


def find_raw_groove_root(diag_or_scan_folder):
    """
    Locate tire_laser_groove_data that pairs with a diagnostics tree, searching the
    scan root that contains diagnostics/ and the diagnostics folder's own parents.
    Returns the abspath or None (then the comparison runs algo-only).
    """
    if not diag_or_scan_folder:
        return None
    here = os.path.abspath(diag_or_scan_folder)
    seen = set()
    # walk up a few levels: laser_tread -> diagnostics -> scan root -> ...
    for _ in range(4):
        if here in seen:
            break
        seen.add(here)
        cand = os.path.join(here, RAW_GROOVE_DIRNAME)
        if lp_isdir(cand):
            return os.path.abspath(cand)
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return None


# ----------------------------------------------------------------------------
# reading the algo output
# ----------------------------------------------------------------------------
def read_request(axle_folder):
    """request.json -> dict, or {} when absent/unreadable (never raises)."""
    path = os.path.join(axle_folder, REQUEST_NAME)
    if not lp_isfile(path):
        return {}
    try:
        with lp_open(path, "r", encoding="utf-8", errors="replace") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


# Columns of *_finalized_groove_depths.csv, read by name so column order is irrelevant.
_FINAL_NUM = ("i_axle", "i_line", "segment", "position", "wheel_edge_position",
              "avg_depth", "median_depth", "avg_original_depth",
              "median_original_depth", "std_depth")


def read_finalized_grooves(axle_folder):
    """
    Read *_finalized_groove_depths.csv -> list of algo-groove dicts (abs depths added).

    Depths in the file are NEGATIVE (below the tread surface). Each row keeps the raw
    signed values AND an absolute-mm convenience field so callers never re-derive it:
      depth_corr  = |median_depth|            the algo's 0.87x-scaled depth
      depth_orig  = |median_original_depth|   the algo's raw depth (before 0.87x)
    plus tire ('outer'/'inner'), wheel_edge_position (groove identity), position
    (lateral, same frame as the raw LMI X), std_depth, n_measurements, valid_bottom.
    """
    import csv
    path = _finalized_csv_path(axle_folder)
    if path is None:
        return []
    rows = []
    try:
        with lp_open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            for raw in csv.DictReader(fh):
                row = {"groove_id": _to_float(raw.get("groove_id")),
                       "target_axle": (raw.get("target_axle") or "").strip(),
                       "tire": (raw.get("tire") or "").strip().lower(),
                       "n_measurements": int(_to_float(raw.get("n_measurements")) or 0),
                       "valid_bottom": _to_bool(raw.get("valid_bottom"))}
                for k in _FINAL_NUM:
                    row[k] = _to_float(raw.get(k))
                # absolute-mm depths (what every table/plot shows)
                row["depth_corr"] = (abs(row["median_depth"])
                                     if _finite(row["median_depth"]) else None)
                row["depth_orig"] = (abs(row["median_original_depth"])
                                     if _finite(row["median_original_depth"]) else None)
                row["depth_corr_avg"] = (abs(row["avg_depth"])
                                         if _finite(row["avg_depth"]) else None)
                row["depth_orig_avg"] = (abs(row["avg_original_depth"])
                                         if _finite(row["avg_original_depth"]) else None)
                rows.append(row)
    except (OSError, ValueError):
        return []
    return rows


def _finalized_csv_path(axle_folder):
    try:
        names = [n for n in lp_listdir(axle_folder)
                 if n.lower().endswith(FINALIZED_SUFFIX.lower())]
    except OSError:
        return None
    if not names:
        return None
    return os.path.join(axle_folder, sorted(names)[0])


def axle_images(axle_folder):
    """
    The algo's step-by-step PNGs for one axle, sorted by their NNNNN_ prefix, each with
    a human label derived from the filename. Returns [(label, abspath)].
    """
    try:
        names = [n for n in lp_listdir(axle_folder) if n.lower().endswith(".png")]
    except OSError:
        return []

    def _key(n):
        head = n.split("_", 1)[0]
        return (int(head) if head.isdigit() else 10**9, n.lower())

    out = []
    for n in sorted(names, key=_key):
        stem = os.path.splitext(n)[0]
        # drop a leading "00017_" index and prettify the rest
        body = stem.split("_", 1)[1] if stem.split("_", 1)[0].isdigit() else stem
        label = body.replace("_", " ").strip() or stem
        out.append((label, os.path.join(axle_folder, n)))
    return out


# ----------------------------------------------------------------------------
# discovery -> DiagAxle list
# ----------------------------------------------------------------------------
def discover_axles(folder):
    """
    Walk a diagnostics tree and return an ordered list of DiagAxle dicts (one per LMI
    sensor x axle that the algo wrote), each:
      { lmi_unit, lmi_index, wheel_pos, axle_index, sensor_id, scan_id,
        folder, first_frame, last_frame, grooves:[...], n_images, images_folder }
    Ordered by wheel position (FL, FR, RL, RR) then axle index. [] when nothing found.
    """
    root = find_laser_tread_root(folder)
    if root is None:
        return []
    axles = []
    try:
        units = sorted(n for n in lp_listdir(root)
                       if n.upper().startswith("LMI_")
                       and lp_isdir(os.path.join(root, n)))
    except OSError:
        return []
    for unit in units:
        unit_dir = os.path.join(root, unit)
        wheel_pos, lmi_index = pos_from_lmi_name(unit)
        try:
            axle_dirs = sorted(n for n in lp_listdir(unit_dir)
                               if lp_isdir(os.path.join(unit_dir, n)))
        except OSError:
            axle_dirs = []
        for ad in axle_dirs:
            axle_folder = os.path.join(unit_dir, ad)
            grooves = read_finalized_grooves(axle_folder)
            req = read_request(axle_folder)
            axle_index = req.get("AxleIndex")
            if axle_index is None and grooves:
                # fall back to the target_axle label on the rows
                lab = grooves[0].get("target_axle", "")
                axle_index = axle_num_from_name(lab)
            if axle_index is None:
                axle_index = axle_num_from_name(ad)
            axles.append({
                "lmi_unit": unit,
                "lmi_index": lmi_index,
                "wheel_pos": wheel_pos,
                "axle_index": axle_index,
                "sensor_id": req.get("SensorId", unit),
                "scan_id": req.get("ScanId"),
                "scan_ts": req.get("ScanTimestampUtc"),
                "folder": axle_folder,
                "first_frame": req.get("FirstFrameIndex"),
                "last_frame": req.get("LastFrameIndex"),
                "grooves": grooves,
                "n_images": len(axle_images(axle_folder)),
            })

    def _ord(a):
        wp = a["wheel_pos"]
        return (POS_ORDER.index(wp) if wp in POS_ORDER else 99,
                a["axle_index"] if a["axle_index"] is not None else 99)

    axles.sort(key=_ord)
    return axles


def scan_id_of(axles):
    """The scan id shared by the axles (first non-empty), or None."""
    for a in axles:
        if a.get("scan_id"):
            return a["scan_id"]
    return None


# ----------------------------------------------------------------------------
# reading the raw LMI frame grooves (what the firmware reported per frame)
# ----------------------------------------------------------------------------
# The two firmware timestamp-column generations; we only need Depth/X/Width here, but
# the header is read by name so either generation parses.
def read_frame_grooves(path):
    """
    One frame_<N>_grooves.csv -> list of (x_mm, depth_mm) for rows where BOTH are real.

    Columns are NOT guaranteed row-aligned (a row can have a valid Depth and a sentinel
    X, or vice-versa); such rows are dropped, since a groove with no position cannot be
    placed and a position with no depth is not a measurement.
    """
    import csv
    out = []
    try:
        with lp_open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            rd = csv.DictReader(fh)
            if rd.fieldnames is None:
                return out
            # tolerate Depth/depth and X/x
            fmap = {k.lower(): k for k in rd.fieldnames}
            dk, xk = fmap.get("depth"), fmap.get("x")
            if dk is None or xk is None:
                return out
            for r in rd:
                d, x = _to_float(r.get(dk)), _to_float(r.get(xk))
                if _finite(d) and _finite(x):
                    out.append((x, d))
    except (OSError, ValueError):
        return out
    return out


def raw_frames_for_axle(raw_groove_root, lmi_unit, first_frame, last_frame):
    """
    Read the raw LMI grooves for one axle's frame window.

    Returns { frame_index: [(x_mm, depth_mm), ...] } for frame_<N>_grooves.csv files
    whose N is in [first_frame, last_frame]. Missing bounds -> read every frame the
    unit has (algo-only fallback). {} when the unit folder is absent.
    """
    if not raw_groove_root:
        return {}
    unit_dir = os.path.join(raw_groove_root, lmi_unit)
    if not lp_isdir(unit_dir):
        # some layouts drop the position suffix; match by the LMI_<n> prefix
        pref = "_".join(lmi_unit.split("_")[:2]).upper()
        try:
            alt = [n for n in lp_listdir(raw_groove_root)
                   if n.upper().startswith(pref)
                   and lp_isdir(os.path.join(raw_groove_root, n))]
        except OSError:
            alt = []
        if not alt:
            return {}
        unit_dir = os.path.join(raw_groove_root, sorted(alt)[0])
    try:
        names = [n for n in lp_listdir(unit_dir) if n.lower().endswith("_grooves.csv")]
    except OSError:
        return {}
    lo = first_frame if isinstance(first_frame, (int, float)) else None
    hi = last_frame if isinstance(last_frame, (int, float)) else None
    frames = {}
    for n in names:
        idx = _frame_index(n)
        if idx is None:
            continue
        if lo is not None and hi is not None and not (lo <= idx <= hi):
            continue
        pts = read_frame_grooves(os.path.join(unit_dir, n))
        if pts:
            frames[idx] = pts
    return frames


def _frame_index(name):
    import re
    m = re.search(r"frame[_\-]?(\d+)", name, re.IGNORECASE)
    return int(m.group(1)) if m else None


# ----------------------------------------------------------------------------
# stitched side image (displayed if present; the viewer never stitches one itself)
# ----------------------------------------------------------------------------
STITCH_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")
# a root-level file whose name contains one of these is treated as a stitched image
STITCH_NAME_HINTS = ("stitch", "pano", "mosaic", "helios", "full_wall", "side_image",
                     "wall_image", "full_side", "stitched")
# a one-level subfolder with one of these names holds stitched images
STITCH_DIR_HINTS = ("stitched", "stitch", "panorama", "mosaic", "helios",
                    "stitched_images")


def find_stitched_images(scan_folder):
    """
    Stitched side/undercarriage images present in a scan, or []. Two places are checked
    (and ONLY these, so the camera frame folders are never trawled):
      * a file at the scan root whose name contains a stitch hint,
      * any image inside a one-level subfolder named like a stitch folder.
    Returns a sorted, de-duplicated list of abspaths. The viewer DISPLAYS these; it
    never generates one.
    """
    if not scan_folder or not lp_isdir(scan_folder):
        return []
    root = os.path.abspath(scan_folder)
    found = []
    try:
        names = lp_listdir(root)
    except OSError:
        return []
    for n in names:
        full = os.path.join(root, n)
        low = n.lower()
        if lp_isfile(full) and low.endswith(STITCH_IMAGE_EXTS) \
                and any(h in low for h in STITCH_NAME_HINTS):
            found.append(full)
        elif lp_isdir(full) and low in STITCH_DIR_HINTS:
            try:
                for m in lp_listdir(full):
                    if m.lower().endswith(STITCH_IMAGE_EXTS):
                        found.append(os.path.join(full, m))
            except OSError:
                pass
    return sorted(set(os.path.abspath(p) for p in found))
