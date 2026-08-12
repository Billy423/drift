"""The replay gate: no claim becomes a finding without being re-derived here.

Each claim carries a two-leg check — its anchor literal is still in the document, and a pure
predicate answers the same question against the repository — and both legs are recomputed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from drift.kernels.doc_contains import doc_contains
from drift.kernels.models import UNGATEABLE_REASONS, EvClaim, Ungateable
from drift.kernels.registry import predicate_registry


class GateOutcome(StrEnum):
    """The replay gate's six possible verdicts on one claim's mechanical legs."""

    M_CERTIFIED = "M_CERTIFIED"  # doc leg True, kernel leg False -> drift condition holds
    PASSING = "PASSING"  # both legs fine -> claim stays live in the inventory
    BINDING_FAIL = "BINDING_FAIL"  # doc leg False -> hallucinated/stale anchor
    KERNEL_ERROR = "KERNEL_ERROR"  # kernel raised -> never a finding
    UNBOUND = "UNBOUND"  # claim.check is None -> nothing to replay
    UNGATEABLE = "UNGATEABLE"  # kernel declared the claim mechanically unadjudicable


@dataclass(frozen=True)
class GateResult:
    """One claim's replay verdict: the claim, its outcome, and a short human-readable detail."""

    claim: EvClaim
    outcome: GateOutcome
    detail: str


def replay(repo_root: str, claims: list[EvClaim]) -> list[GateResult]:
    """Replay each claim's two-leg check against `repo_root`, one result per claim.

    The document leg runs first, so a stale or invented anchor is reported as `BINDING_FAIL` even
    where the kernel would also have failed, and no kernel call is spent on a dead anchor.
    """
    results = []
    for claim in claims:
        check = claim.check
        if check is None:
            results.append(GateResult(claim, GateOutcome.UNBOUND, "check is None"))
            continue
        try:
            anchored = doc_contains(repo_root, check.raw["doc_path"], check.raw["literal"])
        except OSError:
            # Unreadable is not an anchor that held. On the minting path the conservative
            # answer is to decline, which is what a failed anchor already does.
            anchored = False
        if not anchored:
            results.append(
                GateResult(claim, GateOutcome.BINDING_FAIL, "anchor literal not found in doc")
            )
            continue
        try:
            predicate = predicate_registry[check.predicate]
            target_exists = predicate.kernel(repo_root, *check.normalized_args)
        except Ungateable as ung:
            if ung.reason not in UNGATEABLE_REASONS:
                # Raised inside this handler: the sibling `except Exception` would otherwise
                # turn a kernel defect into a KERNEL_ERROR row read as a repository problem.
                raise ValueError(
                    f"kernel {check.predicate!r} raised Ungateable({ung.reason!r}), which is not "
                    f"in the closed reason set {sorted(UNGATEABLE_REASONS)}. The set is closed "
                    f"(see kernels/models.UNGATEABLE_REASONS); a kernel may not add to it."
                ) from ung
            results.append(GateResult(claim, GateOutcome.UNGATEABLE, ung.reason))
            continue
        except Exception as exc:  # noqa: BLE001 - a kernel must never escape the gate
            results.append(GateResult(claim, GateOutcome.KERNEL_ERROR, repr(exc)))
            continue
        if target_exists:
            results.append(GateResult(claim, GateOutcome.PASSING, "both legs hold"))
        else:
            results.append(
                GateResult(claim, GateOutcome.M_CERTIFIED, "doc leg holds, kernel target absent")
            )
    return results


def replay_check(repo_root: str, check: dict) -> bool:
    """Replay a stored check dictionary; True means the drift is still present.

    `normalized_args` is used verbatim and never re-derived through `predicate.normalize`: a
    stored check's identity is the arguments recorded when the claim was gated, and re-deriving
    them would re-decide a settled question and let an issue's status flap between runs.

    Returns:
        True whenever the check cannot be verified — an unknown predicate, a kernel that raised
        or declined — so the issue stays open for a human. False when the anchor has left the
        document, which is the document-side repair. A document that exists but cannot be read
        raises out of `doc_contains` and is caught here as unverifiable.
    """
    predicate = predicate_registry.get(check["predicate"])
    if predicate is None:
        return True
    raw = check["raw"]
    try:
        if not doc_contains(repo_root, raw["doc_path"], raw["literal"]):
            return False
    except Exception:  # noqa: BLE001 - cannot verify -> keep the issue open
        return True
    try:
        target_exists = predicate.kernel(repo_root, *check["normalized_args"])
    except Exception:  # noqa: BLE001 - cannot verify -> keep the issue open
        return True
    return not target_exists
