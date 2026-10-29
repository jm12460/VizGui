"""Desktop appearance preferences and startup environment handling."""
import pytest

from PySide6.QtCore import QSettings

from vizgui import appearance


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    path = str(tmp_path / "appearance.ini")
    monkeypatch.setattr(
        appearance,
        "settings",
        lambda: QSettings(path, QSettings.IniFormat),
    )


def test_defaults(isolated_settings):
    assert appearance.load() == ("system", "auto")


def test_save_and_reload(isolated_settings):
    appearance.save("1.5", "xcb")
    assert appearance.load() == ("1.5", "xcb")


def test_reject_invalid_settings(isolated_settings):
    with pytest.raises(ValueError):
        appearance.save("banana", "xcb")

    with pytest.raises(ValueError):
        appearance.save("1.5", "banana")


def test_invalid_stored_values_fall_back(isolated_settings):
    store = appearance.settings()
    store.setValue("scale", "invalid")
    store.setValue("backend", "invalid")
    store.sync()

    assert appearance.load() == ("system", "auto")


def test_apply_startup_environment(isolated_settings, monkeypatch):
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)

    appearance.save("1.5", "xcb")
    appearance.apply_startup()

    assert appearance.os.environ["QT_SCALE_FACTOR"] == "1.5"
    assert appearance.os.environ["QT_QPA_PLATFORM"] == "xcb"


def test_explicit_environment_overrides_preferences(
    isolated_settings, monkeypatch
):
    appearance.save("1.5", "xcb")
    monkeypatch.setenv("QT_SCALE_FACTOR", "1.25")
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland")

    appearance.apply_startup()

    assert appearance.os.environ["QT_SCALE_FACTOR"] == "1.25"
    assert appearance.os.environ["QT_QPA_PLATFORM"] == "xcb"


def test_vizgui_specific_platform_override(isolated_settings, monkeypatch):
    appearance.save("1.5", "xcb")
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland;xcb")
    monkeypatch.setenv("VIZGUI_QPA_PLATFORM", "wayland")

    appearance.apply_startup()

    assert appearance.os.environ["QT_QPA_PLATFORM"] == "wayland"


def test_saved_backend_overrides_inherited_platform_list(
    isolated_settings, monkeypatch
):
    appearance.save("1.5", "xcb")
    monkeypatch.delenv("VIZGUI_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland;xcb")

    appearance.apply_startup()

    assert appearance.os.environ["QT_QPA_PLATFORM"] == "xcb"
