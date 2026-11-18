"""Read, clean and group the laser profiles of one measurement.  PURE (numpy/pandas/scipy).

Why group: as the tyre rolls through the laser sheet, every frame cuts it at a slightly
different place, so the raw profiles of one wheel lie as separate, shifted and tilted
bands. Frames of one laser burst cut the tyre almost identically; frames of different
bursts can cut it at a genuinely different shape. So instead of forcing one overlay:

  * frames are fitted rigidly (2-D trimmed ICP) onto their nearest already-fitted
    neighbour, starting from the best algo-used frame -> one GROUP of congruent frames;
  * frames that do not fit start the next group, and so on;
  * the detail plot draws each group overlaid on itself, groups as offset rows on one
    shared axis (no forced fit, nothing hidden).

A frame FITS when its residual is <= FIT_RMS_MM over its best-matching 70 % of points
and >= FIT_OVERLAP of its points land within 2 mm of the target.
Measured on MVT scans: same-burst frames fit at 0.1-1.2 mm / 0.6-1.0; fragments and
different cuts at 1.6-55 mm / 0.03-0.5.
"""

import numpy as np

from mm_viewer.common import lp_open, PANDAS_AVAILABLE, pd
from mm_viewer.laser_grooves.data import MIN_VALID_POINTS

try:
    from scipy.spatial import cKDTree
except Exception:                       # pragma: no cover - scipy is in the venv
    cKDTree = None

SENTINEL_ABS = 1e300
SEG_GAP_MM = 3.0         # a jump bigger than this starts a new segment
SEG_MIN_POINTS = 40      # shorter segments are specks (dust, edge returns): dropped
ISLAND_GAP_MM = 25.0     # a cluster this far from its neighbours is an island...
ISLAND_SHARE = 0.04      # ...dropped when it holds fewer than 4 % of the points
FIT_RMS_MM = 1.5
FIT_OVERLAP = 0.5
MAX_GROUPS = 6           # more cuts than this: the rest stay ungrouped (raw)


class FrameProfile:
    __slots__ = ("frame", "usec", "n_valid", "x", "z", "used", "R", "t", "rms",
                 "overlap", "group")

    def __init__(self, frame, usec, x, z, n_valid, used):
        self.frame, self.usec = frame, usec
        self.x, self.z = x, z                       # cleaned, sensor coordinates (mm)
        self.n_valid = n_valid                      # valid points before cleaning
        self.used = used                            # the algo processed this frame
        self.R, self.t = np.eye(2), np.zeros(2)
        self.rms = self.overlap = None
        self.group = None                           # 0.. after group_frames(), else None

    @property
    def has_data(self):
        return self.n_valid >= MIN_VALID_POINTS and self.x.size >= SEG_MIN_POINTS

    def fitted_xz(self):
        if self.group is None:
            return self.x, self.z
        P = np.c_[self.x, self.z] @ self.R.T + self.t
        return P[:, 0], P[:, 1]


class MeasProfiles:
    """The profiles behind one Measurement, plus the profile-quality counts."""

    def __init__(self, frames, missing):
        self.frames = frames                        # [FrameProfile], frame order
        self.missing = missing                      # assigned frames with no file
        self.groups = []                            # [[FrameProfile]] after grouping
        self.grouped = False

    @property
    def n_assigned(self):
        return len(self.frames) + len(self.missing)

    @property
    def n_data(self):
        return sum(1 for f in self.frames if f.has_data)

    @property
    def n_used(self):
        return sum(1 for f in self.frames if f.used)

    def best(self):
        """The single cleanest frame: algo-used, most points (the thumbnail frame)."""
        good = [f for f in self.frames if f.has_data]
        pool = [f for f in good if f.used] or good
        return max(pool, key=lambda f: f.x.size) if pool else None


def snapshot(mp):
    """An ungrouped copy of mp sharing the (read-only) point arrays: safe to hand to the
    GUI while the worker still groups the original."""
    frames = [FrameProfile(f.frame, f.usec, f.x, f.z, f.n_valid, f.used)
              for f in mp.frames]
    return MeasProfiles(frames, list(mp.missing))


# ----------------------------------------------------------------------------
# reading + cleaning
# ----------------------------------------------------------------------------
def read_xz(path):
    """frame_<N>_profile.csv -> (x, z) float64 of the valid points; None if unreadable."""
    try:
        if PANDAS_AVAILABLE:
            with lp_open(path, "rb") as fh:
                d = pd.read_csv(fh, usecols=["x", "z"], dtype=np.float64, engine="c")
            x, z = d["x"].to_numpy(), d["z"].to_numpy()
        else:                                       # pragma: no cover
            with lp_open(path, "r", encoding="utf-8", errors="replace") as fh:
                a = np.loadtxt(fh, delimiter=",", skiprows=1, usecols=(0, 1), ndmin=2)
            x, z = a[:, 0], a[:, 1]
    except Exception:                       # missing file, empty / truncated / bad header
        return None
    m = np.isfinite(x) & np.isfinite(z) & (np.abs(x) < SENTINEL_ABS) & \
        (np.abs(z) < SENTINEL_ABS)
    return x[m], z[m]


def clean(x, z):
    """
    Drop what is not the tyre:
      specks   runs of < SEG_MIN_POINTS consecutive points (dust, edge returns)
      islands  clusters > ISLAND_GAP_MM away from the rest holding < ISLAND_SHARE of
               the points (a mudflap corner, the fender) - a dual's second tyre is a
               large cluster and stays
    """
    if x.size < SEG_MIN_POINTS:
        return x[:0], z[:0]
    br = np.flatnonzero((np.abs(np.diff(x)) > SEG_GAP_MM) |
                        (np.abs(np.diff(z)) > SEG_GAP_MM)) + 1
    keep = np.zeros(x.size, bool)
    for a, b in zip(np.r_[0, br], np.r_[br, x.size]):
        if b - a >= SEG_MIN_POINTS:
            keep[a:b] = True
    x, z = x[keep], z[keep]
    if x.size == 0:
        return x, z
    br = np.flatnonzero(np.hypot(np.diff(x), np.diff(z)) > ISLAND_GAP_MM) + 1
    if br.size:
        keep = np.zeros(x.size, bool)
        for a, b in zip(np.r_[0, br], np.r_[br, x.size]):
            if b - a >= ISLAND_SHARE * x.size:
                keep[a:b] = True
        x, z = x[keep], z[keep]
    return x, z


def decimate(x, z, step):
    """~one point per `step` mm along the profile (keeps shape, drops density)."""
    if x.size < 3:
        return x, z
    s = np.r_[0.0, np.cumsum(np.hypot(np.diff(x), np.diff(z)))]
    idx = np.unique(np.searchsorted(s, np.arange(0.0, s[-1], step)))
    return x[idx], z[idx]


def segments(x, z, gap=SEG_GAP_MM * 3):
    """Split a profile into drawable runs (a polyline never bridges a real gap)."""
    if x.size == 0:
        return []
    br = np.flatnonzero(np.hypot(np.diff(x), np.diff(z)) > gap) + 1
    return [(x[a:b], z[a:b]) for a, b in zip(np.r_[0, br], np.r_[br, x.size])
            if b - a > 1]


# ----------------------------------------------------------------------------
# grouping (rigid fit)
# ----------------------------------------------------------------------------
def _kabsch(P, Q):
    """Best rigid 2-D fit P -> Q (rotation, no reflection). Closed form: in 2-D the
    Kabsch SVD reduces to one angle, ~10x cheaper per ICP iteration."""
    cp, cq = P.mean(0), Q.mean(0)
    Pc, Qc = P - cp, Q - cq
    a = float(np.einsum("ij,ij->", Pc, Qc))
    b = float(np.dot(Pc[:, 0], Qc[:, 1]) - np.dot(Pc[:, 1], Qc[:, 0]))
    th = np.arctan2(b, a)
    c, s = np.cos(th), np.sin(th)
    R = np.array([[c, -s], [s, c]])
    return R, cq - R @ cp


def _icp(src, tree, ref, t0, iters=25, trim=0.7):
    R, t = np.eye(2), t0.astype(float)
    k = max(10, int(trim * len(src)))
    for _ in range(iters):
        _d, j = tree.query(src @ R.T + t)
        sel = np.argpartition(_d, k - 1)[:k]
        R2, t2 = _kabsch(src[sel], ref[j[sel]])
        done = np.abs(R2 - R).max() < 1e-6 and np.abs(t2 - t).max() < 1e-3
        R, t = R2, t2
        if done:
            break
    d, _ = tree.query(src @ R.T + t)
    rms = float(np.sqrt(np.mean(np.partition(d, k - 1)[:k] ** 2)))
    return R, t, rms, float(np.mean(d < 2.0))


def _fits(r):
    return r[2] <= FIT_RMS_MM and r[3] >= FIT_OVERLAP


def _fit(src, ref_pts, tree):
    """Best rigid fit of src onto ref_pts: centroid start first, then the extremes."""
    best = None
    for a_ref, a_src in ((ref_pts.mean(0), src.mean(0)),
                         (ref_pts[np.argmax(ref_pts[:, 1])], src[np.argmax(src[:, 1])]),
                         (ref_pts[np.argmin(ref_pts[:, 0])], src[np.argmin(src[:, 0])]),
                         (ref_pts[np.argmax(ref_pts[:, 0])], src[np.argmax(src[:, 0])])):
        r = _icp(src, tree, ref_pts, a_ref - a_src)
        if best is None or (r[3], -r[2]) > (best[3], -best[2]):
            best = r
        if _fits(best):
            break
    return best


def group_frames(mp):
    """Fill mp.groups (see module doc). Idempotent; safe without scipy (no grouping)."""
    if mp.grouped:
        return
    mp.grouped = True
    left = [f for f in mp.frames if f.has_data]
    if cKDTree is None:
        return
    targets = {}

    def target(f):
        if f.frame not in targets:
            x, z = f.fitted_xz()
            pts = np.c_[decimate(x, z, 1.5)]
            targets[f.frame] = (pts, cKDTree(pts))
        return targets[f.frame]

    while left and len(mp.groups) < MAX_GROUPS:
        pool = [f for f in left if f.used] or left
        ref = max(pool, key=lambda f: f.x.size)
        gi = len(mp.groups)
        ref.group, ref.rms, ref.overlap = gi, 0.0, 1.0
        members = [ref]
        i0 = left.index(ref)
        for i in sorted(range(len(left)), key=lambda i: (abs(i - i0), i)):
            f = left[i]
            if f is ref:
                continue
            src = np.c_[decimate(f.x, f.z, 3.0)]
            if len(src) < 10:
                continue
            near = min(members, key=lambda g: (abs(g.frame - f.frame), g.frame))
            r = _fit(src, *target(near))
            if not _fits(r) and near is not ref:
                r2 = _fit(src, *target(ref))
                if (r2[3], -r2[2]) > (r[3], -r[2]):
                    r = r2
            if _fits(r) or f.rms is None or r[2] < f.rms:
                f.R, f.t, f.rms, f.overlap = r
            if _fits(r):
                f.group = gi
                members.append(f)
        mp.groups.append(sorted(members, key=lambda f: f.frame))
        left = [f for f in left if f.group is None]
    mp.groups.sort(key=lambda g: g[0].frame)
    for gi, g in enumerate(mp.groups):
        for f in g:
            f.group = gi


def load_profiles(meas, mapper=map, cancelled=lambda: False):
    """
    Measurement -> MeasProfiles (read + clean; grouping is separate), None if cancelled.
    `mapper(fn, items)` is map() or a thread pool's map (pandas releases the GIL).
    """
    pairs = [(n, meas.profile_path(n)) for n in meas.frames()]
    have = [(n, p) for n, p in pairs if p]
    missing = [n for n, p in pairs if not p]
    frames = []
    used_set = meas.algo_frames
    for (n, _p), xz in zip(have, mapper(read_xz, [p for _n, p in have])):
        if cancelled():
            return None
        if xz is None:
            missing.append(n)
            continue
        x, z = xz
        nv = int(x.size)
        x, z = clean(x, z)
        if used_set is not None:
            used = n in used_set
        else:                                   # no algo images: the request window
            used = (meas.first is not None and meas.last is not None
                    and meas.first <= n <= meas.last and nv >= MIN_VALID_POINTS)
        frames.append(FrameProfile(n, meas.assigned.get(n), x, z, nv, used))
    return MeasProfiles(frames, sorted(missing))
