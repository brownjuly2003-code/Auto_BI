"""Publish the demo to its Hugging Face Space as a fresh white-list snapshot.

Usage:
    HF_TOKEN=hf_... python deploy/hf-demo/publish_space.py [workdir] [--dry-run] [--force-clean]

The Space repo is a git tree REPLACE (never an additive upload): every tracked
file under the white-list below, the demo Dockerfile copied to the root, and the
Space's own README.md (HF front-matter) kept as-is. Publishing only `git ls-files`
content is what keeps internal notes and untracked scratch out of the public host.

Safety (audit C-3):
- default workdir is a TemporaryDirectory that is ALWAYS cleaned up;
- an existing user-passed workdir is wiped only when it carries the Space-clone
  marker (.git whose remotes point at huggingface.co/spaces/<SPACE>) AND
  --force-clean is passed — an arbitrary directory is never deleted;
- the token never appears in a git URL or argv: the push authenticates through an
  inline credential helper that reads HF_TOKEN from the environment.
"""

import argparse
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


def is_space_clone(path: Path, space: str = SPACE) -> bool:
    """True iff *path* is a git clone whose remotes point at THIS HF Space."""
    if not (path / ".git").exists():
        return False
    try:
        remotes = run("git", "-C", str(path), "remote", "-v").stdout
    except (subprocess.CalledProcessError, OSError):
        return False
    return f"huggingface.co/spaces/{space}" in remotes


def publish(work: Path, *, token: str, dry_run: bool) -> int:
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
    diff = run("git", "-C", str(work), "diff", "--cached", "--stat").stdout.strip()
    if not diff:
        print("nothing to publish — Space already matches main")
        return 0
    if dry_run:
        print(diff)
        print("dry-run: nothing pushed")
        return 0

    head = run("git", "-C", str(REPO), "rev-parse", "--short", "HEAD").stdout.strip()
    run("git", "-C", str(work), "commit", "--quiet", "-m", f"sync from main {head}")
    # The token stays out of URLs and argv: git asks the inline helper, which reads
    # HF_TOKEN from the child's environment at authentication time.
    helper = f"!f() {{ echo username={SPACE_USER}; echo password=$HF_TOKEN; }}; f"
    push = subprocess.run(
        ["git", "-C", str(work), "-c", f"credential.helper={helper}", "push", "origin", "main"],
        text=True,
        capture_output=True,
        env={**os.environ, "HF_TOKEN": token, "GIT_TERMINAL_PROMPT": "0"},
    )
    print("push:", "OK" if push.returncode == 0 else push.stderr.replace(token, "***"))
    return push.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workdir", nargs="?", default=None)
    parser.add_argument("--dry-run", action="store_true", help="build the snapshot, push nothing")
    parser.add_argument(
        "--force-clean",
        action="store_true",
        help="allow wiping an EXISTING workdir (only ever a verified clone of the Space)",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("HF_TOKEN", "")
    if not args.dry_run and not re.fullmatch(r"hf_[A-Za-z0-9]{20,}", token):
        print("set HF_TOKEN to a valid hf_... write token", file=sys.stderr)
        return 2

    if args.workdir is None:
        with tempfile.TemporaryDirectory(prefix="auto_bi_space_") as td:
            return publish(Path(td) / "space", token=token, dry_run=args.dry_run)

    work = Path(args.workdir)
    if work.exists():
        if not is_space_clone(work):
            print(
                f"refusing to touch {work}: not a clone of huggingface.co/spaces/{SPACE} "
                "(pass a fresh path, or none for a temp dir)",
                file=sys.stderr,
            )
            return 2
        if not args.force_clean:
            print(
                f"{work} is an existing Space clone; pass --force-clean to wipe and re-clone it",
                file=sys.stderr,
            )
            return 2
        shutil.rmtree(work)
    return publish(work, token=token, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
