"""Pinned corpus claims and scripted judge responses for end-to-end regression tests.

Claims replay through the production predicate and gate; model verdicts preserve pinned live and
confidence values. A separate low-confidence fixture tests the semantic threshold.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tests.fixtures.step2_substrate import SubstrateClient, UnexpectedRequest

__all__ = [
    "CORPUS_ROOT",
    "MANIFEST_PATH",
    "G3Client",
    "corpus_sha_from_manifest",
    "corpus_skip_reason",
    "load_pin",
    "pin_inventory",
]

PIN_PATH = Path(__file__).resolve().parent / "data" / "g3-pin.json"

CORPUS_ROOT = Path(os.path.expanduser("~/.drift-corpus/jupyter-server__jupyter_server"))
MANIFEST_PATH = Path(os.path.expanduser("~/.drift-corpus/MANIFEST.tsv"))


def load_pin() -> dict:
    """Load the committed corpus pin."""
    return json.loads(PIN_PATH.read_text(encoding="utf-8"))


def corpus_skip_reason() -> str | None:
    """Return why the pinned corpus cannot be replayed, or None when it is usable.

    Missing corpus material must produce a visible skip; silently passing without replaying the
    pin would make the regression test meaningless.
    """
    if not CORPUS_ROOT.is_dir():
        return (
            f"the pinned corpus {CORPUS_ROOT} is absent, so the regression pin cannot be replayed. "
            f"Run `make corpus` to clone it at the pinned commit, then re-run."
        )
    if not MANIFEST_PATH.is_file():
        return (
            f"{MANIFEST_PATH} is absent, so the corpus sha cannot be verified — a moved or "
            f"re-checked-out corpus would invalidate the pin silently."
        )
    return None


def corpus_sha_from_manifest(name: str = "jupyter-server__jupyter_server") -> str | None:
    """Return the revision pinned for a named corpus repository."""
    for line in MANIFEST_PATH.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[0] == name:
            return fields[2].strip()
    return None


def pin_inventory(pin: dict, doc_text: str) -> dict:
    """Build the pinned document's complete scripted discovery inventory.

    Spans are located in the corpus text rather than copied from model output. Claim identity does
    not depend on spans; they only anchor the literal in the document.

    Empty proposed arguments are intentional: `path_exists` derives them from the literal, which
    makes the production normalizer produce the pinned identity rather than transcribing it.
    """
    claims = []
    for row in pin["true_positives"] + pin["base_ambiguous"]:
        claims.append(
            {
                "literal": row["literal"],
                "predicate": row["predicate"],
                "spans": _spans(doc_text, row["literal"]),
                "claim_class": 1,
                "note": f"regression pin ({row['expected_gate_outcome']})",
                "confidence": 0.1,
                "args": [],
            }
        )
    return {"claims": claims}


def _spans(doc_text: str, literal: str) -> list[list[int]]:
    """Locate a pinned literal in the corpus text."""
    for i, line in enumerate(doc_text.splitlines(), 1):
        if literal in line:
            return [[i, i]]
    raise AssertionError(
        f"pinned literal {literal!r} is not in the corpus doc — the pin and the corpus disagree"
    )


class G3Client(SubstrateClient):
    """Serve the pinned inventory and judge verdicts for one corpus document.

    Both tables are closed. Raising on an unscripted request prevents lost pin coverage from
    looking like a successful replay.
    """

    def __init__(self, pin: dict, doc_text: str) -> None:
        """Initialize the closed inventory and verdict tables from the pin."""
        super().__init__()
        self.doc_path = pin["doc_path"]
        self.inventories = {self.doc_path: pin_inventory(pin, doc_text)}
        # Only mechanically refuted rows may reach the judge. Scripting verdicts for skipped rows
        # would hide a routing regression.
        self._verdicts = {
            (self.doc_path, row["literal"]): {
                "live": row["s_verdict"]["live"],
                "reasoning": "regression pin: recorded live in the pinned verdict",
                "confidence": row["s_verdict"]["confidence"],
            }
            for row in pin["true_positives"]
        }

    def _emit_text(self, kwargs: dict) -> str:
        """Return the pinned inventory or the matching closed-table judge verdict."""
        texts = self._texts(kwargs)
        if "You are an independent reader" in "\n".join(texts):
            key = (self._field(texts, "Doc path:"), self._field(texts, "Claim literal:"))
            verdict = self._verdicts.get(key)
            if verdict is None:
                raise UnexpectedRequest(
                    f"no pinned judge verdict for {key!r}; the pin scripts the six true positives "
                    f"only, because nothing else in it may reach the judge"
                )
            return json.dumps(verdict)
        return super()._emit_text(kwargs)
