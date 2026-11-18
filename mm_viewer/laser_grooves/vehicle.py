"""Vehicle map of the Laser Grooves tab.

Same look as the Tread mapping / Tire Wall maps (ladder frame, axle hubs, shadows), with
the tyres drawn as tread: one stripe per groove the algo measured, coloured by depth.

  front is up; each axle row = [card L] [outer][inner] ==axle== [inner][outer] [card R]
  steer (axle 1) has one tyre per side, the other axles a dual (inner + outer)
  stripes sit at their measured distance from the wheel edge, outside -> inside
  the card under the profile thumbnail repeats the depths as chips in the SAME order
  and colour as the stripes, so a number is read off the groove it belongs to
  the last row = Trash: profiles the wheel assembler gave no axle, per laser

Colours (display only - not an acceptance verdict), see data.groove_state:
  red     below the detection limit (2/32" = 1.6 mm, steer 4/32" = 3.2 mm)
  amber   1.6 - 3.2 mm (non-steer)
  green   3.2 mm and deeper
  dark    no depth for this tyre
"""

import numpy as np
from PyQt6.QtCore import pyqtSignal, QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QSizePolicy, QToolTip, QWidget

from mm_viewer.theme import C_BORDER, C_ORANGE, C_TEXT, C_TEXT_DIM
from mm_viewer.vehicle_map.diagram import VehicleDiagram
from mm_viewer.laser_grooves.data import (
    depth_limit, groove_state, LIMIT_OTHER_MM, LIMIT_STEER_MM,
)
from mm_viewer.laser_grooves.profiles import decimate, segments

STATE_COLOUR = {"critical": "#e0533d", "marginal": "#f0b429", "ok": "#43a65a",
                "none": "#4a5563"}
STATE_TEXT = {"critical": "#ffffff", "marginal": "#3b2a00", "ok": "#ffffff",
              "none": "#ffffff"}
C_TYRE_D, C_TYRE_L = "#262f3a", "#3e4a59"
C_PROFILE = "#1e5fa8"
C_PROFILE_2 = "#8fb3dc"
TRASH = "trash"


def tyre_grooves(meas):
    """{'outer': [Groove], 'inner': [Groove]} (a single tyre's grooves under 'outer')."""
    out = {"outer": [], "inner": []}
    if meas is None:
        return out
    for tire, gs in meas.tires():
        out["inner" if tire == "inner" else "outer"].extend(gs)
    return out


def wheel_state(wheel):
    """Worst groove state of the wheel's primary measurement: drives its badge."""
    m = wheel.primary
    if m is None or not m.has_result or not m.grooves:
        return "none"
    states = {groove_state(g.depth, wheel.axle) for g in m.grooves}
    for s in ("critical", "marginal", "ok"):
        if s in states:
            return s
    return "none"


def stripe_fracs(grooves):
    """Across-tyre position of each groove, 0 = outside edge, 1 = inside edge."""
    n = len(grooves)
    if not n:
        return []
    edges = [g.edge_mm for g in grooves]
    if n > 1 and all(e is not None for e in edges) and max(edges) > min(edges):
        lo, hi = min(edges), max(edges)
        return [0.18 + 0.64 * (e - lo) / (hi - lo) for e in edges]
    return [(i + 1.0) / (n + 1.0) for i in range(n)]


def thumb_lines(mp, step=2.0):
    """Polylines for a card: the best frame's group, fitted; [(xs, zs, is_best)]."""
    best = mp.best() if mp is not None else None
    if best is None:
        return []
    group = [f for f in mp.frames if best.group is not None and f.group == best.group] \
        or [best]
    out = []
    for f in group:
        x, z = f.fitted_xz()
        x, z = decimate(x, z, step)
        for sx, sz in segments(x, z):
            out.append((sx, sz, f is best))
    return out


def thumb_bounds(lines):
    """(xmin, xmax, zmin, zmax) that frame the tread: the outer 2 % of x and the lowest
    25 % of z (sidewall run-out, specks) are cut so the cross-section fills the card."""
    xs = np.concatenate([l[0] for l in lines])
    zs = np.concatenate([l[1] for l in lines])
    x0, x1 = np.percentile(xs, (1.0, 99.0))
    z0, z1 = np.percentile(zs, (25.0, 99.8))
    return float(x0), float(x1), float(z0), float(z1)


class VehicleMap(QWidget):
    wheelSelected = pyqtSignal(object)          # (axle, side) or ("trash", unit)

    MIN_ROW = 104
    TRASH_H = 46
    TOP_PAD = 34
    LEGEND_H = 24
    TW = 44                                     # tyre width
    TGAP = 5                                    # between the two tyres of a dual
    RAIL_DX = 21                                # chassis rails, as VehicleDiagram
    RAIL_W = 11

    # the Tread / Tire Wall map's own drawing - one look for every vehicle map
    _draw_frame = VehicleDiagram._draw_frame
    _draw_axle = VehicleDiagram._draw_axle
    _draw_shadow = staticmethod(VehicleDiagram._draw_shadow)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sg = None
        self.profiles = {}                      # (unit, axle) -> MeasProfiles
        self._thumbs = {}                       # (unit, axle) -> cached polylines
        self._pix = {}                          # (unit, axle, w, h, dpr) -> QPixmap
        self.selected = None
        self._hover = None
        self._hit = []                          # [(QRectF, key)]
        self._tips = []                         # [(QRectF, text)] groove stripes / chips
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(560)

    # ---- data ----
    def set_scan(self, sg):
        self.sg = sg
        self._hit, self._tips, self._hover = [], [], None   # the old scan's geometry
        self.profiles.clear()
        self._thumbs.clear()
        self._pix.clear()
        self.selected = None
        n = max(1, len(sg.axles) if sg else 1)
        trash = self.TRASH_H if sg is not None and sg.trash else 0
        self.setMinimumHeight(self.TOP_PAD + self.MIN_ROW * n + trash + self.LEGEND_H + 8)
        self.update()

    def set_profiles(self, meas, mp):
        k = (meas.unit, meas.axle)
        self.profiles[k] = mp
        self._thumbs.pop(k, None)
        for pk in [pk for pk in self._pix if pk[0] == k]:
            del self._pix[pk]
        self.update()

    def keys(self):
        if self.sg is None:
            return []
        return sorted(self.sg.wheels) + [(TRASH, u) for u in self.sg.trash]

    def select(self, key, emit=True):
        if self.sg is None or key not in self.keys():
            return
        if key != self.selected:
            self.selected = key
            self.update()
        if emit:
            self.wheelSelected.emit(key)

    # ---- geometry ----
    def _rows(self):
        n = max(1, len(self.sg.axles))
        trash = self.TRASH_H if self.sg.trash else 0
        avail = self.height() - self.TOP_PAD - trash - self.LEGEND_H - 6
        h = max(self.MIN_ROW, avail / n)
        return [(ax, QRectF(8, self.TOP_PAD + i * h, self.width() - 16, h))
                for i, ax in enumerate(self.sg.axles)]

    def _tyre_rects(self, r, axle, side):
        """[(QRectF, 'outer'|'inner')] outside first, for one side of one axle row.
        Steer = one tyre, unless the algo reported inner grooves on it."""
        cx, cy = r.center().x(), r.center().y()
        th = min(r.height() * 0.80, 96.0)
        sign = -1 if side == "Left" else 1
        inner_off = self.RAIL_DX + self.RAIL_W + 22         # beam end, from the centre
        outer_off = inner_off + self.TW + self.TGAP
        w = self.sg.wheels.get((axle, side)) if self.sg is not None else None
        single = axle == 1 and not (w is not None and tyre_grooves(w.primary)["inner"])
        out = []
        for name, off in (("outer", outer_off), ("inner", inner_off)):
            if name == "inner" and single:
                continue
            x = cx + sign * off - (self.TW if sign < 0 else 0)
            out.append((QRectF(x, cy - th / 2, self.TW, th), name))
        return out

    def _card_rect(self, r, side):
        cx = r.center().x()
        edge = self.RAIL_DX + self.RAIL_W + 22 + 2 * self.TW + self.TGAP + 14
        if side == "Left":
            return QRectF(r.left(), r.top() + 4, max(120.0, cx - edge - r.left()),
                          r.height() - 8)
        x = cx + edge
        return QRectF(x, r.top() + 4, max(120.0, r.right() - x), r.height() - 8)

    # ---- painting ----
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            self._paint(p)
        finally:
            p.end()

    def _font(self, px, bold=False):
        f = QFont(self.font())
        f.setPixelSize(px)
        f.setBold(bold)
        return f

    def _paint(self, p):
        self._hit, self._tips = [], []
        if self.sg is None or not self.sg.axles:
            p.setPen(QColor(C_TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "No wheels to show" if self.sg is None or not self.sg.trash
                       else "No axle was measured - see Trash below")
            if self.sg is not None and self.sg.trash:
                self._paint_trash(p, QRectF(8, self.height() - self.LEGEND_H
                                            - self.TRASH_H - 4, self.width() - 16,
                                            self.TRASH_H))
            return
        rows = self._rows()
        cx = rows[0][1].center().x()
        self._draw_direction(p, cx)
        y0 = rows[0][1].top() - 6
        y1 = rows[-1][1].bottom() - 6
        self._draw_frame(p, cx, y0, y1)
        for ax, r in rows:
            tyres = {s: self._tyre_rects(r, ax, s) for s in ("Left", "Right")}
            lin = min(tyres["Left"], key=lambda t: abs(t[0].center().x() - cx))[0]
            rin = min(tyres["Right"], key=lambda t: abs(t[0].center().x() - cx))[0]
            self._draw_axle(p, lin, rin, r.center().y(), cx, ax)
            for side in ("Left", "Right"):
                w = self.sg.wheels.get((ax, side))
                self._paint_tyres(p, w, ax, side, tyres[side])
                if w is not None:
                    self._paint_card(p, w, self._card_rect(r, side), side, tyres[side])
        bottom = rows[-1][1].bottom()
        if self.sg.trash:
            self._paint_trash(p, QRectF(8, bottom + 2, self.width() - 16,
                                        self.TRASH_H - 4))
            bottom += self.TRASH_H
        self._paint_legend(p, QRectF(8, bottom + 2, self.width() - 16, self.LEGEND_H))

    def _draw_direction(self, p, cx):
        p.setFont(self._font(11, True))
        p.setPen(QColor(C_TEXT_DIM))
        p.drawText(QRectF(cx - 60, 13, 120, 14), Qt.AlignmentFlag.AlignCenter, "FRONT")
        tri = QPainterPath()
        tri.moveTo(cx, 2.0)
        tri.lineTo(cx - 7.0, 12.0)
        tri.lineTo(cx + 7.0, 12.0)
        tri.closeSubpath()
        p.fillPath(tri, QBrush(QColor("#9aa8bd")))

    # -- tyres --
    def _paint_tyres(self, p, wheel, axle, side, tyres):
        key = (axle, side)
        sel, hot = key == self.selected, key == self._hover
        tg = tyre_grooves(wheel.primary if wheel is not None else None)
        span = tyres[0][0].united(tyres[-1][0])
        if sel or hot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(232, 121, 30, 70 if sel else 36))
            p.drawRoundedRect(span.adjusted(-6, -6, 6, 6), 13, 13)
        for rect, _n in tyres:
            self._draw_shadow(p, rect)
        for rect, name in tyres:
            self._paint_tyre(p, rect, tg[name], axle, side, wheel, name)
        if wheel is not None:
            self._hit.append((span.adjusted(-4, -4, 4, 4), key))

    def _paint_tyre(self, p, r, grooves, axle, side, wheel, name):
        g = QLinearGradient(r.left(), 0, r.right(), 0)
        g.setColorAt(0.0, QColor(C_TYRE_D))
        g.setColorAt(0.45, QColor(C_TYRE_L))
        g.setColorAt(1.0, QColor(C_TYRE_D))
        p.setPen(QPen(QColor("#1b222b"), 1.2))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(r, 9, 9)
        inner = r.adjusted(4, 7, -4, -7)
        sw = max(3.0, min(5.0, inner.width() / max(4, 2 * len(grooves) + 1)))
        if not grooves:                         # no depth: plain tread, no colour
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(STATE_COLOUR["none"]))
            for fr in (0.25, 0.5, 0.75):
                x = inner.left() + inner.width() * fr
                p.drawRoundedRect(QRectF(x - 1.5, inner.top(), 3.0, inner.height()),
                                  1.5, 1.5)
            if wheel is not None:
                self._tips.append((r, "%s · %s tyre\nno groove depth for this tyre"
                                   % (wheel.name(), name)))
            return
        worst = None
        for fr, gv in zip(stripe_fracs(grooves), grooves):
            if side == "Right":                 # outside edge is on the right there
                fr = 1.0 - fr
            x = inner.left() + inner.width() * fr
            st = groove_state(gv.depth, axle)
            if st == "critical" or (st == "marginal" and worst != "critical"):
                worst = st
            sr = QRectF(x - sw / 2, inner.top(), sw, inner.height())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(STATE_COLOUR[st]))
            p.drawRoundedRect(sr, sw / 2, sw / 2)
            self._tips.append((sr.adjusted(-3, 0, 3, 0), _groove_tip(wheel, gv)))
        n_bad = sum(1 for gv in grooves
                    if groove_state(gv.depth, axle) in ("critical", "marginal")
                    and groove_state(gv.depth, axle) == worst)
        if worst:                               # badge: how many grooves are that bad
            b = QRectF(r.right() - 9, r.top() - 7, 16, 16)
            p.setPen(QPen(QColor("white"), 1.5))
            p.setBrush(QColor(STATE_COLOUR[worst]))
            p.drawEllipse(b)
            p.setFont(self._font(10, True))
            p.setPen(QColor(STATE_TEXT[worst]))
            p.drawText(b, Qt.AlignmentFlag.AlignCenter, str(n_bad))

    # -- cards --
    def _paint_card(self, p, wheel, card, side, tyres):
        sel, hot = wheel.key == self.selected, wheel.key == self._hover
        p.setPen(QPen(QColor(C_ORANGE if sel else "#f2b27a" if hot else C_BORDER),
                      2.5 if sel else 1))
        p.setBrush(QColor("white"))
        p.drawRoundedRect(card, 8, 8)
        self._hit.append((card, wheel.key))
        inner = card.adjusted(8, 4, -8, -4)
        m = wheel.primary
        mp = self.profiles.get((m.unit, m.axle)) if m is not None else None

        # header: laser + profile count
        p.setFont(self._font(11))
        p.setPen(QColor(C_TEXT_DIM))
        head = QRectF(inner.left(), inner.top(), inner.width(), 14)
        if m is not None:
            lab = "%s laser" % (m.laser or "?").capitalize()
            cnt = ("loading…" if mp is None else
                   "%d of %d profiles used" % (mp.n_used, mp.n_assigned))
            p.drawText(head, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       lab)
            p.drawText(head, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       cnt)

        chips_h = 30.0
        prof = QRectF(inner.left(), head.bottom() + 2, inner.width(),
                      max(18.0, inner.height() - 14 - chips_h - 4))
        chips = QRectF(inner.left(), prof.bottom() + 2, inner.width(), chips_h)
        if m is not None and mp is not None:
            self._paint_thumb(p, prof, m, mp)
        msg = self._message(wheel)
        if msg:
            p.setFont(self._font(12, True))
            p.setPen(QColor(STATE_COLOUR["critical"]))
            p.drawText(chips, Qt.AlignmentFlag.AlignCenter, msg)
            return
        self._paint_chips(p, chips, wheel, side, tyres)

    @staticmethod
    def _message(wheel):
        m = wheel.primary
        if m is None:
            return "No laser measured this wheel"
        if not m.has_result:
            return "No groove-depth result"
        if not m.grooves:
            return "Algo found no grooves"
        return ""

    def _paint_chips(self, p, r, wheel, side, tyres):
        """Depth chips per tyre, in the physical left -> right order of the stripes."""
        tg = tyre_grooves(wheel.primary)
        names = [n for _r, n in tyres]          # outside first
        if side == "Right":
            names = names[::-1]                 # left -> right on screen
        blocks = [(n, tg[n]) for n in names]
        n_chips = sum(max(1, len(gs)) for _n, gs in blocks)
        gap_blk = 12.0
        cw = min(40.0, (r.width() - gap_blk * (len(blocks) - 1)) / max(1, n_chips) - 3)
        cw = max(10.0, cw)                      # a narrow window: smaller chips + font,
        total = n_chips * (cw + 3) - 3 + gap_blk * (len(blocks) - 1)
        p.save()                                # never past the card edge
        p.setClipRect(r.adjusted(-2, -2, 2, 2))
        try:
            self._paint_chip_blocks(p, r, wheel, side, blocks, cw, total, gap_blk)
        finally:
            p.restore()

    def _paint_chip_blocks(self, p, r, wheel, side, blocks, cw, total, gap_blk):
        # hug the tyres: right-aligned in the left card, left-aligned in the right one
        x = max(r.left(), r.right() - total) if side == "Left" else r.left()
        cap_f = self._font(9, True)
        chip_f = self._font(12 if cw >= 28 else 10 if cw >= 20 else 8, True)
        for name, gs in blocks:
            k = max(1, len(gs))
            bw = k * (cw + 3) - 3
            p.setFont(cap_f)
            p.setPen(QColor(C_TEXT_DIM))
            if len(blocks) > 1:
                p.drawText(QRectF(x, r.top(), bw, 11), Qt.AlignmentFlag.AlignCenter,
                           name.upper())
            order = gs if side == "Left" else gs[::-1]
            if not gs:
                cr = QRectF(x, r.top() + 12, bw, r.height() - 12)
                p.setPen(QPen(QColor(C_BORDER), 1, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(cr, 5, 5)
                p.setPen(QColor(C_TEXT_DIM))
                p.drawText(cr, Qt.AlignmentFlag.AlignCenter, "—")
                self._tips.append((cr, "%s · %s tyre: no groove depth"
                                   % (wheel.name(), name)))
            for j, g in enumerate(order):
                cr = QRectF(x + j * (cw + 3), r.top() + 12, cw, r.height() - 12)
                st = groove_state(g.depth, wheel.axle)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(STATE_COLOUR[st]))
                p.drawRoundedRect(cr, 5, 5)
                p.setFont(chip_f)
                p.setPen(QColor(STATE_TEXT[st]))
                p.drawText(cr, Qt.AlignmentFlag.AlignCenter,
                           "—" if g.depth is None else "%.1f" % g.depth)
                self._tips.append((cr, _groove_tip(wheel, g)))
            x += bw + gap_blk

    # -- thumbnail --
    def _paint_thumb(self, p, r, m, mp):
        k = (m.unit, m.axle)
        lines = self._thumbs.get(k)
        if lines is None:
            lines = self._thumbs[k] = thumb_lines(mp)
        if not lines:
            p.setFont(self._font(11))
            p.setPen(QColor(STATE_COLOUR["critical"]))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "no usable profile")
            return
        # rendered once per size: a click only moves the selection frame
        dpr = self.devicePixelRatioF()
        pk = (k, int(r.width()), int(r.height()), dpr)
        pm = self._pix.get(pk)
        if pm is None:
            pm = QPixmap(max(1, int(r.width() * dpr)), max(1, int(r.height() * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            try:
                draw_thumb(q, QRectF(0, 0, r.width(), r.height()), lines)
            finally:
                q.end()
            self._pix[pk] = pm
        p.drawPixmap(r.topLeft(), pm)

    # -- trash + legend --
    def _paint_trash(self, p, r):
        p.setFont(self._font(10, True))
        p.setPen(QColor(C_TEXT_DIM))
        cx = r.center().x()
        p.drawText(QRectF(cx - 70, r.top(), 140, r.height()), Qt.AlignmentFlag.AlignCenter,
                   "TRASH\nno axle")
        by_side = {"Left": [], "Right": []}
        for m in self.sg.trash_list():
            by_side.setdefault(m.side or "Left", []).append(m)
        for side, ms in by_side.items():
            if not ms:
                continue
            x0, x1 = (r.left(), cx - 76) if side == "Left" else (cx + 76, r.right())
            w = min(260.0, ((x1 - x0) - 8 * (len(ms) - 1)) / len(ms))
            xs = x1 - len(ms) * (w + 8) + 8 if side == "Left" else x0
            for i, m in enumerate(ms):
                pr = QRectF(xs + i * (w + 8), r.top() + 2, w, r.height() - 4)
                self._paint_trash_pill(p, pr, m)

    def _paint_trash_pill(self, p, r, m):
        key = (TRASH, m.unit)
        sel, hot = key == self.selected, key == self._hover
        mp = self.profiles.get((m.unit, None))
        p.setPen(QPen(QColor(C_ORANGE if sel else "#f2b27a" if hot else C_BORDER),
                      2.5 if sel else 1, Qt.PenStyle.SolidLine if sel else
                      Qt.PenStyle.DashLine))
        p.setBrush(QColor("#f6f8fb"))
        p.drawRoundedRect(r, 8, 8)
        self._hit.append((r, key))
        p.setFont(self._font(11, True))
        p.setPen(QColor(C_TEXT))
        top = QRectF(r.left() + 8, r.top() + 2, r.width() - 16, r.height() / 2 - 1)
        bot = QRectF(r.left() + 8, r.center().y(), r.width() - 16, r.height() / 2 - 2)
        p.drawText(top, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "%s laser" % (m.laser or "?").capitalize())
        p.setFont(self._font(11))
        p.setPen(QColor(C_TEXT_DIM))
        n = len(m.assigned)
        p.drawText(top, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "%d profile%s" % (n, "" if n == 1 else "s"))
        if mp is None:
            txt, col = "checking…", C_TEXT_DIM
        else:
            v = mp.n_data
            txt = "%d valid" % v if v else "no valid profile"
            col = "#b5651d" if v else C_TEXT_DIM
        p.setFont(self._font(11, True))
        p.setPen(QColor(col))
        p.drawText(bot, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)

    def _paint_legend(self, p, r):
        items = (("critical", 'Critical < %.1f mm (steer < %.1f)' % (LIMIT_OTHER_MM,
                                                                     LIMIT_STEER_MM)),
                 ("marginal", "Marginal %.1f – %.1f mm" % (LIMIT_OTHER_MM,
                                                          LIMIT_STEER_MM)),
                 ("ok", "Pass ≥ %.1f mm" % LIMIT_STEER_MM),
                 ("none", "No depth"))
        f = self._font(11)
        fm = QFontMetricsF(f)
        widths = [16 + fm.horizontalAdvance(t) for _s, t in items]
        total = sum(widths) + 22 * (len(items) - 1)
        x = r.center().x() - total / 2
        p.setFont(f)
        for (st, text), w in zip(items, widths):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(STATE_COLOUR[st]))
            p.drawRoundedRect(QRectF(x, r.center().y() - 5, 10, 10), 2, 2)
            p.setPen(QColor(C_TEXT_DIM))
            p.drawText(QRectF(x + 15, r.top(), w, r.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            x += w + 22

    # ---- interaction ----
    def _key_at(self, pos):
        pt = QPointF(pos)
        for r, k in self._hit:
            if r.contains(pt):
                return k
        return None

    def _tip_at(self, pos):
        pt = QPointF(pos)
        for r, t in self._tips:
            if r.contains(pt):
                return t
        return None

    def mouseMoveEvent(self, ev):
        k = self._key_at(ev.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if k else
                       Qt.CursorShape.ArrowCursor)
        if k != self._hover:
            self._hover = k
            self.update()
        tip = self._tip_at(ev.position())
        if tip is None and k is not None:
            if k[0] == TRASH:
                tip = ("Trash: profiles the wheel assembler gave no axle.\n"
                       "Click to check them for valid tread profiles.")
            elif self.sg is not None and k in self.sg.wheels:
                w = self.sg.wheels[k]
                tip = w.name() + ("" if not w.errors else "\n" + "\n".join(
                    t for _l, t in w.errors))
        if tip:
            QToolTip.showText(ev.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, ev):
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        k = self._key_at(ev.position())
        if k is not None:
            self.setFocus()
            self.select(k)

    def keyPressEvent(self, ev):
        if self.sg is None or self.selected is None or self.selected[0] == TRASH \
                or not self.sg.axles:
            return super().keyPressEvent(ev)
        ax, side = self.selected
        axles = self.sg.axles
        i = axles.index(ax) if ax in axles else 0
        key = ev.key()
        if key == Qt.Key.Key_Up and i > 0:
            self.select((axles[i - 1], side))
        elif key == Qt.Key.Key_Down and i < len(axles) - 1:
            self.select((axles[i + 1], side))
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.select((ax, "Left" if key == Qt.Key.Key_Left else "Right"))
        else:
            super().keyPressEvent(ev)


def draw_thumb(p, r, lines):
    """Profiles zoomed onto the tread; vertical exaggeration up to 4x so the grooves
    read at card size (the detail plot keeps the true shape)."""
    xmin, xmax, zmin, zmax = thumb_bounds(lines)
    sx = (r.width() - 4) / max(xmax - xmin, 1e-6)
    sz = (r.height() - 4) / max(zmax - zmin, 1e-6)
    sz = min(sz, 4.0 * sx)
    sx = min(sx, 2.5 * sz)
    ox = r.left() + (r.width() - (xmax - xmin) * sx) / 2
    oz = r.bottom() - (r.height() - (zmax - zmin) * sz) / 2
    p.setClipRect(r)
    for best_first in (False, True):
        for xs, zs, is_best in lines:
            if is_best != best_first:
                continue
            path = QPainterPath()
            path.moveTo(ox + (xs[0] - xmin) * sx, oz - (zs[0] - zmin) * sz)
            for xv, zv in zip(xs[1:], zs[1:]):
                path.lineTo(ox + (xv - xmin) * sx, oz - (zv - zmin) * sz)
            p.setPen(QPen(QColor(C_PROFILE if is_best else C_PROFILE_2),
                          1.6 if is_best else 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)


def _groove_tip(wheel, g):
    lim = depth_limit(wheel.axle)
    st = groove_state(g.depth, wheel.axle)
    bits = ["%s · %s tyre G%d" % (wheel.name(), g.tire, g.index)]
    if g.depth is not None:
        bits.append("depth %.1f mm%s (median of %d frame%s)" % (
            g.depth, "" if g.std is None else " ± %.2f" % g.std, g.n,
            "" if g.n == 1 else "s"))
    if g.edge_mm is not None:
        bits.append("%.0f mm from the wheel edge" % g.edge_mm)
    if st == "critical":
        bits.append('below the %s detection limit (%.1f mm)'
                    % ('4/32"' if wheel.axle == 1 else '2/32"', lim))
    elif st == "marginal":
        bits.append('below 4/32" (%.1f mm)' % LIMIT_STEER_MM)
    return "\n".join(bits)
