"""Compare the algo groove depths against the raw LMI firmware depths.

PURE: numeric only, no Qt, no disk (it is handed the structures loader.py produced).

WHY REGISTRATION IS NEEDED
  The raw LMI frame grooves drift laterally frame to frame - the tyre translates as it
  passes the sensor, so the SAME physical groove sits at a different X in every frame
  (verified on 2026-11-03 FL axle 1: a groove moved ~-35 mm per frame pair). The algo's
  `position` is a CONSOLIDATED coordinate after it aligns the frames. So a raw groove
  cannot be matched to an algo groove by absolute X.

  Each raw frame is therefore registered onto the algo's groove positions by the single
  lateral offset that lands the most raw grooves on an algo groove (a 1-D vote over the
  candidate offsets algo_pos - x). That mirrors what the algo itself does and is robust
  to a frame that captured only some of the grooves. A raw groove within `tol` of an
  algo groove after the shift is that groove's reading; frames with < MIN_INLIERS
  aligned are dropped as unreliable and counted, never silently used.

WHAT IS AND IS NOT A FAIR COMPARISON  (KB: mm-lmi-algo-tool-d5-vs-gt)
  * Match by distance from the wheel edge (G1 = nearest edge), never by slot index.
  * Algo `depth` = 0.87 x `original_depth` (a fixed, undocumented scale). Both are shown
    so the factor is visible; the raw LMI firmware depth tracks the algo ORIGINAL depth.
  * DUAL tyres are occlusion-limited: the bottom of the inner grooves is not in the
    laser's line of sight, so a dual reading is a LOWER BOUND, not a depth. Dual grooves
    are flagged; their deltas are repeatability evidence, not accuracy.
  This module computes the comparison and the flags; it asserts no pass/fail.
"""

import math
from statistics import median, mean, pstdev

from mm_viewer.diagnostics.loader import _finite


MIN_INLIERS = 2          # a frame must align this many grooves to be trusted
TOL_MIN_MM = 8.0         # match half-window floor
TOL_MAX_MM = 30.0        # ...and ceiling, so wide gaps don't over-merge grooves


def _tolerance(positions):
    """Half the smallest inter-groove spacing, clamped to [TOL_MIN, TOL_MAX]."""
    ps = sorted(p for p in positions if _finite(p))
    if len(ps) < 2:
        return 20.0
    gaps = [b - a for a, b in zip(ps, ps[1:]) if b - a > 1e-6]
    if not gaps:
        return 20.0
    return max(TOL_MIN_MM, min(TOL_MAX_MM, min(gaps) / 2.0))


def register_offset(xs, positions, tol):
    """
    Lateral offset that maps the most of `xs` onto an algo `positions`, and its inlier
    count. Returns (offset, n_inliers); (0.0, 0) when nothing lines up.
    """
    xs = [x for x in xs if _finite(x)]
    ps = [p for p in positions if _finite(p)]
    if not xs or not ps:
        return 0.0, 0
    best_d, best_n = 0.0, -1
    # candidate offsets: every (algo position - raw x) pairing is a hypothesis
    for p in ps:
        for x in xs:
            d = p - x
            n = 0
            for xx in xs:
                sx = xx + d
                if min(abs(sx - pp) for pp in ps) <= tol:
                    n += 1
            if n > best_n:
                best_d, best_n = d, n
    return best_d, max(best_n, 0)


def _assign(xs_ds, offset, positions, tol):
    """Each (x, depth) -> nearest algo groove index after `offset`, or dropped."""
    ps = positions
    out = {}
    for x, d in xs_ds:
        sx = x + offset
        j, dist = min(((j, abs(sx - p)) for j, p in enumerate(ps)),
                      key=lambda t: t[1])
        if dist <= tol:
            out.setdefault(j, []).append(d)
    return out


def _stats(values):
    vs = [v for v in values if _finite(v)]
    if not vs:
        return {"n": 0, "median": None, "mean": None, "std": None,
                "min": None, "max": None}
    return {"n": len(vs), "median": median(vs), "mean": mean(vs),
            "std": pstdev(vs) if len(vs) > 1 else 0.0,
            "min": min(vs), "max": max(vs)}


def _is_dual(axle):
    """A dual axle has both tyres measured (an 'inner' groove appears)."""
    return any(g.get("tire") == "inner" for g in axle.get("grooves", []))


def compare_axle(axle, raw_frames):
    """
    Build the algo-vs-LMI comparison for one DiagAxle.

    `axle` is a loader.discover_axles() entry; `raw_frames` is
    loader.raw_frames_for_axle(...) = {frame_idx: [(x, depth), ...]}.

    Returns a dict:
      { axle (ref), dual (bool), tol, n_frames, n_frames_used, n_frames_dropped,
        has_raw (bool), grooves: [ compare-row ], tyres: { tire: summary } }

    Each compare-row (one per algo groove, labelled G1.. per tyre by edge distance):
      { label, tire, groove_id, position, wheel_edge_position, valid_bottom,
        occluded (bool), n_measurements,
        algo_corr, algo_orig, algo_std,
        lmi: {n, median, mean, std, min, max}, lmi_values: [...],
        d_orig (algo_orig - lmi_median), d_corr (algo_corr - lmi_median) }
    """
    grooves = list(axle.get("grooves", []))
    dual = _is_dual(axle)
    positions = [g.get("position") for g in grooves]
    tol = _tolerance(positions)

    # register every raw frame onto the algo positions; collect depths per algo groove
    per_groove = {i: [] for i in range(len(grooves))}
    n_used = n_dropped = 0
    need = MIN_INLIERS if len(grooves) >= MIN_INLIERS else 1
    for _idx, pts in sorted(raw_frames.items()):
        xs = [x for x, _d in pts]
        off, inl = register_offset(xs, positions, tol)
        if inl < need:
            n_dropped += 1
            continue
        n_used += 1
        for j, ds in _assign(pts, off, positions, tol).items():
            per_groove[j].extend(ds)

    rows = []
    for i, g in enumerate(grooves):
        lmi_vals = per_groove.get(i, [])
        lmi = _stats(lmi_vals)
        occluded = dual and g.get("tire") == "inner"
        rows.append({
            "label": None,                      # filled after per-tyre ordering
            "tire": g.get("tire") or "",
            "groove_id": g.get("groove_id"),
            "position": g.get("position"),
            "wheel_edge_position": g.get("wheel_edge_position"),
            "valid_bottom": bool(g.get("valid_bottom")),
            "occluded": occluded,
            "n_measurements": g.get("n_measurements"),
            "algo_corr": g.get("depth_corr"),
            "algo_orig": g.get("depth_orig"),
            "algo_std": g.get("std_depth"),
            "lmi": lmi,
            "lmi_values": lmi_vals,
            "d_orig": (_sub(g.get("depth_orig"), lmi["median"])),
            "d_corr": (_sub(g.get("depth_corr"), lmi["median"])),
        })

    _label_by_edge(rows)
    tyres = _tyre_summaries(rows, dual)
    return {
        "axle": axle,
        "dual": dual,
        "tol": tol,
        "has_raw": bool(raw_frames),
        "n_frames": len(raw_frames),
        "n_frames_used": n_used,
        "n_frames_dropped": n_dropped,
        "grooves": rows,
        "tyres": tyres,
    }


def _sub(a, b):
    return (a - b) if (_finite(a) and _finite(b)) else None


def _label_by_edge(rows):
    """
    G1.. per tyre. Ordered by wheel_edge_position ascending (G1 = nearest wheel edge);
    on the units that leave wheel_edge_position empty (the FRONT_RIGHT / LMI_1 sensors
    do, every scan) the tyre falls back to lateral `position` order so a groove still
    gets a stable G-number. The tyre prefix (OUT/INN) is added only on dual axles.
    """
    by_tire = {}
    for r in rows:
        by_tire.setdefault(r["tire"], []).append(r)
    multi = len(by_tire) > 1
    for tire, rs in by_tire.items():
        has_edge = any(_finite(r["wheel_edge_position"]) for r in rs)
        if has_edge:
            rs.sort(key=lambda r: (r["wheel_edge_position"]
                                   if _finite(r["wheel_edge_position"]) else 1e9))
        else:
            rs.sort(key=lambda r: (r["position"]
                                   if _finite(r["position"]) else 1e9))
        for k, r in enumerate(rs, 1):
            r["label"] = ("%s G%d" % (tire[:3].upper(), k)) if (multi and tire) \
                else ("G%d" % k)


def _tyre_summaries(rows, dual):
    """Per-tyre roll-up: counts, mean algo/LMI depth, mean |delta|, occlusion flag."""
    out = {}
    tires = sorted({r["tire"] for r in rows}, key=lambda t: (t != "outer", t))
    for tire in tires:
        rs = [r for r in rows if r["tire"] == tire]
        algo_corr = _stats([r["algo_corr"] for r in rs])
        algo_orig = _stats([r["algo_orig"] for r in rs])
        lmi_med = _stats([r["lmi"]["median"] for r in rs])
        d_orig = _stats([r["d_orig"] for r in rs])
        d_corr = _stats([r["d_corr"] for r in rs])
        out[tire or "tyre"] = {
            "tire": tire,
            "n_grooves": len(rs),
            "n_matched": sum(1 for r in rs if r["lmi"]["n"] > 0),
            "algo_corr_mean": algo_corr["mean"],
            "algo_orig_mean": algo_orig["mean"],
            "lmi_median_mean": lmi_med["mean"],
            "mean_abs_d_orig": (mean([abs(v) for v in _finite_list(
                [r["d_orig"] for r in rs])]) if _any_finite([r["d_orig"] for r in rs])
                else None),
            "mean_abs_d_corr": (mean([abs(v) for v in _finite_list(
                [r["d_corr"] for r in rs])]) if _any_finite([r["d_corr"] for r in rs])
                else None),
            "d_orig": d_orig, "d_corr": d_corr,
            "occluded": dual and tire == "inner",
        }
    return out


def _finite_list(vs):
    return [v for v in vs if _finite(v)]


def _any_finite(vs):
    return any(_finite(v) for v in vs)


def axle_title(axle):
    """'FL · Axle 1' style label for a DiagAxle, used in selectors and sheet names."""
    pos = axle.get("wheel_pos") or axle.get("lmi_unit") or "?"
    short = {"FRONT LEFT": "FL", "FRONT RIGHT": "FR",
             "REAR LEFT": "RL", "REAR RIGHT": "RR"}.get(pos, pos)
    ai = axle.get("axle_index")
    return "%s · Axle %s" % (short, ai if ai is not None else "?")


def axle_key(axle):
    """A short, filesystem-safe key for an axle (Excel sheet names, etc.)."""
    pos = axle.get("wheel_pos") or ""
    short = {"FRONT LEFT": "FL", "FRONT RIGHT": "FR",
             "REAR LEFT": "RL", "REAR RIGHT": "RR"}.get(pos, "U")
    ai = axle.get("axle_index")
    return "%s_Axle%s" % (short, ai if ai is not None else "X")
