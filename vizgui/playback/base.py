"""The playback-target contract.

A playback target plays files and answers transport commands. The controller
(MainWindow) owns the cross-cutting concerns - the current file, auto-next,
polling, and the UI - and drives the active target through this interface. SmartCast
TV control is intentionally NOT a playback target; it's a separate remote channel.

``status()`` returns a normalized dict:
    {"idle": bool, "busy": bool, "state": str, "pos": int, "dur": int,
     "file": str, "path": str, "live": bool}
- idle: nothing is playing (stopped / finished / unreachable)
- busy: our process is still starting (so an idle reading is just a transient)
- live: a live stream with no seekable duration (indeterminate progress)
"""


class PlaybackTarget:
    name = "target"

    def available(self):
        """-> (ok, message). ok False means a required tool is missing."""
        return True, ""

    def start(self, path, items, auto_next, resume=0):
        """Begin playing `path`, optionally seeking to `resume` seconds. `items` is
        the folder queue from `path` onward (for targets with native playlists).
        Returns "playing" or an error string."""
        raise NotImplementedError

    def stop(self):
        """Deliberate stop (user pressed Stop)."""

    def shutdown(self):
        """Release resources on target switch / app close."""

    def pause(self):
        return True, ""

    def seek(self, secs):
        """Relative seek by `secs` seconds (may be negative)."""
        return True, ""

    def native_skip(self):
        """True if the target advances its own playlist (so skip() handles next/
        prev); False means the controller should load the next/prev file."""
        return False

    def skip(self, direction):
        """Native next (direction=+1) / previous (direction=-1)."""
        return True, ""

    def needs_advance_on_end(self):
        """True if, when playback ends, the controller must load the next file
        (no native playlist). False if the target advances itself."""
        return False

    def status(self):
        return {"idle": True, "busy": False}
