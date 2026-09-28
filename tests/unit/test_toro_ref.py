"""Unit: which toro checkout CI builds matador against.

uv.lock records the version of the sibling toro it was locked against, and CI runs
`uv sync --locked`. Built against toro main, every toro version bump failed matador's
CI before a single test ran, on main and on every open PR, until someone relocked.
The script is run here with a stand-in `gh` that answers only for the refs a test says
exist.
"""

import os
import pathlib
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "pick-toro-ref.sh"
LOCK = """\
[[package]]
name = "starlette"
version = "0.48.0"

[[package]]
name = "toro-queue"
version = "1.0.1"
source = { editable = "../toro" }
"""


def _pick(tmp_path: pathlib.Path, *, existing: list[str], branch: str = "feat/x") -> str:
    """Run the script; `existing` lists the `gh api` paths that answer 200."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    known = "\n".join(existing)
    gh.write_text(f'#!/bin/sh\nprintf "%s\\n" "{known}" | grep -qxF "$2"\n')
    gh.chmod(0o755)
    (tmp_path / "uv.lock").write_text(LOCK)
    output = tmp_path / "output"
    env = os.environ | {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "BRANCH": branch,
        "OWNER": "acme",
        "GITHUB_OUTPUT": str(output),
    }
    sh = shutil.which("sh")
    assert sh, "no sh on PATH"
    # the command is a resolved executable and the repo's own script
    subprocess.run([sh, str(SCRIPT)], cwd=tmp_path, env=env, check=True)  # noqa: S603
    return output.read_text()


def test_a_toro_branch_with_the_same_name_wins(tmp_path):
    existing = ["repos/acme/toro/branches/feat/x", "repos/acme/toro/git/ref/tags/v1.0.1"]
    assert _pick(tmp_path, existing=existing) == "ref=feat/x\n"


def test_otherwise_the_release_the_lock_records(tmp_path):
    assert _pick(tmp_path, existing=["repos/acme/toro/git/ref/tags/v1.0.1"]) == "ref=v1.0.1\n"


def test_main_when_the_locked_version_was_never_tagged(tmp_path):
    assert _pick(tmp_path, existing=[]) == "ref=main\n"
