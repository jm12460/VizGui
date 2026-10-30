"""Service generation checks use temporary homes and never call systemd."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging/render_server_service.py"


@pytest.fixture
def environment(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path / "home"),
           "XDG_CONFIG_HOME": str(tmp_path / "config"),
           "XDG_CACHE_HOME": str(tmp_path / "cache")}
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def generate(environment, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=environment,
                          cwd=environment["HOME"], capture_output=True, text=True, timeout=20)


def test_checkout_service_uses_explicit_working_directory(tmp_path, environment):
    Path(environment["HOME"]).mkdir()
    checkout = tmp_path / "Media Projects 100%"
    (checkout / "vizgui").mkdir(parents=True)
    (checkout / "vizgui/__init__.py").write_text("")
    # A check that imports the server (instead of resolving it) must fail.
    (checkout / "vizgui/server.py").write_text('raise AssertionError("server imported")\n')
    result = generate(environment, "--checkout", str(checkout))
    assert result.returncode == 0, result.stderr
    assert f'WorkingDirectory={str(checkout).replace("%", "%%")}\n' in result.stdout
    assert "--no-print-pin" in result.stdout
    assert not Path(environment["XDG_CONFIG_HOME"]).exists()
    assert not Path(environment["XDG_CACHE_HOME"]).exists()
    assert not (Path(environment["HOME"]) / ".config").exists()


def test_installed_package_uses_selected_venv_without_checkout(tmp_path, environment):
    Path(environment["HOME"]).mkdir()
    location = tmp_path / "venv with spaces"
    venv.EnvBuilder(with_pip=False).create(location)
    python = location / "bin/python"
    site = subprocess.check_output([str(python), "-c",
                                   "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
    package = Path(site) / "vizgui"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "server.py").write_text('raise AssertionError("server imported")\n')
    result = generate(environment, "--python", str(python))
    assert result.returncode == 0, result.stderr
    assert f'ExecStart="{python}" -m vizgui.server --no-print-pin' in result.stdout
    assert "\nWorkingDirectory=" not in result.stdout
    assert not Path(environment["XDG_CONFIG_HOME"]).exists()


def test_wrong_python_cannot_be_masked_by_checkout_or_pythonpath(tmp_path, environment):
    Path(environment["HOME"]).mkdir()
    location = tmp_path / "empty-venv"
    venv.EnvBuilder(with_pip=False).create(location)
    environment["PYTHONPATH"] = str(ROOT)
    result = generate(environment, "--python", str(location / "bin/python"))
    assert result.returncode != 0
    assert "--checkout" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("args", [("--checkout", "/missing-checkout"),
                                  ("--python", "/missing-python")])
def test_invalid_paths_produce_no_unit(tmp_path, environment, args):
    Path(environment["HOME"]).mkdir()
    result = generate(environment, *args)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Cannot generate VizGui service" in result.stderr


def test_path_escaping_and_directive_injection_rejection():
    spec = importlib.util.spec_from_file_location("service_renderer", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.unit_string('/a "quoted"/100%/$test', command=True) == '"/a \\"quoted\\"/100%%/$test"'
    with pytest.raises(ValueError):
        module.unit_string("/path\nExecStart=/unwanted")


def test_generated_unit_passes_systemd_parser(tmp_path, environment):
    import shutil

    analyzer = shutil.which("systemd-analyze")
    if analyzer is None:
        pytest.skip("systemd-analyze is not installed")
    Path(environment["HOME"]).mkdir()
    checkout = tmp_path / "Media checkout 100%"
    (checkout / "vizgui").mkdir(parents=True)
    (checkout / "vizgui/__init__.py").write_text("")
    (checkout / "vizgui/server.py").write_text('raise AssertionError("server imported")\n')
    python = tmp_path / "Python $literal 100%"
    python.symlink_to(sys.executable)
    rendered = generate(environment, "--checkout", str(checkout), "--python", str(python))
    assert rendered.returncode == 0, rendered.stderr
    unit = tmp_path / "vizgui-test.service"
    unit.write_text(rendered.stdout)
    result = subprocess.run([analyzer, "--user", "verify", str(unit)], env=environment,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert not Path(environment["XDG_CONFIG_HOME"]).exists()
