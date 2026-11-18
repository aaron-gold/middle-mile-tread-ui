"""Timing model of one scan.  PURE (no Qt).

Clock: triggers_data.csv `Host_Timestamp_usec` and the manifest's
`calculated_host_timestamp_usec` are the SAME host clock (checked on MVT scans: a frame's
calculated time minus the first VEHICLE_ENTER rise == its delta_from_vehicle_enter to
the microsecond). Older logs carry `Host` in ms; then frames are placed by
delta_from_vehicle_enter_sec instead. Every time here is seconds from the scan start
(scan_entry, or the first VEHICLE_ENTER rise if that is earlier).

Gate <-> laser acquisition window (gates per Wheel Assembler SRS v1.2 §4; tail = the
system's acquisition time after the falling edge, 400 ms for both lasers):
  front lasers  window = last gate  (gate3) rise -> fall + 400 ms
  rear lasers   window = first gate (gate1) rise -> fall + 400 ms
"""

import itertools
import re
from collections import defaultdict

from mm_viewer.wheel_assembler.triggers import wa_read_triggers

SRS_FRONT_TAIL_MS = 400.0         # acquisition continues this long after the gate fall
SRS_REAR_TAIL_MS = 400.0
TOL_MS = 5.0                      # edge/frame jitter allowed before a window is flagged
CLOCK_TOL_S = 0.05                # manifest vs trigger clock disagreement worth a note
MAX_GATE_GAP_S = 6.0              # a wheel takes at most this long from one gate to the next
SPEED_SPREAD_MAX = 0.35           # (max-min)/mean of the gate-to-gate gaps of one passage

_GATE_RE = re.compile(r"^gate(\d+)$", re.IGNORECASE)


def is_gate(name):
    return bool(_GATE_RE.match(name or ""))


def gate_no(name):
    m = _GATE_RE.match(name or "")
    return int(m.group(1)) if m else 0


def is_laser_enable(name):
    up = (name or "").upper()
    return "LASER" in up and "ENABLE" in up


class Pulse:
    __slots__ = ("signal", "i", "rise", "fall")

    def __init__(self, signal, i, rise, fall):
        self.signal, self.i, self.rise, self.fall = signal, i, rise, fall

    @property
    def dwell_ms(self):
        if self.rise is None or self.fall is None:
            return None
        return (self.fall - self.rise) * 1000.0


class ProfileRow:
    """One wheel x LMI unit: when its profiles were assigned vs its laser gate."""

    def __init__(self, meas, times):
        self.meas = meas
        self.axle, self.side, self.laser, self.unit = (meas.axle, meas.side, meas.laser,
                                                       meas.unit)
        self.primary = True
        self.times = sorted(times)               # s, the assigned profiles
        self.gate = None
        self.pulse = None
        self.n_laser = None                       # laser-enable pulses in the window

    @property
    def first(self):
        return self.times[0] if self.times else None

    @property
    def last(self):
        return self.times[-1] if self.times else None

    @property
    def lead_ms(self):
        if self.pulse is None or self.first is None or self.pulse.rise is None:
            return None
        return (self.first - self.pulse.rise) * 1000.0

    @property
    def tail_ms(self):
        if self.pulse is None or self.last is None or self.pulse.fall is None:
            return None
        return (self.last - self.pulse.fall) * 1000.0

    @property
    def srs_tail_ms(self):
        return SRS_FRONT_TAIL_MS if self.laser == "front" else SRS_REAR_TAIL_MS

    def flags(self):
        out = []
        if self.lead_ms is not None and self.lead_ms < -TOL_MS:
            out.append("assigned %.0f ms before the %s rise" % (-self.lead_ms, self.gate))
        if self.tail_ms is not None and self.tail_ms > self.srs_tail_ms + TOL_MS:
            out.append("assigned until %.0f ms after the %s fall (window: +%.0f ms)"
                       % (self.tail_ms, self.gate, self.srs_tail_ms))
        return out


class Timing:
    def __init__(self):
        self.ok = False
        self.events = []                          # wa_read_triggers events + "t"
        self.clock = None                         # "usec" | "ms" | "device"
        self.signals = []                         # signal names, display order
        self.pulses = {}                          # signal -> [Pulse]
        self.markers = {}                         # scan_entry/exit -> [t]
        self.gates = []                           # gate names in travel order
        self.scan_start = self.scan_end = None
        self.vehicle_in = self.vehicle_out = None
        self.frames = defaultdict(list)           # unit -> [(t, frame, [axles])]
        self.rows = []                            # [ProfileRow]
        self.passages = {}                        # axle -> {gate: Pulse}
        self.unmatched = []                       # [Pulse] gate pulses with no axle
        self.notes = []                           # [(level, text)]

    @property
    def duration(self):
        if self.scan_start is None or self.scan_end is None:
            return None
        return self.scan_end - self.scan_start

    @property
    def vehicle_time(self):
        if self.vehicle_in is None or self.vehicle_out is None:
            return None
        return self.vehicle_out - self.vehicle_in

    def axle_window(self, axle):
        """(t0, t1) that frames this axle's passage and profiles - for zooming."""
        ts = [p.rise for p in self.passages.get(axle, {}).values() if p.rise is not None]
        ts += [p.fall for p in self.passages.get(axle, {}).values() if p.fall is not None]
        for r in self.rows:
            if r.axle == axle:
                ts += r.times
        return (min(ts), max(ts)) if ts else None


# ----------------------------------------------------------------------------
def _event_us(e, clock):
    if clock == "usec":
        return float(e["host"])
    if clock == "ms":
        return float(e["host"]) * 1000.0
    return e["ts"].timestamp() * 1e6


def build_timing(triggers_csv, sg=None):
    """triggers_data.csv (+ the ScanGrooves for frames/axles) -> Timing. Never raises."""
    tm = Timing()
    try:
        events, warns = wa_read_triggers(triggers_csv)
    except Exception as exc:                        # unreadable / not a CSV
        tm.notes.append(("error", "Trigger log unreadable (%s)" % exc))
        return tm
    for w in warns:
        tm.notes.append(("warn", "Trigger log: " + w))
    if not events:
        tm.notes.append(("error", "Trigger log has no events"))
        return tm
    col = events[0].get("host_col")
    if col and all(e.get("host") is not None for e in events):
        tm.clock = "usec" if col == "Host_Timestamp_usec" else "ms"
    else:
        tm.clock = "device"
    us = [_event_us(e, tm.clock) for e in events]
    order = sorted(range(len(events)), key=lambda i: us[i])
    events = [events[i] for i in order]
    us = [us[i] for i in order]

    starts = [u for e, u in zip(events, us)
              if e["event"] == "scan_entry" or (e["event"] == "VEHICLE_ENTER" and e["rise"])]
    origin = min(starts) if starts else us[0]
    for e, u in zip(events, us):
        e = dict(e)
        e["t"] = (u - origin) / 1e6
        tm.events.append(e)

    # ---- markers + level pulses ----
    by_sig = defaultdict(list)
    for e in tm.events:
        by_sig[e["event"]].append(e)
    for name in ("scan_entry", "scan_exit"):
        tm.markers[name] = [e["t"] for e in by_sig.pop(name, [])]
    for name, evs in by_sig.items():
        ps, up = [], None
        for e in evs:
            if e["rise"]:
                if up is not None:                  # two rises: close the first open
                    ps.append(Pulse(name, len(ps), up, None))
                up = e["t"]
            else:
                ps.append(Pulse(name, len(ps), up, e["t"]))
                up = None
        if up is not None:
            ps.append(Pulse(name, len(ps), up, None))
        tm.pulses[name] = ps
    tm.gates = sorted((s for s in tm.pulses if is_gate(s)), key=gate_no)
    tm.signals = _signal_order(list(tm.pulses))

    ent, ext = tm.markers.get("scan_entry"), tm.markers.get("scan_exit")
    tm.scan_start = min(ent) if ent else None
    tm.scan_end = max(ext) if ext else None
    if tm.scan_start is None:
        tm.notes.append(("warn", "No scan_entry event in the trigger log"))
    if tm.scan_end is None:
        tm.notes.append(("warn", "No scan_exit event - scan duration unknown"))
    ve = [p.rise for p in tm.pulses.get("VEHICLE_ENTER", []) if p.rise is not None]
    vx = [p.fall for p in tm.pulses.get("VEHICLE_EXIT", []) if p.fall is not None]
    tm.vehicle_in = min(ve) if ve else None
    tm.vehicle_out = max(vx) if vx else None
    if not tm.gates:
        tm.notes.append(("error", "No gate events in the trigger log"))
    for g in tm.gates:
        for p in tm.pulses[g]:
            if p.fall is None:
                tm.notes.append(("warn", "%s rose at %.3f s and never fell" % (g, p.rise)))
            elif p.rise is None:
                tm.notes.append(("warn", "%s fell at %.3f s with no rise before it"
                                 % (g, p.fall)))
    tm.ok = True

    if sg is not None:
        _place_frames(tm, sg, origin)
        _profile_rows(tm, sg)
        _passages(tm)
    return tm


def _signal_order(names):
    """Inputs in travel order, then laser enable, then the other outputs."""
    def key(n):
        up = n.upper()
        if up == "VEHICLE_ENTER":
            return (0, 0, n)
        if is_gate(n):
            return (1, gate_no(n), n)
        if up == "VEHICLE_EXIT":
            return (2, 0, n)
        if is_laser_enable(n):
            return (3, 0, n)
        return (4, 0, n)
    return sorted(names, key=key)


def _place_frames(tm, sg, origin):
    """Manifest laser frames onto the timeline (seconds from scan start)."""
    ve = tm.vehicle_in
    diffs = []
    for unit, frames in sg.unit_frames.items():
        for frame, (usec, delta, axles) in frames.items():
            t_clock = (usec - origin) / 1e6 if (usec is not None and tm.clock == "usec") \
                else None
            t_delta = (ve + delta) if (delta is not None and ve is not None) else None
            if t_clock is not None and t_delta is not None:
                diffs.append(abs(t_clock - t_delta))
            tm.frames[unit].append([t_clock, t_delta, frame, list(axles)])
    use_delta = False
    if diffs:
        diffs.sort()
        if diffs[len(diffs) // 2] > CLOCK_TOL_S:
            use_delta = True
            tm.notes.append(("warn", "Manifest frame times disagree with the trigger log "
                                     "by %.0f ms (median) - placed by time from vehicle "
                                     "enter instead" % (diffs[len(diffs) // 2] * 1000)))
    for unit in list(tm.frames):
        out = []
        for t_clock, t_delta, frame, axles in tm.frames[unit]:
            t = t_delta if (use_delta or t_clock is None) else t_clock
            if t is not None:
                out.append((t, frame, axles))
        out.sort()
        tm.frames[unit] = out
    if sg.unit_frames and not any(tm.frames.values()):
        tm.notes.append(("warn", "Manifest frames carry no usable time - profiles are not "
                                 "placed on the timeline"))


def _profile_rows(tm, sg):
    t_of = {u: {f: t for t, f, _a in fr} for u, fr in tm.frames.items()}
    gate_of = {"front": laser_gate(tm, "front"), "rear": laser_gate(tm, "rear")}
    for laser, g in gate_of.items():
        if g is None and any(m.laser == laser for m in sg.measurements()):
            tm.notes.append(("error", "No %s events - %s-laser profiles cannot be timed "
                                      "against their gate" % (SRS_GATE[laser], laser)))
    if not sg.has_manifest:
        tm.notes.append(("warn", "No wheel-assembler manifest - profile times and axle "
                                 "passages are unknown; gate pulses are listed in order"))
    flags = {}                                      # (axle, text) -> [sides]
    for w in sg.wheel_list():
        for m in sorted(w.meas, key=lambda m: m is not w.primary):
            if m is not w.primary and not m.has_result:
                continue
            times = [t_of.get(m.unit, {}).get(f) for f in m.assigned]
            row = ProfileRow(m, [t for t in times if t is not None])
            row.primary = m is w.primary
            row.gate = gate_of.get(m.laser)
            if row.gate and row.times:
                row.pulse = _anchor(tm.pulses.get(row.gate, []), row.times)
                if row.pulse is None:
                    tm.notes.append(("error", "%s %s: no %s pulse around its profiles"
                                     % (w.name(), m.label(), row.gate)))
            if row.pulse is not None and row.pulse.rise is not None:
                hi = row.last if row.last is not None else row.pulse.fall
                row.n_laser = sum(1 for p in tm.pulses.get(_laser_sig(tm), [])
                                  if p.rise is not None and row.pulse.rise <= p.rise <= hi)
            tm.rows.append(row)
            if row.primary:
                for f in row.flags():
                    flags.setdefault((w.axle, f), []).append(w.side)
    # one note per axle when both sides say the same thing
    for (axle, f), sides in sorted(flags.items()):
        who = "Axle %d" % axle + ("" if len(sides) > 1 else " " + sides[0])
        tm.notes.append(("warn", "%s: profiles %s" % (who, f)))


SRS_GATE = {"front": "gate3", "rear": "gate1"}


def laser_gate(tm, laser):
    """The gate that opens this laser's window (SRS v1.2 §4), or None if absent."""
    want = SRS_GATE.get(laser)
    return next((g for g in tm.gates if g.lower() == want), None)


def _laser_sig(tm):
    return next((s for s in tm.pulses if is_laser_enable(s)), None)


def _anchor(pulses, times, early_s=0.05, late_s=0.5):
    """
    The gate pulse a wheel's profiles belong to: the one whose window
    [rise - 50 ms, fall + 500 ms] holds the most of them (ties -> the earlier pulse).
    Counting, not "the pulse before the first profile": a frame the wheel assembler
    also gave the previous axle (shared frames) must not move the anchor.
    None when no pulse holds any profile and none rises near the first one.
    """
    ps = [p for p in pulses if p.rise is not None]
    if not ps or not times:
        return None

    def held(p):
        hi = (p.fall if p.fall is not None else p.rise) + late_s
        return sum(1 for t in times if p.rise - early_s <= t <= hi)

    best = max(ps, key=lambda p: (held(p), -p.rise))
    if held(best):
        return best
    return min(ps, key=lambda p: abs(p.rise - times[0]))


def _passages(tm):
    """
    Which pulse of every gate belongs to each axle.

    Each measured axle has an ANCHOR pulse (its laser gate, from its profiles). The other
    gates' pulses are chosen so the wheel crosses the gates in travel order at a near
    constant speed: candidate rises must increase gate by gate, each gap <= 6 s, and the
    gaps may differ by at most 35 % of their mean. Ambiguity is resolved by the most
    uniform gaps; a pulse is given to one axle only. What is left is reported as
    unmatched (skirts, mudflaps, extra beams).
    """
    anchors = {}
    for r in tm.rows:
        if r.primary and r.pulse is not None and r.axle not in anchors:
            anchors[r.axle] = (r.gate, r.pulse)
    taken = set()
    G = tm.gates
    cand = []
    for axle, (ag, ap) in anchors.items():
        best = _best_chain(tm, G, ag, ap, taken=set())
        cand.append((best[0], axle, ag, ap))
    for _score, axle, ag, ap in sorted(cand, key=lambda c: c[0]):
        score, chain = _best_chain(tm, G, ag, ap, taken)
        tm.passages[axle] = chain
        taken.update(id(p) for p in chain.values())
    if not tm.passages:
        return                                      # nothing to match against
    for g in G:
        for p in tm.pulses.get(g, []):
            if id(p) not in taken:
                tm.unmatched.append(p)
    for p in tm.unmatched:
        tm.notes.append(("info", "%s pulse at %s (%s) not matched to an axle"
                         % (p.signal, _fmt_s(p.rise if p.rise is not None else p.fall),
                            _fmt_ms(p.dwell_ms))))


def _best_chain(tm, G, ag, ap, taken):
    """(score, {gate: Pulse}) for one anchor; gates with no consistent pulse are left out."""
    ai = G.index(ag)
    opts = []
    for gi, g in enumerate(G):
        if gi == ai:
            opts.append([ap])
            continue
        ps = [p for p in tm.pulses.get(g, []) if p.rise is not None and id(p) not in taken]
        if gi < ai:
            ps = [p for p in ps if 0 < ap.rise - p.rise <= MAX_GATE_GAP_S * (ai - gi)]
        else:
            ps = [p for p in ps if 0 < p.rise - ap.rise <= MAX_GATE_GAP_S * (gi - ai)]
        opts.append(ps + [None])
    best = (9.0, {ag: ap})
    for combo in itertools.product(*opts):
        pts = [(gi, p) for gi, p in enumerate(combo) if p is not None]
        rises = [p.rise for _gi, p in pts]
        if any(b <= a for a, b in zip(rises, rises[1:])):
            continue
        gaps = [(b.rise - a.rise) / (gj - gi)
                for (gi, a), (gj, b) in zip(pts, pts[1:])]
        if any(gp > MAX_GATE_GAP_S for gp in gaps):
            continue
        if len(gaps) >= 2:
            spread = (max(gaps) - min(gaps)) / (sum(gaps) / len(gaps))
            if spread > SPEED_SPREAD_MAX:
                continue
        else:
            spread = 0.3                            # one gap: plausible, but unconfirmed
        score = spread + 0.5 * (len(G) - len(pts))  # prefer complete passages
        if score < best[0]:
            best = (score, {G[gi]: p for gi, p in pts})
    return best


def _fmt_s(t):
    return "—" if t is None else "%.3f s" % t


def _fmt_ms(v):
    return "—" if v is None else "%.0f ms" % v
