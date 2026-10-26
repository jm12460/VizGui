"""Run work off the Qt UI thread and deliver the result back ON the main thread.

A default/direct signal->callable connection would run the callback on the worker
thread and crash on any widget touch; we pin the signal object to the main thread
and use a queued connection so callbacks may safely touch widgets.
"""
from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import QApplication


class _Sig(QObject):
    done = Signal(object)


POOL = QThreadPool.globalInstance()
_LIVE = set()  # hold signal objects alive until their queued callback fires


class _Task(QRunnable):
    """Run a callable off the UI thread; emit its result on `done`."""
    def __init__(self, fn, sig):
        super().__init__()
        self.fn, self.sig = fn, sig

    def run(self):
        try:
            res = self.fn()
        except Exception as e:
            res = {"error": str(e)}
        if self.sig is not None:
            try:
                self.sig.done.emit(res)
            except RuntimeError:
                pass  # app tearing down; receiver already gone


def run_async(fn, on_done=None):
    sig = None
    if on_done is not None:
        sig = _Sig()
        app = QApplication.instance()
        if app is not None:
            sig.moveToThread(app.thread())
        _LIVE.add(sig)

        def _cb(res, _s=sig):
            try:
                on_done(res)
            finally:
                _LIVE.discard(_s)

        sig.done.connect(_cb, Qt.QueuedConnection)
    POOL.start(_Task(fn, sig))
