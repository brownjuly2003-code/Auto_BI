"""Publish the demo to its Hugging Face Space as a fresh white-list snapshot.

Usage:  HF_TOKEN=hf_...  python deploy/hf-demo/publish_space.py [workdir]

The Space repo is a git tree REPLACE (never an additive upload): every tracked
file under the white-list below, the demo Dockerfile copied to the root, and the
Space's own README.md (HF front-matter) kept as-is. Publishing only `git ls-files`
content is what keeps internal notes and untracked scratch out of the public host.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SPACE = os.environ.get("HF_SPACE", "JuLioMe20/auto-bi-demo")
SPACE_USER = SPACE.split("/")[0]
REPO = Path(__file__).resolve().parents[2]
WHITELIST_DIRS = ("auto_bi/", "deploy/", "docker/")
WHITELIST_FILES = ("pyproject.toml", "LICENSE", "semantic/model.yaml")


def run(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs)


def main() -> int:
    token = os.environ.get("HF_TOKEN", "")
    if not re.fullmatch(r"hf_[A-Za-z0-9]{20,}", token):
        print("set HF_TOKEN to a valid hf_... write token", file=sys.stderr)
        return 2

    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp()) / "space"
    if work.exists():
        shutil.rmtree(work)
    run("git", "clone", "--quiet", f"https://huggingface.co/spaces/{SPACE}", str(work))
    before = set(run("git", "-C", str(work), "ls-files").stdout.split())

    snapshot = [
        f
        for f in run("git", "-C", str(REPO), "ls-files").stdout.splitlines()
        if f.startswith(WHITELIST_DIRS) or f in WHITELIST_FILES
    ]
    for entry in work.iterdir():  # wipe all but .git and the Space README front-matter
        if entry.name in (".git", "README.md"):
            continue
        shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    for f in snapshot:
        dst = work / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / f, dst)
    shutil.copy2(REPO / "deploy/hf-demo/Dockerfile", work / "Dockerfile")
    if (work / "README.md").read_text(encoding="utf-8").splitlines()[:1] != ["---"]:
        print("Space README.md lost its HF front-matter — aborting", file=sys.stderr)
        return 1

    run("git", "-C", str(work), "add", "-A")
    after = set(run("git", "-C", str(work), "ls-files").stdout.split())
    print(f"snapshot: {len(after)} files (+{sorted(after - before)} -{sorted(before - after)})")
    if not run("git", "-C", str(work), "diff", "--cached", "--stat").stdout.strip():
        print("nothing to publish — Space already matches main")
        return 0

    head = run("git", "-C", str(REPO), "rev-parse", "--short", "HEAD").stdout.strip()
    run("git", "-C", str(work), "commit", "--quiet", "-m", f"sync from main {head}")
    push_url = f"https://{SPACE_USER}:{token}@huggingface.co/spaces/{SPACE}"
    push = subprocess.run(
        ["git", "-C", str(work), "push", push_url, "main"],
        text=True,
        capture_output=True,
    )
    print("push:", "OK" if push.returncode == 0 else push.stderr.replace(token, "***"))
    return push.returncode


if __name__ == "__main__":
    raise SystemExit(main())
