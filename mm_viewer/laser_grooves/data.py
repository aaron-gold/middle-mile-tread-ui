"""Scan -> wheels, algo grooves and the profile frames assigned to each wheel.  PURE (no Qt).

Sources (all inside one scan folder):
  diagnostics/laser_tread/LMI_<n>_<POS>/<axle dir>/
      *_finalized_groove_depths.csv   the algo result: one row per groove, tire inner/outer,
                                      wheel_edge_position = groove identity (distance from
                                      the wheel edge, outside -> inside), depths NEGATIVE
      request.json                    AxleIndex + FirstFrameIndex..LastFrameIndex
      NNNNN_split_segments_f<N>.png   one per frame the algo actually processed
  Scan_Output_Classified/scan_classification_manifest.csv
                                      wheel-assembler assignment, one row per (file, axle);
                                      calculated_host_timestamp_usec = trigger-log clock
  tire_laser_groove_data/LMI_<n>_<POS>/frame_<N>_profile.csv   x,z (-1.797e308 = no point)

The firmware groove depths in tire_laser_groove_data (scan_grooves_full.csv,
frame_<N>_grooves.csv) are NOT used: the system now measures with the algo.

Depth shown = |median_depth| (median over the frames the algo measured the groove in).
Median, not mean: n is 2-8 frames per groove and one bad frame moves a mean by up to
~1 mm (std up to 0.9 mm seen on MVT scans); with n = 2 both are the same number.
"""

import csv
import math
import os
import re
from collections import defaultdict

from mm_viewer.common import lp_isdir, lp_isfile, lp_listdir, lp_open
from mm_viewer.diagnostics.loader import (
    discover_axles, find_laser_tread_root, pos_from_lmi_name, RAW_GROOVE_DIRNAME,
)

MANIFEST_DIR = "Scan_Output_Classified"
MANIFEST_NAME = "scan_classification_manifest.csv"

# MM-ART-GRV-003: the system must detect tread < 4/32" on steer and < 2/32" on drive.
INCH_MM = 25.4
LIMIT_STEER_MM = 4.0 / 32.0 * INCH_MM          # 3.175 mm
LIMIT_OTHER_MM = 2.0 / 32.0 * INCH_MM          # 1.5875 mm

# A profile frame with fewer valid points than this is "no data" (a laser-off frame or
# a fragment). Full tread profiles on MVT carry ~3,500-9,500 of 12,196 points.
MIN_VALID_POINTS = 1000

SIDES = ("Left", "Right")
_SPLIT_RE = re.compile(r"split_segments_f(\d+)", re.IGNORECASE)
_PROFILE_RE = re.compile(r"frame_(\d+)_profile\.csv$", re.IGNORECASE)
_AXLE_RE = re.compile(r"axle[_ ]?(\d+)", re.IGNORECASE)


def profile_errors(mp):
    """Errors only known once a wheel's profiles are read: [(level, text)]."""
    out = []
    if mp is None:
        return out
    if mp.n_assigned and mp.n_data == 0:
        out.append(("error", "no usable profile among the %d assigned" % mp.n_assigned))
    if mp.missing:
        out.append(("warn", "%d assigned profile file%s missing or unreadable (f%s)"
                    % (len(mp.missing), "" if len(mp.missing) == 1 else "s",
                       ", f".join(str(n) for n in mp.missing[:6])
                       + (" …" if len(mp.missing) > 6 else ""))))
    return out


def depth_limit(axle):
    """Detection threshold (mm) for this axle: steer = axle 1."""
    return LIMIT_STEER_MM if axle == 1 else LIMIT_OTHER_MM


# Groove colour on the vehicle map (display only, not an acceptance verdict):
#   critical  below the axle's detection limit (2/32" = 1.6 mm; steer 4/32" = 3.2 mm)
#   marginal  below 4/32" (3.2 mm) on a non-steer axle
#   ok        at or above 4/32"
#   none      no depth
def groove_state(depth, axle):
    if depth is None:
        return "none"
    if depth < depth_limit(axle):
        return "critical"
    if depth < LIMIT_STEER_MM:
        return "marginal"
    return "ok"


def unit_info(unit):
    """'LMI_2_REAR_LEFT' -> ('Left', 'rear', 2). Side/laser None when not in the name."""
    pos, idx = pos_from_lmi_name(unit)
    up = str(unit).upper()
    side = "Left" if "LEFT" in up else "Right" if "RIGHT" in up else None
    laser = "front" if "FRONT" in up else "rear" if "REAR" in up else None
    if pos and side is None:
        side = "Left" if pos.endswith("LEFT") else "Right"
    return side, laser, idx


def preferred_laser(axle):
    """Wheel Assembler SRS v1.2: rear lasers measure axles 3 and 5, front the others."""
    return "rear" if axle in (3, 5) else "front"


def axle_num(label):
    """'Axle_3' -> 3; 'Trash' / '' -> None."""
    m = _AXLE_RE.search(str(label or ""))
    return int(m.group(1)) if m else None


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and abs(x) < 1e300 else None


# ----------------------------------------------------------------------------
# containers
# ----------------------------------------------------------------------------
class Groove:
    __slots__ = ("tire", "edge_mm", "depth", "depth_avg", "depth_orig", "std", "n",
                 "bottom", "position", "index")

    def __init__(self, row):
        self.tire = (row.get("tire") or "").strip().lower() or "tire"
        self.edge_mm = row.get("wheel_edge_position")
        self.position = row.get("position")
        md, ad = row.get("median_depth"), row.get("avg_depth")
        mo = row.get("median_original_depth")
        self.depth = abs(md) if md is not None else None
        self.depth_avg = abs(ad) if ad is not None else None
        self.depth_orig = abs(mo) if mo is not None else None
        self.std = row.get("std_depth")
        self.n = int(row.get("n_measurements") or 0)
        self.bottom = bool(row.get("valid_bottom"))
        self.index = 0                     # G number within its tire, set by Measurement


class Measurement:
    """One LMI unit's view of one axle: the algo result + the profiles behind it."""

    def __init__(self, unit, axle):
        self.unit = unit
        self.side, self.laser, self.lmi_index = unit_info(unit)
        self.axle = axle
        self.has_result = False             # an algo folder exists for it
        self.folder = None                  # the algo folder
        self.first = self.last = None       # request.json frame window
        self.grooves = []                   # [Groove], sorted per tire by edge distance
        self.algo_frames = None             # frames the algo processed (from its PNGs)
        self.assigned = {}                  # frame -> host usec (manifest), {} if none
        self.profile_dir = None

    # -- grooves --
    def set_grooves(self, rows):
        gs = [Groove(r) for r in rows if r.get("median_depth") is not None]
        by = defaultdict(list)
        for g in gs:
            by[g.tire].append(g)
        out = []
        for tire in sorted(by, key=_tire_order):
            # outside -> inside. With no edge distance, fall back on the algo position:
            # the wheel edge sits at the HIGH-position end (position + edge distance is
            # constant per tyre on MVT results), so higher position = nearer the edge.
            lst = sorted(by[tire], key=lambda g: (g.edge_mm is None,
                                                  g.edge_mm if g.edge_mm is not None
                                                  else -(g.position or 0.0)))
            for i, g in enumerate(lst, 1):
                g.index = i
            out.extend(lst)
        self.grooves = out

    def tires(self):
        """[(tire, [Groove])] in display order: outer, inner, other."""
        by = defaultdict(list)
        for g in self.grooves:
            by[g.tire].append(g)
        return [(t, by[t]) for t in sorted(by, key=_tire_order)]

    def min_groove(self):
        ok = [g for g in self.grooves if g.depth is not None]
        return min(ok, key=lambda g: g.depth) if ok else None

    # -- frames --
    def frames(self):
        """The frames to plot: what the wheel assembler assigned; else the algo window."""
        if self.assigned:
            return sorted(self.assigned)
        if self.first is not None and self.last is not None:
            return list(range(int(self.first), int(self.last) + 1))
        return []

    def profile_path(self, frame):
        if not self.profile_dir:
            return None
        return os.path.join(self.profile_dir, "frame_%d_profile.csv" % frame)

    @property
    def is_trash(self):
        """The frames the wheel assembler gave no axle (manifest target_axle = Trash)."""
        return self.axle is None

    def label(self):
        return "%s laser (LMI %s)" % ((self.laser or "?").capitalize(),
                                      self.lmi_index if self.lmi_index is not None
                                      else self.unit)


def _tire_order(t):
    return {"outer": 0, "inner": 1}.get(t, 2), t


class Wheel:
    def __init__(self, axle, side):
        self.axle = axle
        self.side = side
        self.meas = []                      # [Measurement]
        self.primary = None
        self.errors = []                    # [(level, text)]

    @property
    def key(self):
        return (self.axle, self.side)

    def name(self):
        return "Axle %d %s" % (self.axle, self.side)

    def is_dual(self):
        m = self.primary
        if m is not None and any(g.tire == "inner" for g in m.grooves):
            return True
        return self.axle != 1


class ScanGrooves:
    def __init__(self, folder):
        self.folder = folder
        self.scan_id = None
        self.axles = []
        self.wheels = {}                    # (axle, side) -> Wheel
        self.notes = []                     # scan-level [(level, text)]
        self.has_manifest = False
        self.has_diag = False
        self.has_profiles = False
        self.groove_root = None
        # every laser frame the manifest lists: unit -> {frame: (usec, delta_s, [axles])}
        self.unit_frames = defaultdict(dict)
        self.trash = {}                     # unit -> Measurement (axle None) of its Trash

    def trash_list(self):
        """Trash measurements, left side first, front before rear."""
        return [self.trash[u] for u in sorted(
            self.trash, key=lambda u: (self.trash[u].side != "Left",
                                       self.trash[u].laser != "front", u))]

    def wheel_list(self):
        return [self.wheels[k] for k in sorted(self.wheels)]

    def measurements(self):
        """Every measurement of every wheel (primary first)."""
        out = []
        for w in self.wheel_list():
            out.extend(sorted(w.meas, key=lambda m: m is not w.primary))
        return out

    def all_errors(self):
        out = list(self.notes)
        for w in self.wheel_list():
            out.extend((lvl, "%s: %s" % (w.name(), txt)) for lvl, txt in w.errors)
        return out


# ----------------------------------------------------------------------------
# readers
# ----------------------------------------------------------------------------
def find_manifest(scan):
    p = os.path.join(scan, MANIFEST_DIR, MANIFEST_NAME)
    if lp_isfile(p):
        return p
    d = os.path.join(scan, MANIFEST_DIR)
    if lp_isdir(d):
        try:
            for n in lp_listdir(d):
                if n.lower().endswith("manifest.csv"):
                    return os.path.join(d, n)
        except OSError:
            pass
    return None


def read_manifest_laser(path):
    """
    Laser profile rows of the manifest -> list of (unit, frame, axle|None, usec, delta_s).
    One row per (file, axle): a frame shared by two axles appears twice (KB trap) and is
    kept twice. Rows with no usable timestamp keep None.
    """
    out = []
    with lp_open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh):
            if (r.get("media_type") or "").strip().lower() != "laser":
                continue
            m = _PROFILE_RE.search(r.get("filename") or "")
            if not m:
                continue                    # the sibling _grooves.csv row
            unit = (r.get("original_subfolder") or "").strip()
            if not unit:
                continue
            out.append((unit, int(m.group(1)), axle_num(r.get("target_axle")),
                        _f(r.get("calculated_host_timestamp_usec")),
                        _f(r.get("delta_from_vehicle_enter_sec"))))
    return out


def algo_frames(folder):
    """Frames the algo processed = its NNNNN_split_segments_f<N>.png files; None if none."""
    try:
        got = {int(m.group(1)) for n in lp_listdir(folder)
               for m in [_SPLIT_RE.search(n)] if m}
    except OSError:
        return None
    return got or None


def _scan_id_from_folder(folder):
    base = os.path.basename(os.path.normpath(folder or ""))
    return base.split("_", 1)[1] if "_" in base else None


# ----------------------------------------------------------------------------
# build
# ----------------------------------------------------------------------------
def load_scan_grooves(scan):
    """Scan folder -> ScanGrooves. Never raises on missing parts: they become notes."""
    sg = ScanGrooves(scan)
    sg.scan_id = _scan_id_from_folder(scan)
    groove_root = os.path.join(scan, RAW_GROOVE_DIRNAME)
    sg.groove_root = groove_root if lp_isdir(groove_root) else None
    sg.has_profiles = sg.groove_root is not None
    sg.has_diag = find_laser_tread_root(scan) is not None

    # ---- wheel-assembler assignment ----
    assigned = defaultdict(dict)            # (unit, axle) -> {frame: usec}
    man = find_manifest(scan)
    if man:
        try:
            rows = read_manifest_laser(man)
            sg.has_manifest = True
        except (OSError, ValueError, csv.Error) as exc:
            rows = []
            sg.notes.append(("error", "Wheel-assembler manifest unreadable (%s)" % exc))
        for unit, frame, axle, usec, delta in rows:
            rec = sg.unit_frames[unit].setdefault(frame, [usec, delta, []])
            rec[2].append(axle)
            if axle is not None:
                assigned[(unit, axle)][frame] = usec

    # ---- algo results ----
    diag = discover_axles(scan) if sg.has_diag else []
    meas = {}
    ids = set()
    for d in diag:
        ax = d.get("axle_index")
        try:
            ax = int(ax)
        except (TypeError, ValueError):
            continue
        m = Measurement(d["lmi_unit"], ax)
        m.has_result = True
        m.folder = d["folder"]
        m.first, m.last = d.get("first_frame"), d.get("last_frame")
        m.set_grooves(d.get("grooves") or [])
        m.algo_frames = algo_frames(d["folder"])
        meas[(m.unit, ax)] = m
        if d.get("scan_id"):
            ids.add(str(d["scan_id"]))

    # ---- wheels ----
    axles = sorted({a for (_u, a) in assigned} | {a for (_u, a) in meas})
    sg.axles = axles
    units_by_side = defaultdict(set)
    for u in set(sg.unit_frames) | {u for (u, _a) in meas}:
        s, _l, _i = unit_info(u)
        if s:
            units_by_side[s].add(u)
    for ax in axles:
        for side in SIDES:
            w = Wheel(ax, side)
            for u in sorted(units_by_side.get(side, ())):
                m = meas.get((u, ax))
                if m is None:
                    continue
                w.meas.append(m)
            if not w.meas:
                # nothing measured this wheel: keep the unit the SRS says should have,
                # with whatever the wheel assembler gave it, so the gap is visible
                want = [u for u in sorted(units_by_side.get(side, ()))
                        if unit_info(u)[1] == preferred_laser(ax)]
                cand = [u for u in want if assigned.get((u, ax))] or want
                if cand:
                    w.meas.append(Measurement(cand[0], ax))
            for m in w.meas:
                m.assigned = dict(assigned.get((m.unit, ax), {}))
                if sg.groove_root:
                    d = os.path.join(sg.groove_root, m.unit)
                    m.profile_dir = d if lp_isdir(d) else None
            with_res = [m for m in w.meas if m.has_result]
            pref = [m for m in with_res if m.laser == preferred_laser(ax)]
            w.primary = (pref or with_res or w.meas or [None])[0]
            sg.wheels[w.key] = w

    # ---- Trash: frames the wheel assembler gave no axle, per laser unit ----
    for unit in sorted(sg.unit_frames):
        tf = {f: rec[0] for f, rec in sg.unit_frames[unit].items() if None in rec[2]}
        if not tf:
            continue
        m = Measurement(unit, None)
        m.assigned = tf
        if sg.groove_root:
            d = os.path.join(sg.groove_root, unit)
            m.profile_dir = d if lp_isdir(d) else None
        sg.trash[unit] = m

    _check(sg, ids)
    return sg


def _check(sg, ids):
    """Scan- and wheel-level errors. Wording is for the reader, not the developer."""
    if not sg.has_profiles:
        sg.notes.append(("error", "No laser profiles in this scan (%s missing)"
                         % RAW_GROOVE_DIRNAME))
    if not sg.has_diag:
        sg.notes.append(("error", "No groove-depth results (diagnostics folder missing)"))
    if not sg.has_manifest:
        sg.notes.append(("warn", "No wheel-assembler manifest - profiles are taken from "
                                 "the algo frame windows and frame timing is unavailable"))
    if sg.scan_id and ids and any(i != sg.scan_id for i in ids):
        sg.notes.append(("error", "Algo results belong to another scan (%s)"
                         % ", ".join(sorted(ids))))

    for w in sg.wheel_list():
        m = w.primary
        if m is None:
            w.errors.append(("error", "no laser measured this wheel"))
            continue
        if not m.has_result:
            if sg.has_diag:                 # no diagnostics at all is ONE scan note
                n = len(m.assigned)
                w.errors.append(("error", "no groove-depth result (%d profile%s "
                                          "assigned)" % (n, "" if n == 1 else "s")))
        elif not m.grooves:
            w.errors.append(("error", "algo found no grooves"))
        if sg.has_manifest and not m.assigned:
            w.errors.append(("error", "wheel assembler assigned no profiles"))
        if m.has_result and m.assigned and m.first is not None and m.last is not None:
            lo, hi = min(m.assigned), max(m.assigned)
            if (lo, hi) != (int(m.first), int(m.last)):
                w.errors.append(("warn", "algo frame window f%d-f%d differs from the "
                                         "assigned frames f%d-f%d"
                                 % (m.first, m.last, lo, hi)))
        lim = depth_limit(w.axle)
        low = [g for g in m.grooves if g.depth is not None and g.depth < lim]
        if low:
            w.errors.append(("warn", "%d groove%s below %s (%.1f mm): %s"
                             % (len(low), "" if len(low) == 1 else "s",
                                '4/32"' if w.axle == 1 else '2/32"', lim,
                                ", ".join("%s G%d %.1f" % (g.tire, g.index, g.depth)
                                          for g in low))))
