"""Extract a submission ZIP and verify the documented uv and Docker Compose startup paths.

Run: uv run python scripts/smoke_submission.py [dist/trialviz-submission.zip]
Uses a dummy planner key for HTTP health/schema/static-file checks, never paid API calls.
The extracted copy also runs all offline tests. Requires Docker Desktop to be running.
"""

import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


def command(args, root, **kwargs):
    return subprocess.run(args, cwd=root, check=True, **kwargs)


def verify(base):
    for _ in range(100):
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as r:
                assert json.load(r) == {"status": "ok"}
            break
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    else:
        raise RuntimeError(f"Server never became healthy: {base}")
    with urllib.request.urlopen(base + "/openapi.json") as r:
        schema = json.load(r)
        assert "/query" in schema["paths"]
        assert "TrendRow" in schema["components"]["schemas"]
    with urllib.request.urlopen(base + "/ui/") as r:
        assert b"<html" in r.read().lower()
    with urllib.request.urlopen(base + "/examples/01-trend-with-hint.json") as r:
        assert json.load(r)["response"]["status"] == "ok"


def main():
    archive = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/trialviz-submission.zip").resolve()
    with tempfile.TemporaryDirectory(prefix="trialviz-smoke-") as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            assert not any(Path(n).name == ".env" for n in names)
            bundle.extractall(staging)
        root = staging / "trialviz"
        (root / ".env").write_text("OPENAI_API_KEY=smoke-test-placeholder\n")
        command(["uv", "sync", "--frozen"], root)
        command(["uv", "run", "pytest", "-q"], root)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with (staging / "uv-server.log").open("w") as log:
            server = subprocess.Popen(
                [
                    "uv",
                    "run",
                    "uvicorn",
                    "--factory",
                    "trialviz.api:build_app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=root,
                stdout=log,
                stderr=log,
            )
            try:
                verify(f"http://127.0.0.1:{port}")
                print("uv: health, OpenAPI, UI, and saved examples passed", flush=True)
            finally:
                server.terminate()
                server.wait(timeout=15)
        # Override only the host port so an existing user server is not disturbed.
        override = staging / "ports.yml"
        override.write_text('services:\n  api:\n    ports: !override\n      - "127.0.0.1::8000"\n')
        compose = [
            "docker",
            "compose",
            "-p",
            f"trialviz-smoke-{port}",
            "-f",
            str(root / "docker-compose.yml"),
            "-f",
            str(override),
        ]
        try:
            command([*compose, "up", "--build", "-d"], root)
            address = (
                subprocess.check_output([*compose, "port", "api", "8000"], cwd=root)
                .decode()
                .strip()
            )
            verify(f"http://{address}")
            print("Docker Compose: health, OpenAPI, UI, and saved examples passed", flush=True)
        finally:
            command([*compose, "down", "--rmi", "local"], root)
        print("Clean ZIP smoke checks passed", flush=True)


if __name__ == "__main__":
    main()
