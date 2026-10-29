"""Headless mode smoke tests: no display or real Chromecast required."""
from unittest import mock
import threading


def test_webremote_has_no_qt_dependency():
    import sys
    from vizgui import webremote
    assert not hasattr(webremote, "WebBridge")
    assert "PySide6" not in webremote.__dict__


def test_headless_controller_routes_actions_on_owner_thread():
    from vizgui import config
    from vizgui import server
    with mock.patch.object(config, "CFG", {
        "targets": [{"type": "cast", "addr": "127.0.0.1", "name": "Test TV"}],
        "web": {"pin": "123456", "token": "token"},
        "auto_next": True,
    }):
        with mock.patch.object(server.Engine, "set_target_index"):
            controller = server.HeadlessController()
        observed = []
        controller.events.put((lambda: observed.append(threading.get_ident()), (), None))
        fn, args, response = controller.events.get_nowait()
        controller._process(fn, args, response)
        assert observed == [threading.get_ident()]
        controller.stop()
        controller.workers.shutdown(wait=True)


def test_headless_entrypoint_exists():
    from vizgui.server import main
    assert callable(main)
