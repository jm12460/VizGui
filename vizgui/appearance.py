"""Desktop-only display preferences.

Read before QApplication starts. Never imported by the headless server.
Preferences are separate from the shared playback configuration.
"""
import os

from PySide6.QtCore import QSettings

SCALES = ("system", "1", "1.25", "1.5", "1.75", "2")
BACKENDS = ("auto", "wayland", "xcb")


def settings():
    return QSettings("vizgui", "appearance")


def load():
    store = settings()
    scale = str(store.value("scale", "system"))
    backend = str(store.value("backend", "auto"))

    if scale not in SCALES:
        scale = "system"
    if backend not in BACKENDS:
        backend = "auto"

    return scale, backend


def save(scale, backend):
    if scale not in SCALES:
        raise ValueError("Invalid interface scale")
    if backend not in BACKENDS:
        raise ValueError("Invalid display backend")

    store = settings()
    store.setValue("scale", scale)
    store.setValue("backend", backend)
    store.sync()

    if store.status() != QSettings.NoError:
        raise RuntimeError("Could not save appearance preferences")


def apply_startup():
    """Apply preferences before creating QApplication.

    Explicit environment variables take precedence so users can
    override a problematic setting from the terminal.
    """
    scale, backend = load()

    if scale != "system":
        os.environ.setdefault("QT_SCALE_FACTOR", scale)

    # Saved desktop preferences override inherited Qt platform lists
    # such as "wayland;xcb". A VizGui-specific variable provides an
    # intentional per-launch escape hatch for troubleshooting.
    override = os.environ.get("VIZGUI_QPA_PLATFORM")
    selected = override if override in ("wayland", "xcb") else backend

    if selected != "auto":
        os.environ["QT_QPA_PLATFORM"] = selected
