"""The algo-vs-LMI comparison panel: selectors, embedded plot, table, Excel export.

Parses off the GUI thread (DiagnosticsWorker) behind a BusyOverlay, then for the
selected axle shows a matplotlib view (overview / depth-vs-position / Δ bars / LMI
spread) over a per-groove comparison table, and exports the whole scan to one .xlsx.

Crash-safety (package contract): the embedded canvas uses a CHILD QTimer for resize
reflow (never QTimer.singleShot), toolbar.update() re-seats the nav stack after
fig.clear(), and shutdown() disconnects the worker's signals BEFORE interrupting it.
"""

import os
import traceback

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget
)

from mm_viewer.common import Figure, FigureCanvas, MATPLOTLIB_AVAILABLE, NavigationToolbar
from mm_viewer.widgets.controls import ArrowComboBox
from mm_viewer.widgets.layout import allow_shrink, field_label, SortCell
from mm_viewer.diagnostics import plots, export
from mm_viewer.diagnostics.compare import axle_title
from mm_viewer.diagnostics.loader import _finite
from mm_viewer.diagnostics.worker import DiagnosticsWorker


# plot menu: label -> draw function (None = the stacked overview)
_PLOTS = [
    ("Overview (position + Δ)", None),
    ("Depth vs position", plots.draw_depth_vs_position),
    ("Δ: algo − LMI per groove", plots.draw_delta_bars),
    ("LMI spread vs algo", plots.draw_raw_scatter),
]

# table: (header, key fn, numeric?)
_TCOLS = [
    ("Groove", lambda r: r["label"], False),
    ("Tyre", lambda r: r["tire"], False),
    ("Edge (mm)", lambda r: r["wheel_edge_position"], True),
    ("Pos (mm)", lambda r: r["position"], True),
    ("Algo raw", lambda r: r["algo_orig"], True),
    ("Algo 0.87×", lambda r: r["algo_corr"], True),
    ("Algo σ", lambda r: r["algo_std"], True),
    ("Algo n", lambda r: r["n_measurements"], True),
    ("LMI med", lambda r: r["lmi"]["median"], True),
    ("LMI σ", lambda r: r["lmi"]["std"], True),
    ("LMI n", lambda r: r["lmi"]["n"], True),
    ("Δ raw", lambda r: r["d_orig"], True),
    ("Δ 0.87×", lambda r: r["d_corr"], True),
    ("Valid btm", lambda r: "yes" if r["valid_bottom"] else "no", False),
    ("Occl", lambda r: "yes" if r["occluded"] else "", False),
]


class DiagnosticsComparePanel(QWidget):
    def __init__(self, diag_folder, groove_folder=None, parent=None):
        super().__init__(parent)
        self.diag_folder = diag_folder
        self.groove_folder = groove_folder
        self._cmps = []
        self._scan_id = None
        self._worker = None
        self._closing = False

        # ---- controls ----
        self.axle_combo = ArrowComboBox()
        self.axle_combo.setMinimumWidth(200)
        self.plot_combo = ArrowComboBox()
        for label, _fn in _PLOTS:
            self.plot_combo.addItem(label)
        self.plot_combo.setMinimumWidth(190)
        self.export_btn = QPushButton("Export scan → Excel…")
        self.export_btn.setObjectName("primaryBtn")
        self.export_btn.setEnabled(False)
        self.export_btn.setToolTip(
            "Write one .xlsx for this scan: every axle's comparison table and plots.")
        self.note = QLabel("Reading diagnostics…")
        self.note.setObjectName("hintText")
        allow_shrink(self.note, 160)

        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(20, 10, 20, 2)
        ctrl.setSpacing(10)
        ctrl.addWidget(field_label("Axle"))
        ctrl.addWidget(self.axle_combo)
        ctrl.addSpacing(10)
        ctrl.addWidget(field_label("Plot"))
        ctrl.addWidget(self.plot_combo)
        ctrl.addWidget(self.note, 1)
        ctrl.addWidget(self.export_btn)

        # ---- plot + table ----
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(8)

        if MATPLOTLIB_AVAILABLE:
            plot_box = QWidget()
            pv = QVBoxLayout(plot_box)
            pv.setContentsMargins(6, 0, 6, 0)
            pv.setSpacing(2)
            self.fig = Figure(figsize=(11, 6), dpi=100)
            self.canvas = FigureCanvas(self.fig)
            self.toolbar = NavigationToolbar(self.canvas, self)
            pv.addWidget(self.toolbar)
            pv.addWidget(self.canvas, 1)
            self._reflow = QTimer(self)                 # CHILD timer, never singleShot
            self._reflow.setSingleShot(True)
            self._reflow.timeout.connect(self._redraw_plot)
            self.canvas.mpl_connect("resize_event",
                                    lambda _e: self._reflow.start(120))
            self.split.addWidget(plot_box)
        else:
            self.fig = self.canvas = self.toolbar = None
            lbl = QLabel("matplotlib is not installed — the comparison plots are "
                         "unavailable; the table still works.")
            lbl.setObjectName("placeholder")
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.split.addWidget(lbl)

        self.table = QTableWidget(0, len(_TCOLS))
        self.table.setObjectName("depthTable")
        self.table.setHorizontalHeaderLabels([c[0] for c in _TCOLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.split.addWidget(self.table)
        self.split.setSizes([560, 300])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 6)
        root.setSpacing(4)
        root.addLayout(ctrl)
        root.addWidget(self.split, 1)

        self.axle_combo.currentIndexChanged.connect(self._on_axle)
        self.plot_combo.currentIndexChanged.connect(self._redraw_plot)
        self.export_btn.clicked.connect(self._export)

        # busy overlay sits on top
        self.busy = BusyOverlay(self, subtitle="Diagnostics — please wait")

        self._start_worker()

    # ---------- worker ----------
    def _start_worker(self):
        self.busy.setGeometry(self.rect())
        self.busy.start("Reading diagnostics…")
        self._worker = DiagnosticsWorker(self.diag_folder, self.groove_folder)
        self._worker.phase.connect(self._on_phase)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_phase(self, text):
        if not self._closing:
            self.busy.set_phase(text, force=False)

    def _on_done(self, result):
        if self._closing:
            return
        try:
            self._cmps = result.get("comparisons") or []
            self._scan_id = result.get("scan_id")
            self.axle_combo.blockSignals(True)
            self.axle_combo.clear()
            for c in self._cmps:
                self.axle_combo.addItem(axle_title(c["axle"]))
            self.axle_combo.blockSignals(False)
            self.export_btn.setEnabled(bool(self._cmps))
            if self._cmps:
                self._on_axle(0)
                raw = any(c.get("has_raw") for c in self._cmps)
                self.note.setText(
                    "%d axle%s%s   ·   algo 0.87× = raw × 0.87; Δ = algo − LMI"
                    % (len(self._cmps), "" if len(self._cmps) == 1 else "s",
                       "" if raw else "   ·   no raw LMI found (algo only)"))
            else:
                self.note.setText("No algo groove data found in this diagnostics tree.")
        except Exception:
            traceback.print_exc()
        finally:
            self.busy.stop()

    def _on_failed(self, tb):
        if self._closing:
            return
        self.busy.stop()
        import sys
        sys.stderr.write(tb + "\n")
        self.note.setText("Diagnostics could not be read — see the console.")
        QMessageBox.critical(self, "Diagnostics failed",
                             "The diagnostics could not be read.\n\n"
                             + (tb.strip().splitlines() or ["unknown error"])[-1])

    # ---------- current axle ----------
    def _current(self):
        i = self.axle_combo.currentIndex()
        return self._cmps[i] if 0 <= i < len(self._cmps) else None

    def _on_axle(self, _i=0):
        self._fill_table()
        self._redraw_plot()

    def _fill_table(self):
        cmp = self._current()
        self.table.setSortingEnabled(False)
        rows = cmp["grooves"] if cmp else []
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, (_h, fn, numeric) in enumerate(_TCOLS):
                v = fn(row)
                if numeric and _finite(v):
                    txt = ("%d" % v) if float(v).is_integer() else ("%.2f" % v)
                    item = SortCell(txt, key=v)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                elif numeric:
                    item = SortCell("—", key=float("nan"))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item = QTableWidgetItem("" if v is None else str(v))
                if row["occluded"]:
                    item.setToolTip("Inner (dual) groove — occlusion-limited; the depth "
                                    "is a lower bound, not an accuracy figure.")
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)

    def _redraw_plot(self):
        if not MATPLOTLIB_AVAILABLE or self._closing:
            return
        cmp = self._current()
        try:
            if cmp is None:
                self.fig.clear()
            else:
                draw = _PLOTS[self.plot_combo.currentIndex()][1]
                if draw is None:
                    plots.draw_overview(self.fig, cmp)
                else:
                    draw(self.fig, cmp)
            self.canvas.draw_idle()
            self.toolbar.update()                       # re-seat nav stack after clear()
        except Exception:
            traceback.print_exc()

    # ---------- export ----------
    def _export(self):
        if not self._cmps:
            return
        sid = self._scan_id or "scan"
        safe = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in str(sid))
        start_dir = self.diag_folder
        for up in range(3):                             # suggest the scan root
            parent = os.path.dirname(start_dir.rstrip("\\/"))
            if parent and parent != start_dir:
                start_dir = parent
            if os.path.basename(start_dir).lower() not in (
                    "laser_tread", "diagnostics"):
                break
        default = os.path.join(start_dir, "diagnostics_%s.xlsx" % safe[:40])
        path, _f = QFileDialog.getSaveFileName(
            self, "Export diagnostics comparison", default,
            "Excel workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            self.export_btn.setEnabled(False)
            out = export.export_scan(self._scan_id, self._cmps, path,
                                     scan_folder=self._scan_root())
            QMessageBox.information(
                self, "Export complete",
                "Saved the diagnostics comparison for this scan:\n\n%s\n\n"
                "%d axle sheet%s, each with the table and plots." % (
                    out, len(self._cmps), "" if len(self._cmps) == 1 else "s"))
        except Exception:
            traceback.print_exc()
            QMessageBox.critical(self, "Export failed",
                                 "Could not write the Excel file.\n\n"
                                 + traceback.format_exc().strip().splitlines()[-1])
        finally:
            self.export_btn.setEnabled(bool(self._cmps))

    def _scan_root(self):
        d = self.diag_folder
        for _ in range(3):
            parent = os.path.dirname(d.rstrip("\\/"))
            if os.path.basename(d).lower() in ("laser_tread", "diagnostics"):
                d = parent
            else:
                break
        return d

    # ---------- geometry + teardown ----------
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if getattr(self, "busy", None) is not None and self.busy.isVisible():
            self.busy.setGeometry(self.rect())

    def shutdown(self):
        self._closing = True
        w = self._worker
        self._worker = None
        if w is not None:
            for sig in (w.phase, w.done, w.failed):
                try:
                    sig.disconnect()
                except (TypeError, RuntimeError):
                    pass
            try:
                w.requestInterruption()
                if w.isRunning():
                    w.wait(5000)
            except RuntimeError:
                pass


# Late import: BusyOverlay lives in a module that loads after this one.
from mm_viewer.loading import BusyOverlay  # noqa: E402
