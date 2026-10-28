"""Windows process lifecycle regressions, runnable on any host."""
import subprocess
from unittest import mock

import pytest

from vizgui.platforms import windows


def test_terminate_waits_for_taskkill_to_finish_the_caster():
    proc = mock.Mock(pid=123, poll=mock.Mock(return_value=None))
    with mock.patch.object(windows.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
        windows._WinCasterHandle(proc).terminate()

    assert run.call_args.args[0] == ["taskkill", "/F", "/T", "/PID", "123"]
    proc.wait.assert_called_once_with(timeout=3)
    proc.kill.assert_not_called()


def test_terminate_falls_back_when_taskkill_returns_nonzero():
    proc = mock.Mock(pid=123, poll=mock.Mock(return_value=None))

    def failed_taskkill(cmd, **kwargs):
        result = subprocess.CompletedProcess(cmd, 1)
        if kwargs.get("check"):
            result.check_returncode()
        return result

    with mock.patch.object(windows.subprocess, "run", side_effect=failed_taskkill):
        windows._WinCasterHandle(proc).terminate()

    proc.kill.assert_called_once_with()
    proc.wait.assert_called_once_with(timeout=3)


@pytest.mark.parametrize("error", [OSError("taskkill unavailable"),
                                  subprocess.TimeoutExpired("taskkill", 5)])
def test_terminate_falls_back_when_taskkill_cannot_run(error):
    proc = mock.Mock(pid=123, poll=mock.Mock(return_value=None))
    with mock.patch.object(windows.subprocess, "run", side_effect=error):
        windows._WinCasterHandle(proc).terminate()

    proc.kill.assert_called_once_with()
    proc.wait.assert_called_once_with(timeout=3)


def test_terminate_falls_back_if_taskkill_does_not_stop_the_caster():
    proc = mock.Mock(pid=123, poll=mock.Mock(return_value=None))
    proc.wait.side_effect = [subprocess.TimeoutExpired("caster", 3), 1]
    with mock.patch.object(windows.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
        windows._WinCasterHandle(proc).terminate()

    proc.kill.assert_called_once_with()
    assert proc.wait.call_args_list == [mock.call(timeout=3), mock.call(timeout=3)]


def test_terminate_does_not_run_taskkill_for_an_exited_caster():
    proc = mock.Mock(pid=123, poll=mock.Mock(return_value=0))
    with mock.patch.object(windows.subprocess, "run") as run:
        windows._WinCasterHandle(proc).terminate()

    run.assert_not_called()
    proc.kill.assert_not_called()


def test_terminate_handles_caster_exiting_during_taskkill_failure():
    proc = mock.Mock(pid=123, poll=mock.Mock(side_effect=[None, 0]))
    with mock.patch.object(windows.subprocess, "run", side_effect=OSError):
        windows._WinCasterHandle(proc).terminate()

    proc.kill.assert_not_called()
    proc.wait.assert_called_once_with(timeout=3)


def test_terminate_logs_failed_fallback_without_crashing(caplog):
    proc = mock.Mock(pid=123, poll=mock.Mock(return_value=None))
    proc.kill.side_effect = OSError("access denied")
    with mock.patch.object(windows.subprocess, "run", side_effect=OSError):
        windows._WinCasterHandle(proc).terminate()

    assert "Could not terminate caster process 123" in caplog.text


@pytest.mark.parametrize("launch", [windows.launch_caster, windows.launch_detached])
def test_launch_closes_log_when_process_creation_fails(tmp_path, launch):
    with mock.patch.object(windows.subprocess, "Popen", side_effect=FileNotFoundError) as popen:
        with pytest.raises(FileNotFoundError):
            launch(["missing executable"], log_path=tmp_path / "caster.log")

    assert popen.call_args.kwargs["stdout"].closed


@pytest.mark.parametrize("launch", [windows.launch_caster, windows.launch_detached])
def test_launch_closes_parent_log_after_success(tmp_path, launch):
    proc = mock.Mock(pid=123)
    args = [r"C:\Program Files\caster.exe", "file with spaces.mp4"]
    with mock.patch.object(windows.subprocess, "Popen", return_value=proc) as popen:
        result = launch(args, log_path=tmp_path / "caster.log")

    assert result.pid == proc.pid
    assert popen.call_args.args == (args,)
    assert popen.call_args.kwargs["stdout"].closed
    assert popen.call_args.kwargs["stderr"] is popen.call_args.kwargs["stdout"]
