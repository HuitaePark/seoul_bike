"""Check resolved road mounts across preparation and worker Compose configurations.

Only `docker compose config` is used: no engine, containers or road downloads.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def compose_cli():
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker Compose CLI is required for road mount validation")
    result = subprocess.run([docker, "compose", "version", "--short"], capture_output=True, text=True)
    if result.returncode:
        pytest.skip("Docker Compose plugin is required for road mount validation")
    return [docker, "compose", "--env-file", str(ROOT / "infra/.env.example")]


@pytest.mark.parametrize("custom_paths", [False, True], ids=["local-defaults", "external-paths-with-spaces"])
@pytest.mark.parametrize("configuration", ["prepare", "worker"])
def test_preparation_and_worker_use_the_same_persistent_roads(compose_cli, tmp_path, custom_paths, configuration):
    env = dict(os.environ)
    for name in ("OSRM_DATA_DIR", "OSRM_INPUT_DIR"):
        env.pop(name, None)
    base = tmp_path.resolve() / "서울 roads outside checkout"
    inputs, data = base / "source input", base / "processed roads"
    if custom_paths:
        env.update(OSRM_INPUT_DIR=str(inputs), OSRM_DATA_DIR=str(data))
    else:
        inputs = ROOT / "infra/optimizer/data/input"
        data = ROOT / "infra/optimizer/data/processed"
    command = list(compose_cli)
    if configuration == "prepare":
        command += ["-f", str(ROOT / "infra/optimizer/compose.dev.yaml"),
                    "--profile", "prepare", "--profile", "tools"]
    else:
        command += ["-f", str(ROOT / "infra/compose.yaml"),
                    "-f", str(ROOT / "infra/optimizer/compose.osrm.yaml")]
    result = subprocess.run(command + ["config", "--format", "json"], cwd=ROOT,
                            env=env, capture_output=True, text=True, check=True)
    services = json.loads(result.stdout)["services"]
    mounts = {name: {v["target"]: v for v in service.get("volumes", [])}
              for name, service in services.items()}
    assert mounts["osrm"]["/data"]["source"] == str(data)
    assert mounts["osrm"]["/data"]["read_only"] is True
    target = "/data" if configuration == "prepare" else "/roads"
    assert mounts["optimizer"][target]["source"] == str(data)
    assert mounts["optimizer"][target]["read_only"] is True
    if configuration == "prepare":
        assert mounts["osrm-prepare"]["/input"]["source"] == str(inputs)
        assert mounts["osrm-prepare"]["/input"]["read_only"] is True
        assert mounts["osrm-prepare"]["/data"]["source"] == str(data)
        assert not mounts["osrm-prepare"]["/data"].get("read_only", False)
    else:
        # Changing the road directory must not replace the deduplication journal.
        assert mounts["optimizer"]["/state"]["source"] == "optimizer-state"
        assert mounts["optimizer"]["/cache"]["source"] == "optimizer-matrix-cache"
        assert {"backend", "redpanda", "osrm"} <= services["optimizer"]["depends_on"].keys()
        assert not services["osrm"].get("ports")
    if custom_paths:
        assert not inputs.exists() and not data.exists()
