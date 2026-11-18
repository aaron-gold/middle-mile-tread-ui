"""Keep a Python error from killing a Qt app - works with PyQt6 or PySide6 (no Qt import here).

PyQt6 hands an exception raised inside a slot to sys.excepthook, and the DEFAULT hook calls
qFatal(): the process dies at once (exit 0xC0000409), no traceback, no chance to save work
(KB 00_knowledge\\cross-program\\pyqt6-qfatal-deleted-wrapper.md). Replacing the hook keeps the
app alive: the traceback goes to stderr and the user sees a dialog.

    from PyQt6.QtWidgets import QMessageBox           # or PySide6.QtWidgets
    install_exception_guard(lambda title, text: QMessageBox.warning(None, title, text))

Binding choice (PyQt6 for MM, PySide6 for Gen2) is still open (audit C7) - this module does
not care: the caller passes the dialog function.
"""
import sys
import traceback

DEFAULT_TITLE = "Unexpected error"


def error_text(exc_type, exc_value):
    return ("An internal error occurred. The app is still running - the action "
            "that failed was cancelled.\n\n"
            f"{exc_type.__name__}: {exc_value}\n\n"
            "Details were printed to the console.")


def install_exception_guard(show_error, title=DEFAULT_TITLE, stream=None):
    """Replace sys.excepthook. show_error(title, text) shows the dialog; its own errors are ignored."""
    def hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        (stream or sys.stderr).write(text)
        try:
            show_error(title, error_text(exc_type, exc_value))
        except Exception:
            pass
    sys.excepthook = hook
    return hook
