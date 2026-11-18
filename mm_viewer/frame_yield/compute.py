"""Frame-yield data layer (PURE: no Qt).

Two granularities, both answering "how many LMI frames carry a groove measurement":

  per_unit(groove_folder)      one row per LMI unit (FL/FR/RL/RR), whole scan, from
                               tire_laser_groove_data/<unit>/scan_grooves_full.csv.
                               A frame counts as WITH a groove if >=1 of its Depth_0..9
                               slots is a real value (the -1.8e308 = -DBL_MAX sentinel
                               means "no groove in this slot").
  per_axle(diag_folder, ...)   one row per (axle, wheel), from the diagnostics algo's
                               per-axle capture window (request.json FirstFrameIndex..
                               LastFrameIndex) joined to the raw LMI frames in it.

Row = {label, assigned, with_groove, readings, median, gap, unit, axle}. 'assigned' is
the frames attributed to that wheel/axle (captured, or the algo's window); 'with_groove'
is how many of them carried >=1 groove; 'readings' is the total groove readings; 'median'
the pooled median depth (mm). gap=True + assigned=None marks a missing data source.
"""

import csv
import math
import os

from mm_viewer.diagnostics import loader


UNIT_ORDER = ("LMI_0_FRONT_LEFT", "LMI_1_FRONT_RIGHT",
              "LMI_2_REAR_LEFT", "LMI_3_REAR_RIGHT")
_WHEEL = {"FRONT_LEFT": "FL", "FRONT_RIGHT": "FR",
          "REAR_LEFT": "RL", "REAR_RIGHT": "RR"}
SENT = -1e300          # anything <= this is the -DBL_MAX "no groove" sentinel
GROOVE_FULL_NAME = "scan_grooves_full.csv"


def wheel_of(unit):
    u = (unit or "").upper()
    for k, v in _WHEEL.items():
        if k in u:
            return v
    return unit or "?"


def _median(vals):
    v = sorted(x for x in vals if x is not None and not math.isnan(x))
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _row(label, assigned, with_groove, readings, median,
         gap=False, unit=None, axle=None):
    return {"label": label, "assigned": assigned, "with_groove": with_groove,
            "readings": readings, "median": median, "gap": gap,
            "unit": unit, "axle": axle}


def _unit_dir(groove_folder, unit):
    d = os.path.join(groove_folder, unit)
    if os.path.isdir(d):
        return d
    # some layouts drop the position suffix: match by the LMI_<n> prefix
    pref = "_".join(unit.split("_")[:2]).upper()
    try:
        cand = [n for n in os.listdir(groove_folder)
                if n.upper().startswith(pref)
                and os.path.isdir(os.path.join(groove_folder, n))]
    except OSError:
        cand = []
    return os.path.join(groove_folder, sorted(cand)[0]) if cand else None


def per_unit(groove_folder):
    """One row per LMI unit present under groove_folder (tire_laser_groove_data)."""
    rows = []
    if not groove_folder or not os.path.isdir(groove_folder):
        return rows
    for unit in UNIT_ORDER:
        udir = _unit_dir(groove_folder, unit)
        if udir is None:
            continue                       # unit not in this scan at all
        f = os.path.join(udir, GROOVE_FULL_NAME)
        if not os.path.isfile(f) or os.path.getsize(f) == 0:
            rows.append(_row(wheel_of(unit), None, None, None, None,
                             gap=True, unit=unit))
            continue
        total = wg = rdg = 0
        depths = []
        try:
            with open(f, newline="", encoding="utf-8", errors="replace") as fh:
                rd = csv.DictReader(fh)
                fn = rd.fieldnames or []
                dcols = [c for c in fn if c.startswith("Depth_")]
                if not dcols:
                    rows.append(_row(wheel_of(unit), 0, 0, 0, None, unit=unit))
                    continue
                for r in rd:
                    total += 1
                    has = False
                    for c in dcols:
                        try:
                            x = float(r[c])
                        except (TypeError, ValueError):
                            continue
                        if x > SENT:
                            has = True
                            rdg += 1
                            depths.append(x)
                    if has:
                        wg += 1
        except OSError:
            rows.append(_row(wheel_of(unit), None, None, None, None,
                             gap=True, unit=unit))
            continue
        rows.append(_row(wheel_of(unit), total, wg, rdg, _median(depths), unit=unit))
    return rows


def per_axle(diag_folder, axle_filter=None):
    """One row per (axle, wheel) from the diagnostics algo's per-axle windows."""
    rows = []
    if not diag_folder:
        return rows
    try:
        axles = loader.discover_axles(diag_folder)
    except Exception:
        return rows
    rr = loader.find_raw_groove_root(diag_folder)
    for a in axles:
        axn = a.get("axle_index")
        if axle_filter is not None and axn != axle_filter:
            continue
        fr, lr = a.get("first_frame"), a.get("last_frame")
        assigned = (lr - fr + 1) if (isinstance(fr, (int, float))
                                     and isinstance(lr, (int, float))) else None
        raw = loader.raw_frames_for_axle(rr, a["lmi_unit"], fr, lr) if rr else {}
        wg = len(raw)
        depths = [d for pts in raw.values() for (_x, d) in pts]
        if assigned is None or assigned < wg:
            assigned = wg                  # no window on disk: fall back to what was read
        rows.append(_row("Axle %s · %s" % (axn, wheel_of(a.get("lmi_unit"))),
                         assigned, wg, len(depths), _median(depths),
                         unit=a.get("lmi_unit"), axle=axn))
    return rows


def axle_numbers(diag_folder):
    """Sorted distinct axle indices in the diagnostics tree (for the Axle selector)."""
    if not diag_folder:
        return []
    try:
        axles = loader.discover_axles(diag_folder)
    except Exception:
        return []
    return sorted({a.get("axle_index") for a in axles
                   if a.get("axle_index") is not None})


def scan_id(diag_folder=None, groove_folder=None):
    """Best scan-id label: the algo's ScanId, else the scan folder name."""
    if diag_folder:
        try:
            sid = loader.scan_id_of(loader.discover_axles(diag_folder))
            if sid:
                return sid
        except Exception:
            pass
    root = scan_root_of(groove_folder or diag_folder)
    return os.path.basename(root.rstrip("\\/")) if root else None


def scan_root_of(sub_folder):
    """The scan root given one of its child folders (tire_laser_groove_data / diagnostics)."""
    if not sub_folder:
        return None
    return os.path.dirname(os.path.abspath(sub_folder.rstrip("\\/")))


def resolve_scan(root):
    """(groove_folder, diag_folder) under a scan root, each or None if absent."""
    if not root or not os.path.isdir(root):
        return None, None
    g = os.path.join(root, "tire_laser_groove_data")
    d = os.path.join(root, "diagnostics")
    return (g if os.path.isdir(g) else None), (d if os.path.isdir(d) else None)
