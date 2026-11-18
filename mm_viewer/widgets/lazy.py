"""LazyPage: a tab page that builds its real content the first time it is shown.

Loading a scan used to build every sub-tab up front (3.7-5 s with the GUI frozen on an
MVT scan). Only the tab the user lands on has to exist; the rest are built on the first
click. A factory that raises leaves a readable message in the page, never a dead tab.
"""

import sys
import traceback

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


class LazyPage(QWidget):
    def __init__(self, factory, busy_text="Building…", parent=None):
        super().__init__(parent)
        self._factory = factory
        self.content = None
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._wait = QLabel(busy_text)
        self._wait.setObjectName("hintText")
        self._wait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lay.addWidget(self._wait)

    def disarm(self):
        """The page is about to be deleted: never build it."""
        self._factory = None

    def ensure_built(self):
        """Build now (idempotent); returns the content widget or None on failure."""
        if self.content is not None or self._factory is None:
            return self.content
        factory, self._factory = self._factory, None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            w = factory()
        except Exception:
            tb = traceback.format_exc()
            sys.stderr.write(tb)
            self._wait.setText("This view could not be built:\n\n"
                               + tb.strip().splitlines()[-1])
            return None
        finally:
            QApplication.restoreOverrideCursor()
        self._lay.removeWidget(self._wait)
        self._wait.deleteLater()
        self._wait = None
        self._lay.addWidget(w)
        self.content = w
        return w

    def showEvent(self, ev):
        super().showEvent(ev)
        if self.content is None and self._factory is not None:
            self.ensure_built()
