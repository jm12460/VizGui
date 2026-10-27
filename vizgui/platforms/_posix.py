"""Shared POSIX (Linux + macOS) process launching.

go-chromecast keeps its in-process HTTP server alive only while it has a
controlling terminal, so it runs on a PTY in its own session; the PTY is drained
to a log so the buffer never stalls it. Both are torn down by killing the whole
process group (go-chromecast + any ffmpeg child).
"""
import os
import pty
import signal
import subprocess
import threading

from .base import CasterHandle


class _PosixCasterHandle(CasterHandle):
    def terminate(self):
        p = self._p
        if p.poll() is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(os.getpgid(p.pid), sig)
                    p.wait(timeout=3)
                    break
                except ProcessLookupError:
                    break
                except Exception:
                    pass


def launch_caster(args, env=None, log_path=None):
    master, slave = pty.openpty()
    p = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave,
                         start_new_session=True, close_fds=True, env=env)
    os.close(slave)

    def _drain():
        try:
            with (open(log_path, "wb") if log_path else open(os.devnull, "wb")) as f:
                while True:
                    data = os.read(master, 4096)
                    if not data:
                        break
                    f.write(data)
                    f.flush()
        except OSError:
            pass
        finally:
            try:
                os.close(master)
            except OSError:
                pass

    threading.Thread(target=_drain, daemon=True).start()
    return _PosixCasterHandle(p)


def launch_detached(args, log_path=None):
    out = open(log_path, "wb") if log_path else subprocess.DEVNULL
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         start_new_session=True)
    if log_path:
        out.close()
    return p
