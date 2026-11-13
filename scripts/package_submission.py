"""Package committed project files without secrets, caches, or the private assignment.

Run: uv run python scripts/package_submission.py
The output defaults to dist/trialviz-submission.zip. Commit changes before packaging.
"""

import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "trialviz-submission.zip"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().strip()


def main():
    if git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("Commit tracked changes before packaging a reviewable revision.")
    files = filter(None, git("ls-files", "-z").split("\0"))
    OUT.parent.mkdir(exist_ok=True)
    revision = git("rev-parse", "HEAD")
    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            path = Path(name)
            if path.name.startswith(".env") and path.name != ".env.example":
                raise SystemExit(f"Refusing to package environment file: {name}")
            if path.name == "TASK.md" or any(
                part in {".git", ".venv", "dist"} for part in path.parts
            ):
                continue
            archive.write(ROOT / path, f"trialviz/{name}")
        archive.writestr(
            "trialviz/SUBMISSION.json", json.dumps({"code_revision": revision}, indent=2)
        )
    print(f"{OUT} ({OUT.stat().st_size:,} bytes), revision {revision}")


if __name__ == "__main__":
    main()
