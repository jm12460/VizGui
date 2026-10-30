"""Multiple desktop/phone clients sharing one isolated headless session.

Uses the real HTTP handler, ServerClient, HeadlessController queue, and Engine.
Only playback-device I/O is fake. No serve() timers, discovery, real devices,
services, or user configuration are involved.
"""
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.request

import pytest
from PySide6.QtWidgets import QApplication

from vizgui import config, engine, history, remote_controller, server, server_identity, webremote
from vizgui.server_client import ServerClient, ServerConnectionError


TOKEN = "multi-client-test-token"


class PlaybackDevice:
    """Thread-safe in-memory device; never launches a process or opens a socket."""

    def __init__(self):
        self.lock = threading.Lock()
        self.position = 30
        self.state = "PLAYING"
        self.operations = []

    def status(self):
        with self.lock:
            return {"idle": False, "state": self.state, "pos": self.position,
                    "dur": 600, "file": "test-movie.mp4"}

    def pause(self):
        with self.lock:
            self.operations.append("playpause")
            self.state = "PAUSED" if self.state == "PLAYING" else "PLAYING"
        return True, ""

    def seek_to(self, seconds):
        with self.lock:
            self.operations.append(("seek", seconds))
            self.position = seconds
        return True, ""

    def stop(self):
        with self.lock:
            self.operations.append("stop")
        return True, ""

    def shutdown(self):
        with self.lock:
            self.operations.append("shutdown")


@pytest.fixture
def session(tmp_path, monkeypatch, request):
    device = PlaybackDevice()
    monkeypatch.setattr(engine, "CastTarget", lambda: device)
    monkeypatch.setattr(engine, "VlcTarget", PlaybackDevice)
    monkeypatch.setattr(config, "CFG", {
        "targets": [{"type": "cast", "name": "Test TV", "addr": "test-device"}],
        "sources": [], "web": {"token": TOKEN}, "auto_next": False,
    })
    monkeypatch.setattr(config, "HOST", None)
    monkeypatch.setattr(config, "set_active_cast", Mock())
    monkeypatch.setattr(engine, "save_config", Mock())
    monkeypatch.setattr(history, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(history, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(history, "CURRENT_PATH", str(tmp_path / "current.json"))
    monkeypatch.setattr(server_identity, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(server_identity, "IDENTITY_PATH", str(tmp_path / "identity.json"))
    # Initialize once before concurrent requests so identity creation is not
    # confused with the client-coordination behavior under test.
    server_identity.server_identity()
    # Allocate the socket before starting threads; failed setup must leave no
    # engine owner running in the background.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webremote._WebHandler)
    request.addfinalizer(httpd.server_close)
    controller = server.HeadlessController()
    request.addfinalizer(lambda: controller.workers.shutdown(wait=True, cancel_futures=True))
    dispatched = []
    web_do = controller.engine.web_do

    def record_dispatch(kind, payload):
        dispatched.append((threading.get_ident(), kind, dict(payload)))
        return web_do(kind, payload)

    monkeypatch.setattr(controller.engine, "web_do", record_dispatch)

    def owner_loop():
        while not controller.stop_event.is_set():
            try:
                fn, args, response = controller.events.get(timeout=0.1)
            except queue.Empty:
                continue
            controller._process(fn, args, response)

    owner = threading.Thread(target=owner_loop, name="test-engine-owner", daemon=True)
    owner.start()

    def stop_owner():
        controller.stop()
        owner.join(timeout=3)
        assert not owner.is_alive()

    request.addfinalizer(stop_owner)

    def seed():
        controller.engine._now_path = str(tmp_path / "test-movie.mp4")
        controller.engine.now_title = "Test Movie"

    controller.call(seed)
    # Bind only loopback and a kernel-selected port. Never use the user's port.
    httpd.win = controller
    httpd.daemon_threads = True
    listener = threading.Thread(target=httpd.serve_forever, daemon=True)
    listener.start()

    def stop_listener():
        httpd.shutdown()
        listener.join(timeout=3)
        assert not listener.is_alive()

    request.addfinalizer(stop_listener)
    port = httpd.server_port

    def client():
        return ServerClient(host="127.0.0.1", port=port, token=TOKEN, timeout=2)

    def phone(path="/api/status", payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=data,
            headers={"X-Viz-Token": TOKEN, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.load(response)

    rig = SimpleNamespace(controller=controller, device=device, owner=owner,
                          first=client(), second=client(), client=client,
                          phone=phone, dispatched=dispatched)
    yield rig


def wait_for(client, **expected):
    deadline = time.monotonic() + 3
    while True:
        status = client.connection_status()
        if all(status["now"][key] == value for key, value in expected.items()):
            return status
        assert time.monotonic() < deadline, (expected, status)


@pytest.fixture
def desktops(session, monkeypatch):
    app = QApplication.instance() or QApplication([])
    pending = []
    monkeypatch.setattr(remote_controller, "run_async",
                        lambda fn, done: pending.append((fn, done)))

    def complete():
        fn, done = pending.pop(0)
        try:
            result = fn()
        except Exception as exc:
            result = {"error": str(exc)}
        done(result)

    first = remote_controller.RemoteController(session.first)
    second = remote_controller.RemoteController(session.second)
    for desktop in (first, second):
        desktop.start()
        desktop.timer.stop()  # responses are advanced explicitly, not by wall clock
        complete()
    yield first, second, pending, complete
    first.close()
    second.close()
    assert app is not None


def test_two_desktops_and_phone_observe_one_session(session):
    a, b = session.first.connection_status(), session.second.connection_status()
    phone = session.phone()
    assert a == b == phone
    assert a["now"]["title"] == "Test Movie"
    assert session.first.sessions()["sessions"] == [
        {"id": "default", "name": "Default", "active": True}]
    assert session.dispatched == []
    assert session.device.operations == []


def test_commands_from_each_client_refresh_all_three_views(session):
    identity = session.first.connection_status()["server"]
    session.first.command_for_server(identity, "transport", action="playpause")
    paused = wait_for(session.second, state="PAUSED")
    assert session.phone()["now"] == paused["now"]
    session.phone("/api/cmd", {"kind": "jump", "secs": 120})
    sought = wait_for(session.first, pos=120)
    assert session.second.status()["now"] == sought["now"]
    session.second.command_for_server(identity, "transport", action="playpause")
    playing = wait_for(session.first, state="PLAYING")
    assert session.phone()["now"] == playing["now"]
    assert session.device.operations == ["playpause", ("seek", 120), "playpause"]
    assert {thread for thread, _, _ in session.dispatched} == {session.owner.ident}


def test_closing_one_desktop_does_not_stop_or_disconnect_others(session, desktops):
    first, second, pending, complete = desktops
    assert first.connected and second.connected
    first.close()
    first.transport("stop")
    first.refresh()
    assert pending == []
    assert session.device.operations == []
    session.phone("/api/cmd", {"kind": "jump", "secs": 90})
    wait_for(session.second, pos=90)
    second.refresh()
    complete()
    assert second.connected
    assert second.snapshot["now"]["pos"] == 90
    assert not first.connected
    assert session.device.operations == [("seek", 90)]
    assert session.controller.stop_event.is_set() is False


def test_one_client_outage_does_not_affect_others_and_reconnect_gets_fresh_state(
        session, desktops, monkeypatch):
    first, second, pending, complete = desktops
    before = dict(first.snapshot["now"])
    original = session.first.connection_status
    monkeypatch.setattr(session.first, "connection_status",
                        Mock(side_effect=ServerConnectionError("simulated client outage")))
    first.refresh()
    complete()
    assert not first.connected
    assert first.snapshot["now"] == before
    first.transport("stop")
    assert pending == []
    session.phone("/api/cmd", {"kind": "jump", "secs": 180})
    wait_for(session.second, pos=180)
    second.refresh()
    complete()
    assert second.connected and second.snapshot["now"]["pos"] == 180
    monkeypatch.setattr(session.first, "connection_status", original)
    first.refresh()
    complete()
    assert first.connected
    assert first.snapshot["now"] == second.snapshot["now"]
    assert session.device.operations == [("seek", 180)]


def test_late_response_to_closed_desktop_cannot_restore_old_state(session, desktops):
    first, second, pending, complete = desktops
    first.refresh()
    fn, done = pending.pop()
    old = fn()  # HTTP response arrives, but its GUI callback hasn't run yet
    first.close()
    session.phone("/api/cmd", {"kind": "jump", "secs": 210})
    wait_for(session.second, pos=210)
    first.start()
    first.timer.stop()
    complete()
    done(old)
    assert first.connected
    assert first.snapshot["now"]["pos"] == 210
    assert session.device.operations == [("seek", 210)]


def test_stale_instance_command_does_not_affect_another_client(session):
    identity = session.first.connection_status()["server"]
    stale = {**identity, "instance_id": "previous-test-process"}
    with pytest.raises(ServerConnectionError):
        session.first.command_for_server(stale, "transport", action="stop")
    assert session.dispatched == []
    session.second.command_for_server(identity, "jump", secs=240)
    status = wait_for(session.first, pos=240)
    assert session.phone()["now"] == status["now"]
    assert session.device.operations == [("seek", 240)]


def test_simultaneous_clients_dispatch_once_on_owner_thread(session):
    barrier = threading.Barrier(3)
    identity = session.first.connection_status()["server"]

    def desktop(client):
        barrier.wait(timeout=3)
        return client.command_for_server(identity, "transport", action="playpause")

    def phone():
        barrier.wait(timeout=3)
        return session.phone("/api/cmd", {"kind": "transport", "action": "playpause"})

    with ThreadPoolExecutor(max_workers=3) as pool:
        requests = [pool.submit(desktop, session.first),
                    pool.submit(desktop, session.second), pool.submit(phone)]
        assert all(request.result(timeout=5)["ok"] for request in requests)
    status = wait_for(session.first, state="PAUSED")  # three toggles from PLAYING
    assert session.second.status()["now"] == session.phone()["now"] == status["now"]
    assert len(session.dispatched) == 3
    assert {thread for thread, _, _ in session.dispatched} == {session.owner.ident}
    assert session.device.operations == ["playpause"] * 3


def test_conflicting_seek_commands_follow_owner_dispatch_order(session):
    """Concurrent arrival has no fixed winner; the last accepted seek wins."""
    barrier = threading.Barrier(3)
    identity = session.first.connection_status()["server"]

    def seek(client, position):
        barrier.wait(timeout=3)
        return client.command_for_server(identity, "jump", secs=position)

    def phone_seek():
        barrier.wait(timeout=3)
        return session.phone("/api/cmd", {"kind": "jump", "secs": 150})

    with ThreadPoolExecutor(max_workers=3) as pool:
        requests = [pool.submit(seek, session.first, 60),
                    pool.submit(seek, session.second, 90), pool.submit(phone_seek)]
        assert all(request.result(timeout=5)["ok"] for request in requests)
    positions = [payload["secs"] for _, kind, payload in session.dispatched if kind == "jump"]
    assert sorted(positions) == [60, 90, 150]
    status = wait_for(session.first, pos=positions[-1])
    assert session.second.status()["now"] == session.phone()["now"] == status["now"]
    assert session.device.operations == [("seek", position) for position in positions]
    assert {thread for thread, _, _ in session.dispatched} == {session.owner.ident}


def test_queued_command_from_closed_client_cannot_interrupt_other_client(session, desktops):
    first, second, pending, complete = desktops
    first.transport("stop")
    send, done = pending.pop()
    first.close()
    second.jump_to(270)
    complete()  # dispatch second client's command; callback schedules its refresh
    wait_for(session.second, pos=270)
    complete()  # fresh desktop snapshot
    done(send())  # first client's worker finally starts after its window closed
    assert not first.connected
    assert second.connected
    assert second.snapshot["now"]["pos"] == 270
    assert session.device.operations == [("seek", 270)]
    assert len(session.dispatched) == 1
