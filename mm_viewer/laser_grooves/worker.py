"""Load every wheel's profiles off the GUI thread.

Two passes so the map fills fast:
  1. read + clean every measurement and emit it ungrouped (a COPY - the GUI never sees
     an object the worker is still changing);
  2. group each measurement (rigid fit, the slow part) and emit it again.
Order: wheel primaries, other lasers, then Trash.

Crash-safe worker contract of this package (see grooves.worker / diagnostics.worker):
unparented QThread, held in a module-level set while it runs, interruption checked
between files; the owner disconnects the signals BEFORE interrupt + wait.
"""

import traceback
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import pyqtSignal, QThread

from mm_viewer.laser_grooves.profiles import group_frames, load_profiles, snapshot

_WORKERS = set()
READ_THREADS = 4


class ProfilesWorker(QThread):
    measReady = pyqtSignal(object, object)      # (Measurement, MeasProfiles)
    progress = pyqtSignal(int, int)
    failed = pyqtSignal(str)

    def __init__(self, measurements):
        super().__init__(None)
        self.meas = list(measurements)
        _WORKERS.add(self)
        self.finished.connect(lambda: _WORKERS.discard(self))

    def run(self):
        pool = ThreadPoolExecutor(max_workers=READ_THREADS,
                                  thread_name_prefix="lg-read")
        stop = self.isInterruptionRequested
        try:
            n = len(self.meas)
            loaded = []
            for m in self.meas:                     # pass 1: read, show ungrouped
                if stop():
                    return
                mp = load_profiles(m, pool.map, stop)
                if mp is None:
                    return
                loaded.append((m, mp))
                self.measReady.emit(m, snapshot(mp))
            for i, (m, mp) in enumerate(loaded, 1):  # pass 2: group, show again
                if stop():
                    return
                group_frames(mp)
                if stop():
                    return
                self.measReady.emit(m, mp)
                self.progress.emit(i, n)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
