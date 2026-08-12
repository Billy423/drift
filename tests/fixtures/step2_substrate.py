"""A generated repository and scripted client that exercise every read-model row type.

Safe files form a deterministic base commit; opt-in hazards and a low-confidence claim extend it
without changing base artifacts.
Responses selected by request content stay stable across scheduling orders.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

# Importing the runner's emit vocabulary keeps the fixture coupled to the requests it actually
# sends. A local copy could turn a runner-side reword into a silent invalid response.
from drift.agent.runner import _EMIT_INSTRUCTION, EMIT_TOOL_NAME

__all__ = [
    "AGENT_INVENTORIES",
    "EXPECTED_UNGATEABLE_REASONS",
    "HAZARD_BIG_CHARS",
    "SUBSTRATE_COMMIT_SHA",
    "SUBSTRATE_COMMIT_SHA_INPUTS",
    "SUB_THRESHOLD_CLAIM",
    "SubstrateClient",
    "agent_inventories",
    "build_substrate_repo",
    "doc_texts",
    "make_substrate_client",
]

_README = """# Substrate

The bundled logo lives at assets/logo.png and is copied on every build.
Release notes live in docs/CHANGELOG.md.
Bundled output is written to dist/bundle.js by the packer.
Run `make build` to produce it.
The icon file logo.png is referenced from the assets directory.
Call render(verbose=True) for chatty output.
Network access goes through requests.get today.
"""

_GUIDE = """# Guide

Roadmap notes live in docs/ROADMAP.md for now.
The package entry point is substrate_pkg/core.py.
Widget has a configurable label option.
The scanner degrades gracefully when the network is slow.
"""

# Ignoring `dist/` makes the absent bundle mechanically unadjudicable as build output.
_GITIGNORE = "dist/\n*.log\n"

# These documented parameters cover variadic, present, absent, and class-constructor outcomes.
# In particular, the absent `style` parameter gives the docstring producer a claim that reaches
# the judge.
_CORE_PY = '''"""Substrate package core — fixture code for the step-2 verification substrate."""


def render(**kwargs):
    """Render the payload.

    Parameters
    ----------
    verbose : bool
        Whether to log each step.
    """
    return dict(kwargs)


def summarize(text, limit):
    """Summarize a blob of text.

    Parameters
    ----------
    text : str
        The text to summarize.
    limit : int
        Maximum length of the summary.
    style : str
        Documented but absent from the signature.
    """
    return text[:limit]


class Widget:
    """A widget carrying one configurable label."""

    label = "widget"

    def __init__(self, size):
        """Build a widget.

        Parameters
        ----------
        size : int
            Widget size in cells.
        """
        self.size = size
'''

_INIT_PY = '"""Substrate fixture package."""\n'

# Hazard members are added after these safe files are committed.
_SAFE_FILES: dict[str, str] = {
    "README.md": _README,
    "GUIDE.md": _GUIDE,
    ".gitignore": _GITIGNORE,
    "substrate_pkg/__init__.py": _INIT_PY,
    "substrate_pkg/core.py": _CORE_PY,
    "assets/logo.png": "PNG-fixture\n",
}

# These omissions are intentional: no Makefile, `docs/` directory, or `dist/` directory means
# their claims exercise distinct gate outcomes.

HAZARD_BIG_CHARS = 420_000  # above the document input bound

# Fixed identity and timestamps produce the same commit revision for the same tree. Journaled
# fixture rows use that revision, so it must remain stable across machines.
SUBSTRATE_COMMIT_SHA_INPUTS = {
    "GIT_AUTHOR_NAME": "drift substrate",
    "GIT_AUTHOR_EMAIL": "substrate@drift.invalid",
    "GIT_AUTHOR_DATE": "2026-08-06T00:00:00+0000",
    "GIT_COMMITTER_NAME": "drift substrate",
    "GIT_COMMITTER_EMAIL": "substrate@drift.invalid",
    "GIT_COMMITTER_DATE": "2026-08-06T00:00:00+0000",
}

# This revision is asserted because editing any safe fixture file changes the tree and invalidates
# artifacts captured against the old one. A mismatch requires deliberate artifact recapture.
SUBSTRATE_COMMIT_SHA = "be27050d5e5ce0db05eff729c98ac685364279b6"


def doc_texts() -> dict[str, str]:
    """Return the fixture's Markdown documents by repository-relative path."""
    return {"README.md": _README, "GUIDE.md": _GUIDE}


def build_substrate_repo(
    tmp_path, include_hazards: bool = False, commit_hazards: bool = False
) -> Path:
    """Build and commit the substrate repository, optionally adding hazard members.

    Hazard members are created after the safe commit because Git cannot index a FIFO. They are
    excluded from baseline scans but available to enumeration-safety tests.

    `commit_hazards` commits the symlink and oversize file so classification guards see them. It
    moves `HEAD` and is incompatible with callers that require `SUBSTRATE_COMMIT_SHA`.
    """
    # On macOS, `/var` resolves to `/private/var`. Resolving both sides prevents real parser paths
    # from appearing to escape an unresolved temporary root and suppressing docstring claims.
    root = Path(tmp_path).resolve() / "step2_substrate"
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in _SAFE_FILES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    env = {**os.environ, **SUBSTRATE_COMMIT_SHA_INPUTS}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "substrate fixture"], cwd=root, check=True, env=env)

    if include_hazards:
        _add_hazards(root)
        if commit_hazards:
            # Naming the indexable hazards avoids asking Git to add the FIFO.
            subprocess.run(
                ["git", "add", "hazard_escape.md", "hazard_big.md"], cwd=root, check=True
            )
            subprocess.run(["git", "commit", "-qm", "hazards"], cwd=root, check=True, env=env)
    return root


def _add_hazards(root: Path) -> None:
    """Add an escaping symlink, FIFO, and oversize document to the fixture."""
    outside = root.parent / "outside_the_repo.md"
    outside.write_text("I live outside the repo.\n", encoding="utf-8")
    escape = root / "hazard_escape.md"
    if not escape.exists():
        os.symlink(outside, escape)

    fifo = root / "hazard_fifo.md"
    if not fifo.exists():
        os.mkfifo(fifo)

    big = root / "hazard_big.md"
    big.write_text("# big\n" + ("x" * HAZARD_BIG_CHARS) + "\n", encoding="utf-8")


# Each entry: (literal, predicate, args, claim_class, confidence, note). `predicate == "none"`
# means the discovery producer declined to bind a predicate. Tests verify the intended outcome of
# every entry.

_SPEC: dict[str, list[tuple]] = {
    "GUIDE.md": [
        # Mechanical refutation, then judged not live: journal only.
        ("docs/ROADMAP.md", "path_exists", [], 1, 0.4, "roadmap pointer"),
        # Passing replay: recorded but absent from user-facing tiers.
        ("substrate_pkg/core.py", "path_exists", [], 1, 0.95, "entry point"),
        # preview grade -> preview_verdict + ranked tier (cannot mint, cannot suppress)
        (
            "Widget has a configurable label option",
            "class_has_member",
            ["substrate_pkg.core.Widget", "label"],
            1,
            0.9,
            "configurable option",
        ),
        # Unbound claim: ranked as suspected.
        (
            "The scanner degrades gracefully when the network is slow.",
            "none",
            [],
            3,
            0.1,
            "behavioural, no predicate fits",
        ),
    ],
    "README.md": [
        # Passing replay.
        ("assets/logo.png", "path_exists", [], 1, 0.9, "bundled asset"),
        # Mechanical refutation judged live: emitted finding.
        ("docs/CHANGELOG.md", "path_exists", [], 1, 0.3, "release notes"),
        # Gitignored build output: journal only.
        ("dist/bundle.js", "path_exists", [], 1, 0.5, "build output"),
        # Missing adjudication input: ranked as unexamined.
        ("make build", "make_target_exists", ["build"], 1, 0.8, "build command"),
        # Ambiguous base: journal only.
        ("logo.png", "path_exists", [], 1, 0.6, "bare asset reference"),
        # Variadic signature: ranked as suspected.
        (
            "render(verbose=True)",
            "signature_has_param",
            ["substrate_pkg.core.render", "verbose"],
            2,
            0.15,
            "keyword argument",
        ),
        # External symbol: journal only.
        ("requests.get", "symbol_resolves", [], 1, 0.7, "third-party helper"),
        # This deliberately absent literal models a hallucinated anchor rejected by the doc leg.
        ("docs/ghost.md", "path_exists", [], 1, 0.25, "hallucinated anchor"),
    ],
}

# The read model treats some mechanically unadjudicable reasons as ranked entries and keeps the
# rest in the journal; the fixture covers both sets.
EXPECTED_UNGATEABLE_REASONS = {
    "comment_routed": {"no-makefile", "variadic"},
    "journal_only": {"gitignored", "base-ambiguous", "external"},
}


def _spans(doc_text: str, literal: str) -> list[list[int]]:
    """Return the literal's first one-indexed line span, or a synthetic first-line span.

    The absent entry models a hallucinated anchor, which still arrives with a model-proposed span.
    """
    for i, line in enumerate(doc_text.splitlines(), 1):
        if literal in line:
            return [[i, i]]
    return [[1, 1]]


def _inventory(doc_path: str) -> dict:
    """Materialize one document's scripted claims with spans."""
    texts = doc_texts()
    return {
        "claims": [
            {
                "literal": literal,
                "predicate": predicate,
                "spans": _spans(texts[doc_path], literal),
                "claim_class": claim_class,
                "note": note,
                "confidence": confidence,
                "args": list(args),
            }
            for literal, predicate, args, claim_class, confidence, note in _SPEC[doc_path]
        ]
    }


AGENT_INVENTORIES: dict[str, dict] = {path: _inventory(path) for path in _SPEC}

# `s_passed` requires both a live verdict and confidence at or above `S_THRESHOLD`. This opt-in
# claim is live below the threshold, so it distinguishes that rule from minting on `live` alone.
#
# The literal already appears in a committed document, preserving the fixture revision. Its
# `(doc_path, literal)` key is unique, avoiding accidental reuse of another verdict, and `docs`
# exists nowhere in the tree, ensuring the claim reaches the judge.
#
# It defaults off because artifacts captured against the base inventory must not grow a claim.
_SUB_THRESHOLD_DOC = "GUIDE.md"
_SUB_THRESHOLD_ENTRY: tuple = (
    "docs/",
    "path_exists",
    [],
    1,
    0.35,
    "the docs directory this doc points into",
)


def agent_inventories(include_sub_threshold: bool = False) -> dict[str, dict]:
    """Return scripted inventories, optionally including the live low-confidence claim."""
    if not include_sub_threshold:
        return AGENT_INVENTORIES
    spec = {path: list(entries) for path, entries in _SPEC.items()}
    spec[_SUB_THRESHOLD_DOC] = [*spec[_SUB_THRESHOLD_DOC], _SUB_THRESHOLD_ENTRY]
    texts = doc_texts()
    return {
        path: {
            "claims": [
                {
                    "literal": literal,
                    "predicate": predicate,
                    "spans": _spans(texts[path], literal),
                    "claim_class": claim_class,
                    "note": note,
                    "confidence": confidence,
                    "args": list(args),
                }
                for literal, predicate, args, claim_class, confidence, note in entries
            ]
        }
        for path, entries in spec.items()
    }


#: The document and literal added by the sub-threshold arm.
SUB_THRESHOLD_CLAIM = (_SUB_THRESHOLD_DOC, _SUB_THRESHOLD_ENTRY[0])

# `docs/ROADMAP.md` is judged not live. `docs/` is live below the confidence threshold and must
# mint nothing. Its verdict can remain in the table because the key is unreachable until opted in.
_JUDGE_VERDICTS: dict[tuple[str, str], dict] = {
    ("GUIDE.md", "docs/ROADMAP.md"): {
        "live": False,
        "reasoning": "the doc frames this as a roadmap pointer, not a present-tense assertion",
        "confidence": 0.85,
    },
    SUB_THRESHOLD_CLAIM: {
        "live": True,
        "reasoning": "probably a live reference, but the surrounding prose is ambiguous",
        "confidence": 0.3,  # < S_THRESHOLD (0.5): live is not enough, and that is the point
    },
}
_DEFAULT_VERDICT = {
    "live": True,
    "reasoning": "present-tense assertion about the current repo",
    "confidence": 0.9,
}


class _Usage:
    """Provide the token fields the runner reads from a response."""

    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        """Set paid-token counts and zero both cache-token counts."""
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _Block:
    """Represent one typed response content block."""

    def __init__(self, type: str, **kw) -> None:  # noqa: A002 - mirrors the SDK's field name
        """Store the block type and its type-specific fields."""
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _Resp:
    """Represent a scripted model response with content, usage, and stop reason.

    The runner rejects an emit payload stopped by `max_tokens`, because its strict tool arguments
    may be partial. Scripted responses therefore use real API stop values rather than leaving the
    field unset.
    """

    def __init__(self, blocks: list, usage: _Usage, stop_reason: str = "end_turn") -> None:
        """Store the response fields consumed by the runner."""
        self.content = blocks
        self.usage = usage
        self.stop_reason = stop_reason


class UnexpectedRequest(AssertionError):
    """Signal that the substrate has no scripted answer for a request.

    Guessing would turn an unrecognised prompt into an apparently valid empty inventory and hide
    lost coverage.
    """


class SubstrateClient:
    """Serve deterministic discovery and judge responses by request content.

    Role comes from the system prompt, document from `Document path:`, and claim from
    `Claim literal:` plus `Doc path:`. Scheduling order therefore cannot change answers.

    The emit turn is recognized by exact equality with the runner's final instruction and by the
    offered emit tool. Exact matching prevents a document that quotes the instruction from being
    mistaken for an emit request.
    """

    #: Token usage chosen so budget tests stop after one document deterministically.
    INPUT_TOKENS = 1_000
    OUTPUT_TOKENS = 500

    #: The emit `tool_use` block's id. The runner never pairs a `tool_result` to it (an emit call
    #: is terminal and is never executed as a belt tool), but the real API always sends one.
    EMIT_BLOCK_ID = "substrate-emit-1"

    def __init__(self, include_sub_threshold: bool = False) -> None:
        """Initialize call capture and the selected inventory variant."""
        self.calls: list[dict] = []
        self.messages = self
        #: Off by default because base artifacts exclude the sub-threshold claim.
        self.inventories = agent_inventories(include_sub_threshold)

    def create(self, **kwargs) -> _Resp:
        """Return a scripted loop response or strict emit-tool call for one request."""
        self.calls.append(kwargs)
        usage = _Usage(self.INPUT_TOKENS, self.OUTPUT_TOKENS)
        if not self._is_emit_request(kwargs):
            # Loop turns avoid tools so the fixture stays deterministic and preserves the exact
            # two-calls-per-unit cost model.
            return _Resp([_Block("text", text="no repo exploration needed")], usage)
        if not self._offers_emit_tool(kwargs):
            # Answering an emit request without an offered tool would model an API-impossible
            # response and hide a runner that stopped sending the tool.
            raise UnexpectedRequest(
                f"the emit turn was asked for, but no {EMIT_TOOL_NAME!r} tool was offered; "
                f"the runner offers it on every request, so this request did not come "
                f"from `run_loop`"
            )
        return _Resp(
            [
                _Block(
                    "tool_use",
                    id=self.EMIT_BLOCK_ID,
                    name=EMIT_TOOL_NAME,
                    input=json.loads(self._emit_text(kwargs)),
                )
            ],
            usage,
            stop_reason="tool_use",
        )

    @staticmethod
    def _last_user_texts(kwargs: dict) -> list[str]:
        """Return text blocks from the request's final user message."""
        messages = kwargs.get("messages") or []
        if not messages or messages[-1].get("role") != "user":
            return []
        content = messages[-1].get("content")
        if isinstance(content, str):
            return [content]
        return [
            item["text"]
            for item in content or []
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]

    @classmethod
    def _is_emit_request(cls, kwargs: dict) -> bool:
        """Return whether the final user message exactly equals the emit instruction.

        Containment is unsafe because the final user message on a loop turn may be a document that
        quotes the instruction. The runner appends the real instruction as its own message, so
        equality distinguishes the two.
        """
        return any(text == _EMIT_INSTRUCTION for text in cls._last_user_texts(kwargs))

    @staticmethod
    def _offers_emit_tool(kwargs: dict) -> bool:
        """Return whether this request offers the emit tool."""
        return any(
            isinstance(tool, dict) and tool.get("name") == EMIT_TOOL_NAME
            for tool in kwargs.get("tools") or []
        )

    @staticmethod
    def _texts(kwargs: dict) -> list[str]:
        """Every text a request carries, system blocks first, then user/assistant content."""
        out: list[str] = []
        for block in kwargs.get("system") or []:
            if isinstance(block, dict) and "text" in block:
                out.append(block["text"])
        for message in kwargs.get("messages") or []:
            content = message.get("content")
            if isinstance(content, str):
                out.append(content)
                continue
            for item in content or []:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    out.append(item["text"])
                elif getattr(item, "type", None) == "text":
                    out.append(getattr(item, "text", ""))
        return out

    @staticmethod
    def _field(texts: list[str], label: str) -> str | None:
        """The value of a `Label: value` line in any of the request's texts."""
        for text in texts:
            for line in text.splitlines():
                if line.startswith(label):
                    return line[len(label) :].strip()
        return None

    def _emit_text(self, kwargs: dict) -> str:
        """Return the scripted emit payload as a JSON string.

        `create` decodes the string into the tool call input. Keeping the override point textual
        lets specialized clients supply payloads without reproducing the carrier logic.
        """
        texts = self._texts(kwargs)
        joined = "\n".join(texts)
        if "You are a cartographer" in joined:
            doc_path = self._field(texts, "Document path:")
            if doc_path not in self.inventories:
                raise UnexpectedRequest(
                    f"no scripted inventory for doc unit {doc_path!r}; the substrate's doc set "
                    f"is {sorted(self.inventories)}"
                )
            return json.dumps(self.inventories[doc_path])
        if "You are an independent reader" in joined:
            key = (self._field(texts, "Doc path:"), self._field(texts, "Claim literal:"))
            return json.dumps(_JUDGE_VERDICTS.get(key, _DEFAULT_VERDICT))
        raise UnexpectedRequest("request matched neither the discovery nor the judge system prompt")


def make_substrate_client(include_sub_threshold: bool = False) -> SubstrateClient:
    """Return a scripted client with per-run call capture.

    The optional inventory adds a live claim below the semantic threshold. It defaults off to
    preserve artifacts captured against the base inventory.
    """
    return SubstrateClient(include_sub_threshold)
