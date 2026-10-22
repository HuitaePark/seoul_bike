"""Deployment selection/preflight with Compose's parser and simulated Docker mutations."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "infra/optimizer/deploy-settings.sh"


@pytest.fixture(scope="module")
def docker_cli():
    executable = shutil.which("docker")
    if not executable or subprocess.run([executable, "compose", "version"], capture_output=True).returncode:
        pytest.skip("Docker Compose CLI is required; no engine or containers are used")
    return executable


@pytest.fixture
def roads(tmp_path):
    directory = tmp_path.resolve() / "서울 prepared roads"
    directory.mkdir()
    (directory / "data-version").write_text("a" * 64 + "\n")
    (directory / "region.osrm.hsgr").write_bytes(b"fixture CH header, not a real map")
    return directory


def runtime_environment():
    env = dict(os.environ)
    for key in ("OSRM_ENABLED", "OSRM_DATA_DIR", "OSRM_INPUT_DIR", "COMPOSE_FILE", "COMPOSE_PATH_SEPARATOR"):
        env.pop(key, None)
    env["COMPOSE_PROJECT_NAME"] = "optimizer-deploy-test"
    return env


def env_file(tmp_path, enabled=None, data=None, inputs=None):
    values = (ROOT / "infra/.env.example").read_text() + "\nPOSTGRES_PASSWORD=must-not-appear-in-logs\n"
    for name, value in (("OSRM_ENABLED", enabled), ("OSRM_DATA_DIR", data), ("OSRM_INPUT_DIR", inputs)):
        if value is not None:
            values += f"{name}='{value}'\n"
    path = tmp_path / "server.env"
    path.write_text(values)
    return path


def configure(path, env=None):
    return subprocess.run(["/bin/bash", "-c", '''
set -eu
. "$1"
configure_optimizer_deployment "$2" "$3"
printf 'COMPOSE_FILE=%s\nOSRM_ENABLED=%s\nOSRM_DATA_DIR=%s\nOSRM_INPUT_DIR=%s\n' \
    "$COMPOSE_FILE" "$OSRM_ENABLED" "$OSRM_DATA_DIR" "$OSRM_INPUT_DIR"
''', "test-settings", str(SETTINGS), str(ROOT), str(path)], cwd=ROOT,
        env=env or runtime_environment(), capture_output=True, text=True)


def test_default_is_haversine_without_road_data(docker_cli, tmp_path):
    result = configure(env_file(tmp_path))
    assert result.returncode == 0, result.stderr
    assert f"COMPOSE_FILE={ROOT}/infra/compose.yaml\n" in result.stdout
    assert "OSRM_ENABLED=false\n" in result.stdout and "compose.osrm.yaml" not in result.stdout
    assert "must-not-appear-in-logs" not in result.stdout + result.stderr


def test_dotenv_paths_and_shell_override_use_the_same_overlay(docker_cli, tmp_path, roads):
    inputs = tmp_path / "source input"
    inputs.mkdir()
    # Docker Compose handles quoted dotenv values; shell variables take precedence.
    path = env_file(tmp_path, "true", roads, inputs)
    result = configure(path)
    assert result.returncode == 0, result.stderr
    assert f"COMPOSE_FILE={ROOT}/infra/compose.yaml:{ROOT}/infra/optimizer/compose.osrm.yaml" in result.stdout
    assert f"OSRM_DATA_DIR={roads}\n" in result.stdout
    assert f"OSRM_INPUT_DIR={inputs}\n" in result.stdout
    env = runtime_environment()
    env["OSRM_ENABLED"] = "false"
    result = configure(path, env)
    assert result.returncode == 0 and "OSRM_ENABLED=false\n" in result.stdout
    assert "compose.osrm.yaml" not in result.stdout
    assert "must-not-appear-in-logs" not in result.stdout + result.stderr


@pytest.mark.parametrize("problem,expected", [
    ("bad_flag", "OSRM_ENABLED"), ("missing_directory", "absolute directory"),
    ("relative_directory", "absolute directory"), ("absent_directory", "does not exist"),
    ("inside_checkout", "outside the checkout"), ("symlink_into_checkout", "outside the checkout"),
    ("bad_input", "OSRM_INPUT_DIR"), ("missing_version", "data-version"),
    ("bad_version", "SHA-256"), ("missing_ch", "region.osrm.hsgr"),
])
def test_bad_road_settings_fail_preflight(docker_cli, tmp_path, roads, problem, expected):
    enabled, data, inputs = "true", roads, None
    if problem == "bad_flag": enabled = "enabled"
    elif problem == "missing_directory": data = ""
    elif problem == "relative_directory": data = "relative/roads"
    elif problem == "absent_directory": data = tmp_path / "absent"
    elif problem == "inside_checkout": data = ROOT / "infra"
    elif problem == "symlink_into_checkout":
        data = tmp_path / "link"
        data.symlink_to(ROOT / "infra", target_is_directory=True)
    elif problem == "bad_input": inputs = ROOT / "infra"
    elif problem == "missing_version": (roads / "data-version").unlink()
    elif problem == "bad_version": (roads / "data-version").write_text("unfinished\n")
    elif problem == "missing_ch": (roads / "region.osrm.hsgr").unlink()
    result = configure(env_file(tmp_path, enabled, data, inputs))
    assert result.returncode != 0 and expected in result.stderr
    assert "must-not-appear-in-logs" not in result.stdout + result.stderr


def fake_docker(tmp_path, executable):
    binary = tmp_path / "bin"
    binary.mkdir()
    path = binary / "docker"
    path.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
with open(os.environ["DOCKER_CALLS"], "a") as log:
    log.write(json.dumps({"args": sys.argv[1:], "compose": os.environ.get("COMPOSE_FILE")}) + "\\n")
# Read-only configuration uses the real Compose parser. Every mutation is simulated.
if sys.argv[1] == "compose" and "config" in sys.argv[2:]:
    os.execv(os.environ["REAL_DOCKER"], [os.environ["REAL_DOCKER"], *sys.argv[1:]])
sys.exit(0)
''')
    path.chmod(0o755)
    env = runtime_environment()
    env.update(PATH=str(binary) + os.pathsep + env["PATH"], REAL_DOCKER=executable,
               DOCKER_CALLS=str(tmp_path / "docker-calls.jsonl"))
    return env


@pytest.mark.parametrize("enabled", ["false", "true"])
def test_deploy_uses_selected_config_and_waits_for_roads_first(docker_cli, tmp_path, roads, enabled):
    env = fake_docker(tmp_path, docker_cli)
    result = subprocess.run(["/bin/bash", str(ROOT / "infra/deploy.sh"),
                             str(env_file(tmp_path, enabled, roads))], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in Path(env["DOCKER_CALLS"]).read_text().splitlines()]
    changes = [c for c in calls if c["args"][0] == "compose" and "up" in c["args"]]
    assert changes
    expected = str(ROOT / "infra/compose.yaml")
    if enabled == "true": expected += ":" + str(ROOT / "infra/optimizer/compose.osrm.yaml")
    assert all(c["compose"] == expected for c in changes)
    if enabled == "true":
        osrm_start = next(i for i, c in enumerate(changes) if c["args"][-1] == "osrm")
        backend_start = next(i for i, c in enumerate(changes) if c["args"][-1] == "backend")
        assert osrm_start < backend_start and "--wait" in changes[osrm_start]["args"]
        assert any("optimizer" in c["args"] and "osrm" in c["args"] for c in changes)
    else:
        assert all("osrm" not in c["args"] for c in changes)
    assert "must-not-appear-in-logs" not in result.stdout + result.stderr


def test_invalid_roads_stop_before_any_container_change(docker_cli, tmp_path):
    env = fake_docker(tmp_path, docker_cli)
    result = subprocess.run(["/bin/bash", str(ROOT / "infra/deploy.sh"),
                             str(env_file(tmp_path, "true", tmp_path / "missing"))],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    calls = [json.loads(line)["args"] for line in Path(env["DOCKER_CALLS"]).read_text().splitlines()]
    assert all(c[0] == "compose" and "config" in c for c in calls)
