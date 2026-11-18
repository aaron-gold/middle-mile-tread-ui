"""Timeline figure: trigger inputs, laser enable and every LMI profile frame on ONE time
axis, one offset row per signal (line-only step traces, never filled).

  * gate pulses matched to an axle are drawn in that axle's colour and labelled A<n>;
    unmatched pulses (skirts, mudflaps) in a contrasting grey
  * profile frames are ticks, coloured by the axle the wheel assembler gave them;
    frames it put in Trash are grey
  * under each measuring unit, a thin bracket = the laser acquisition window of that
    axle (front: gate3 rise -> fall + 400 ms, rear: gate1 rise -> fall + 400 ms)
"""

from mm_viewer.theme import C_NAVY, C_TEXT, C_TEXT_DIM
from mm_viewer.timing.compute import is_gate, is_laser_enable

AXLE_COLOURS = ("#1e5fa8", "#e8791e", "#2e9e5b", "#8e44ad", "#c0392b", "#16a2b8",
                "#7f8c8d", "#b8860b")
C_UNMATCHED = "#b8c2d0"
C_TRASH = "#c3cad6"
ROW_H = 1.0
LEVEL_H = 0.62


def axle_colour(axle):
    return AXLE_COLOURS[(axle - 1) % len(AXLE_COLOURS)] if axle else C_TRASH


def signal_label(name):
    up = name.upper()
    if up == "VEHICLE_ENTER":
        return "Vehicle enter"
    if up == "VEHICLE_EXIT":
        return "Vehicle exit"
    if is_gate(name):
        return "Gate " + name[4:]
    if is_laser_enable(name):
        return "Laser enable"
    return name.replace("TREAD_OUTPUT_", "").replace("OUTPUT_", "").replace("_", " ") \
        .capitalize()


def unit_label(unit):
    up = unit.upper()
    pos = ("FL" if "FRONT_LEFT" in up else "FR" if "FRONT_RIGHT" in up else
           "RL" if "REAR_LEFT" in up else "RR" if "REAR_RIGHT" in up else "")
    parts = unit.split("_")
    n = parts[1] if len(parts) > 1 and parts[1].isdigit() else "?"
    return "LMI %s · %s profiles" % (n, pos) if pos else unit


def timeline_rows(tm, show_all=False):
    """[(kind, name)] top -> bottom."""
    rows = []
    for s in tm.signals:
        up = s.upper()
        core = up in ("VEHICLE_ENTER", "VEHICLE_EXIT") or is_gate(s) or is_laser_enable(s)
        if core or show_all:
            rows.append(("signal", s))
    for u in sorted(tm.frames):
        rows.append(("unit", u))
    return rows


def draw_timeline(fig, tm, show_all=False, xlim=None):
    fig.clear()
    ax = fig.add_axes((0.13, 0.11, 0.85, 0.86))
    if not tm.ok:
        ax.set_axis_off()
        ax.text(0.5, 0.5, "No trigger log", ha="center", va="center", color=C_TEXT_DIM)
        return ax
    rows = timeline_rows(tm, show_all)
    n = len(rows)
    t_end = max([e["t"] for e in tm.events] +
                [t for fr in tm.frames.values() for t, _f, _a in fr] + [0.0])
    t0 = min(0.0, min((e["t"] for e in tm.events), default=0.0))
    owner = {}                                     # id(pulse) -> axle
    for axle, chain in tm.passages.items():
        for p in chain.values():
            owner[id(p)] = axle
    yt, yl = [], []
    for i, (kind, name) in enumerate(rows):
        base = (n - 1 - i) * ROW_H
        yt.append(base + LEVEL_H / 2)
        if kind == "signal":
            yl.append(signal_label(name))
            _step(ax, tm.pulses.get(name, []), base, t0, t_end, owner, name)
        else:
            yl.append(unit_label(name))
            _frames(ax, tm, name, base)
        ax.axhline(base - 0.18, color="#eef2f8", lw=0.8, zorder=0)
    for t in tm.markers.get("scan_entry", []) + tm.markers.get("scan_exit", []):
        ax.axvline(t, color="#2e9e5b", lw=1.0, ls=(0, (5, 3)), zorder=0)
    if tm.scan_start is not None:
        ax.text(tm.scan_start, n * ROW_H - 0.15, " scan start", fontsize=7.5,
                color="#2e9e5b", va="top", clip_on=True)
    if tm.scan_end is not None:
        ax.text(tm.scan_end, n * ROW_H - 0.15, "scan end ", fontsize=7.5,
                color="#2e9e5b", va="top", ha="right", clip_on=True)
    ax.set_yticks(yt)
    ax.set_yticklabels(yl, fontsize=8.5, color=C_TEXT)
    ax.set_ylim(-0.4, n * ROW_H)
    ax.set_xlim(xlim if xlim else (t0 - 0.2, t_end + 0.3))
    ax.set_xlabel("time from scan start [s]", fontsize=8.5, color=C_TEXT_DIM)
    ax.tick_params(axis="x", labelsize=8, colors=C_TEXT_DIM)
    ax.tick_params(axis="y", length=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="x", color="#eef2f8", lw=0.8)
    return ax


def _step(ax, pulses, base, t0, t_end, owner, name):
    """One signal as a line-only step trace; each pulse coloured by its axle."""
    hi = base + LEVEL_H
    last = t0
    for p in pulses:
        r = p.rise if p.rise is not None else t0
        f = p.fall if p.fall is not None else t_end
        axle = owner.get(id(p))
        c = axle_colour(axle) if axle else (C_UNMATCHED if is_gate(name) else C_NAVY)
        ax.plot([last, r], [base, base], color="#7d8a9c", lw=1.0)
        ax.plot([r, r, f, f], [base, hi, hi, base], color=c, lw=1.4 if axle else 1.1)
        if axle and is_gate(name):
            ax.text((r + f) / 2, hi + 0.04, "A%d" % axle, fontsize=7, color=c,
                    ha="center", va="bottom", fontweight="bold", clip_on=True)
        last = f
    ax.plot([last, t_end], [base, base], color="#7d8a9c", lw=1.0)


def _frames(ax, tm, unit, base):
    fr = tm.frames.get(unit, [])
    by_col = {}
    for t, _f, axles in fr:
        a = next((x for x in axles if x), None)
        by_col.setdefault(axle_colour(a), []).append(t)
    for c, ts in by_col.items():
        ax.vlines(ts, base + 0.12, base + LEVEL_H, color=c, lw=1.1,
                  alpha=0.55 if c == C_TRASH else 1.0)
    for r in tm.rows:
        if r.unit != unit or r.pulse is None or r.pulse.rise is None:
            continue
        end = (r.pulse.fall if r.pulse.fall is not None else r.pulse.rise) + \
            r.srs_tail_ms / 1000.0
        c = axle_colour(r.axle)
        ax.plot([r.pulse.rise, end], [base + 0.04, base + 0.04], color=c, lw=2.2,
                alpha=0.45, solid_capstyle="butt")
