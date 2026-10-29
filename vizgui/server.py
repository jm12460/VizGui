"""Headless VizGui: run the existing Engine and phone remote without Qt.

The owner thread runs all engine state transitions. HTTP threads submit commands
through a queue; blocking device calls run in a worker pool and deliver their
completion back to the owner thread. No Qt event loop or QApplication required.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import logging
import queue
import signal
import sys
import threading
import time

from . import config
from .engine import Engine
from .net import local_ip
from .paths import log
from .webremote import start_web_server


class HeadlessController:
    """Single-owner engine driver; call serve() on the main thread."""

    def __init__(self):
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vizgui-io")
        self.engine = Engine(run_async=self.run_async, on_status=self.on_status)
        cast_targets = [t for t in config.CFG.get("targets", []) if t.get("type") == "cast"]
        if not cast_targets:
            raise RuntimeError("No Cast target configured. Configure a TV in the desktop app first.")
        # Preserve the original config indexes used by the phone UI.
        self.engine.set_target_index(config.CFG["targets"].index(cast_targets[0]))
        self.httpd = None

    def on_status(self, message):
        log.info("headless: %s", message)

    def run_async(self, fn, on_done=None):
        """Run device I/O off the engine thread, return results on owner thread."""
        def task():
            try:
                result = fn()
            except Exception as exc:
                log.exception("headless device operation failed")
                result = {"error": str(exc)}
            if on_done is not None and not self.stop_event.is_set():
                self.events.put((on_done, (result,), None))
        self.workers.submit(task)

    def call(self, fn, *args, timeout=15):
        """Run a short engine action on its owner thread; propagate errors."""
        if self.stop_event.is_set():
            raise RuntimeError("Server is stopping")
        result = queue.Queue(maxsize=1)
        self.events.put((fn, args, result))
        try:
            ok, value = result.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("Engine command timed out") from exc
        if not ok:
            raise value
        return value

    # Interface consumed by the existing HTTP handler.
    def web_status(self):
        return self.call(self.engine.web_status)

    def web_history(self):
        return self.call(self.engine.web_history)

    def web_pair(self, pin):
        return self.call(self.engine.web_pair, pin)

    def web_dispatch(self, kind, payload):
        # Commands may schedule device I/O, but never block the HTTP thread on it.
        if kind == "target":
            try:
                idx = int(payload.get("index", -1))
                target = config.CFG.get("targets", [])[idx]
                if idx < 0 or target.get("type") != "cast":
                    return
            except (ValueError, TypeError, IndexError):
                return
        self.call(self.engine.web_do, kind, payload)

    def _process(self, fn, args, response):
        try:
            value = fn(*args)
        except Exception as exc:
            log.exception("engine event failed")
            if response is not None:
                response.put((False, exc))
        else:
            if response is not None:
                response.put((True, value))

    def serve(self, port, print_pin=True):
        self.httpd = start_web_server(self, port)
        log.info("headless server listening on port %s", port)
        print(f"VizGui headless remote: http://{local_ip()}:{port}", flush=True)
        # PIN is printed for initial setup; journal access should be restricted.
        if print_pin:
            print(f"Pairing PIN: {config.CFG['web']['pin']}", flush=True)
        next_tick = next_cast = next_tv = time.monotonic()
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now >= next_tick:
                    self.engine.tick()
                    next_tick = now + 1
                if now >= next_cast:
                    self.engine.poll_cast()
                    next_cast = now + 3
                if now >= next_tv:
                    self.engine.poll_tv()
                    next_tv = now + 5
                wait = min(next_tick, next_cast, next_tv) - time.monotonic()
                try:
                    fn, args, response = self.events.get(timeout=max(0, min(wait, 0.5)))
                except queue.Empty:
                    continue
                self._process(fn, args, response)
        finally:
            self.stop_event.set()
            if self.httpd is not None:
                self.httpd.shutdown()
                self.httpd.server_close()
            self.workers.shutdown(wait=True, cancel_futures=True)
            self.engine.shutdown()
            log.info("headless server stopped")

    def stop(self):
        self.stop_event.set()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run VizGui without a desktop")
    parser.add_argument("--port", type=int, default=None, help="override configured remote port")
    parser.add_argument("--show-pin", action="store_true",
                        help="show pairing PIN and exit")
    parser.add_argument("--no-print-pin", action="store_true",
                        help="hide PIN in startup logs")
    args = parser.parse_args(argv)
    if args.show_pin:
        web = config.CFG.get("web", {})
        print(f"VizGui headless remote: http://{local_ip()}:{web.get('port', 8950)}")
        print(f"Pairing PIN: {web['pin']}")
        return
    if not config.CFG.get("web", {}).get("enabled", True):
        parser.error("Phone remote disabled in config.json; enable it first")
    targets = config.CFG.get("targets", [])
    if not any(t.get("type") == "cast" for t in targets):
        parser.error("No Cast target configured; add one using the desktop app first")
    # Preserve the full configuration: filtering it in memory could cause a later
    # save (e.g. auto-next or add-cast) to delete the user's VLC targets.
    port = args.port if args.port is not None else int(config.CFG["web"].get("port", 8950))
    if not 1 <= port <= 65535:
        parser.error("Port must be between 1 and 65535")
    logging.getLogger("viz-gui").addHandler(logging.StreamHandler(sys.stdout))
    controller = HeadlessController()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _sig, _frame: controller.stop())
    try:
        controller.serve(port, print_pin=not args.no_print_pin)
    except OSError as exc:
        log.error("Could not start headless HTTP server: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
