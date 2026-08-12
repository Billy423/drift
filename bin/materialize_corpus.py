"""Materialize the pinned corpus the regression pin replays against.

The pin protects the six verified findings `evals/` publishes. Without the corpus those tests skip,
which is honest but means the headline result is unguarded on every machine but the one that wrote
it — so the clone is a documented step rather than local knowledge.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

PIN = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "data" / "g3-pin.json"
UPSTREAM = "https://github.com/jupyter-server/jupyter_server"


def main() -> int:
    corpus = json.loads(PIN.read_text(encoding="utf-8"))["corpus"]
    root = pathlib.Path(os.path.expanduser(corpus["root"]))
    manifest = pathlib.Path(os.path.expanduser(corpus["manifest"]))
    sha = corpus["commit_sha"]

    root.parent.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        subprocess.run(["git", "clone", UPSTREAM, str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "checkout", "--detach", sha], check=True)

    # The manifest is what lets the fixture verify the checkout is still the pinned one; a corpus
    # that moved without it would invalidate the pin silently. Three tab-separated fields, because
    # that is the shape the fixture parses — a two-field line is read as no entry at all, and the
    # tests then fail rather than skip, which is how this was found.
    line = f"{corpus['name']}\t{UPSTREAM}.git\t{sha}\n"
    existing = manifest.read_text(encoding="utf-8") if manifest.is_file() else ""
    if line not in existing:
        manifest.write_text(existing + line, encoding="utf-8")

    print(f"{corpus['name']} at {sha[:7]} -> {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
