"""Wheel Assembler tab: one page, two views of the same question - how the wheel
assembler cut the scan into axles - picked from a dropdown at the top:

  Triggers & axle split   the wheel-assembler panel (trigger log, axle windows)
  Laser timing            when each wheel's profiles were assigned vs its gate edges

The Timing view is built the first time it is picked.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from mm_viewer.theme import C_NAVY, C_ORANGE
from mm_viewer.widgets.controls import ArrowComboBox
from mm_viewer.widgets.lazy import LazyPage

VIEWS = (
    ("triggers", "Triggers & axle split",
     "Trigger log and the axle windows the wheel assembler cut from it"),
    ("timing", "Laser timing (profiles vs gate edges)",
     "When each wheel's laser profiles were assigned, against its gate rise / fall and "
     "the 400 ms acquisition window"),
)


class WheelAssemblerTab(QWidget):
    def __init__(self, wa_factory, timing_factory, parent=None):
        """wa_factory() -> the wheel-assembler panel; timing_factory() -> the Timing
        view (or an info page saying why there is none)."""
        super().__init__(parent)
        bar = QFrame()
        bar.setObjectName("card")
        bar.setStyleSheet("QFrame#card{border-left:4px solid %s;}" % C_ORANGE)
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(14, 6, 14, 6)
        hl.setSpacing(12)
        lab = QLabel("View")
        lab.setStyleSheet("font-weight:700; color:%s;" % C_NAVY)
        self.view = ArrowComboBox()
        self.view.setMinimumWidth(330)
        for key, text, tip in VIEWS:
            self.view.addItem(text, key)
            self.view.setItemData(self.view.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self.view.setToolTip("Switch between the trigger / axle-split view and the laser "
                             "timing view")
        self.desc = QLabel(VIEWS[0][2])
        self.desc.setObjectName("hintText")
        hl.addWidget(lab)
        hl.addWidget(self.view)
        hl.addWidget(self.desc, 1)

        self.stack = QStackedWidget()
        self.pages = {"triggers": LazyPage(wa_factory, "Reading wheel-assembler triggers…"),
                      "timing": LazyPage(timing_factory, "Reading trigger timing…")}
        for key, _t, _tip in VIEWS:
            self.stack.addWidget(self.pages[key])
        self.view.currentIndexChanged.connect(self._on_view)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 0)
        root.setSpacing(6)
        root.addWidget(bar)
        root.addWidget(self.stack, 1)

    def _on_view(self, i):
        key = self.view.itemData(i)
        self.desc.setText(VIEWS[i][2])
        self.stack.setCurrentWidget(self.pages[key])

    def set_view(self, key):
        for i in range(self.view.count()):
            if self.view.itemData(i) == key:
                self.view.setCurrentIndex(i)
                return
