"""The closed predicate vocabulary: what a predicate is, and how the model is told about it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

Grade = Literal["high", "preview"]


@dataclass(frozen=True)
class Predicate:
    """One entry in the closed predicate vocabulary.

    Attributes:
        description: Rendered into the model's prompt by `vocabulary`, so editing it edits the
            prompt.
        normalize: Turns a document literal into recorded normalization plus the kernel's
            arguments, or returns None to decline the claim.
        kernel: Reads the checked-out tree and nothing else: no network, no clock, and none of
            the scanned repository's own dependencies are imported. Purity holds within one
            revision rather than across revisions — the symbol reader is memoized per
            repository path for the life of the process, so a long-lived worker answers from
            the tree it loaded first.
        grade: A `preview` predicate runs, journals and annotates the ranked tier, but can
            neither mint a finding nor suppress a claim, so the vocabulary can grow with demand
            without the growth being able to move a published number.
    """

    name: str
    description: str
    normalize: Callable[[str, str, tuple[str, ...] | None], tuple[dict, tuple[str, ...]] | None]
    kernel: Callable[..., bool]
    # Defaulting to `high` means a predicate that forgets to declare a grade gets the
    # obligations that are enforced, rather than silently acquiring the exemption.
    grade: Grade = "high"


predicate_registry: dict[str, Predicate] = {}


def register_predicate(p: Predicate) -> None:
    """Admit a predicate into the closed registry."""
    predicate_registry[p.name] = p


def vocabulary() -> str:
    """Render the registry as the model's binding vocabulary, high grade first.

    Grades are labelled rather than left implicit: a preview bind cannot lead to a certifiable
    finding, and a model unable to tell the two apart displaces one with the other by accident.
    This text is part of the prompt, so an edit here is a change of discovery version and must
    ship in the same commit as the bump.
    """
    high = [p for p in predicate_registry.values() if p.grade == "high"]
    preview = [p for p in predicate_registry.values() if p.grade == "preview"]
    blocks = [
        "HIGH-grade predicates (a mechanical check the gate can certify a finding from):",
        *(f"- {p.name}: {p.description}" for p in high),
    ]
    if preview:
        blocks += [
            "",
            "Preview-grade predicates (being measured; bind them when they fit, but their "
            "results are reported as unverified candidates, never as findings):",
            *(f"- {p.name}: {p.description}" for p in preview),
        ]
    return "\n".join(blocks)
