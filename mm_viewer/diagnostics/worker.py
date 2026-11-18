"""Off-GUI-thread parse + compare for the Diagnostics panel.

Follows the package's crash-safe worker contract (grooves.worker):
  * super().__init__(None) - UNPARENTED, so a closing tab never destroys a running
    QThread (that aborts the whole process, exit 0xC0000409).
  * strong-ref module set so a running thread is never garbage-collected.
  * checks isInterruptionRequested() between axles.
The panel disconnects this worker's signals BEFORE it interrupts+waits (see panel
.shutdown()), so a late emit never lands on a destroyed widget.
"""

import traceback
from PyQt6.QtCore import pyqtSignal, QThread

from mm_viewer.diagnostics import loader, compare


_DIAG_WORKERS = set()           # strong refs: a running QThread is never GC'd


class DiagnosticsWorker(QThread):
    """
    Read a diagnostics tree, pair it with the raw LMI grooves, and build the per-axle
    comparisons. Emits:
      done(object) -> {"scan_id": str|None, "comparisons": [cmp, ...],
                       "raw_root": str|None, "has_raw": bool}
      failed(str)  -> a formatted traceback
      phase(str)   -> a short progress caption for the busy overlay
    """
    phase = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, diag_folder, groove_folder=None):
        super().__init__(None)                      # unparented: outlives a closing tab
        self.diag_folder = diag_folder
        self.groove_folder = groove_folder
        _DIAG_WORKERS.add(self)
        self.finished.connect(lambda: _DIAG_WORKERS.discard(self))

    def run(self):
        try:
            self.phase.emit("Reading diagnostics…")
            axles = loader.discover_axles(self.diag_folder)
            # pair with the raw LMI grooves: prefer the model's groove folder, else
            # resolve a tire_laser_groove_data beside the diagnostics tree
            raw_root = self.groove_folder or loader.find_raw_groove_root(
                loader.find_laser_tread_root(self.diag_folder) or self.diag_folder)

            comparisons = []
            for i, a in enumerate(axles, 1):
                if self.isInterruptionRequested():
                    return
                self.phase.emit("Comparing %s (%d/%d)…"
                                % (compare.axle_title(a), i, len(axles)))
                raw = loader.raw_frames_for_axle(
                    raw_root, a["lmi_unit"], a["first_frame"], a["last_frame"])
                comparisons.append(compare.compare_axle(a, raw))

            if self.isInterruptionRequested():
                return
            self.done.emit({
                "scan_id": loader.scan_id_of(axles),
                "comparisons": comparisons,
                "raw_root": raw_root,
                "has_raw": any(c.get("has_raw") for c in comparisons),
            })
        except Exception:
            self.failed.emit(traceback.format_exc())
