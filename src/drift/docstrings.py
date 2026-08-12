"""The docstring producer: parameter claims read straight out of the code, with no model involved.

A documented parameter is its own anchor, so this producer needs no discovery step.
"""

from __future__ import annotations

import re

from drift.kernels.models import Anchor, Check, EvClaim, SSlot
from drift.kernels.pysymbols import provider_for
from drift.kernels.registry import predicate_registry

_IDENT = re.compile(r"^[A-Za-z_]\w*$")

# Must name a member of the closed set in `kernels.models.PRODUCERS`.
_PRODUCER = "docstrings"

# `.pyi` belongs here: a stub file is Python, and answering "nothing" for one would be wrong.
_PYTHON_SUFFIXES = (".py", ".pyi")


class DocstringProducer:
    """Walks the repository's docstrings and emits parameter claims mechanically."""

    def __init__(self, repo_root: str, agent_ver: str) -> None:
        self._repo_root = repo_root
        self._agent_ver = agent_ver

    def _emit(self, pd) -> tuple[EvClaim | None, str | None]:
        """One documented parameter as a claim, or nothing and the cause of the decline.

        The causes stay apart in the coverage record: one aggregate could not say whether a
        parsing filter or a normalization change moved it.
        """
        name = pd.param
        if name.startswith("*") or not _IDENT.match(name) or name in ("self", "cls"):
            return None, "filtered"  # parsing artefacts, not documented parameters
        normalized = predicate_registry["signature_has_param"].normalize(
            pd.doc_line, pd.file, (pd.dotted_name, name)
        )
        if normalized is None:
            return None, "normalize-declined"
        normalization, normalized_args = normalized
        claim = EvClaim(
            anchor=Anchor(doc_path=pd.file, spans=((pd.line_no, pd.line_no),), literal=pd.doc_line),
            check=Check(
                predicate="signature_has_param",
                raw={
                    "literal": pd.doc_line,
                    "doc_path": pd.file,
                    "proposed_args": [pd.dotted_name, name],
                },
                normalization=normalization,
                normalized_args=normalized_args,
            ),
            claim_class=2,
            s_slot=SSlot(note="docstring-documented parameter", confidence=1.0),
            # Every emitted claim is bound; a decline returns above, with its cause.
            provenance={
                "producer": _PRODUCER,
                "agent_ver": self._agent_ver,
                "bind": {"outcome": "bound"},
            },
        )
        return claim, None

    def produce(self, doc_filter: str | None = None) -> tuple[list[EvClaim], dict]:
        """This producer's whole output for one run: its claims and one coverage record.

        The filter is applied inside the iteration, so a parameter doc outside it is never
        normalized. It deliberately does not narrow the symbol-table load: the provider is
        memoized per repository root and shared with the gate's predicates in this process, so
        narrowing it would turn resolvable symbols into skips.

        Args:
            doc_filter: A repository-relative path, matched against a parameter doc's file by
                exact equality. One naming no Python file short-circuits before the provider is
                built, and still writes a coverage record saying why nothing was walked.
        """
        if doc_filter is not None and not doc_filter.endswith(_PYTHON_SUFFIXES):
            return [], self._coverage(
                claims=[],
                symbols=set(),
                contributed=set(),
                filtered_out=0,
                skipped={"filtered": 0, "normalize-declined": 0},
                doc_filter=doc_filter,
                detail=(
                    f"no Python corpus walked: {doc_filter} names no "
                    f"{'/'.join(_PYTHON_SUFFIXES)} file, so this lane has nothing to contribute "
                    f"and skipped the symbol-table load"
                ),
            )
        provider = provider_for(self._repo_root)
        claims: list[EvClaim] = []
        symbols = set()
        contributed = set()
        filtered_out = 0
        skipped = {"filtered": 0, "normalize-declined": 0}
        for pd in provider.documented_param_docs():
            symbols.add(pd.dotted_name)
            if doc_filter is not None and pd.file != doc_filter:
                filtered_out += 1
                continue  # ahead of `_emit`: a filtered-out parameter doc costs no normalize
            claim, decline = self._emit(pd)
            if claim is None:
                skipped[decline] += 1
            else:
                claims.append(claim)
                contributed.add(pd.dotted_name)
        detail = (
            f"{sum(skipped.values())} param docs declined "
            f"({skipped['filtered']} filtered, {skipped['normalize-declined']} normalize-declined)"
        )
        if doc_filter is not None:
            detail += f"; {filtered_out} outside {doc_filter}"
        return claims, self._coverage(
            claims=claims,
            symbols=symbols,
            contributed=contributed,
            filtered_out=filtered_out,
            skipped=skipped,
            doc_filter=doc_filter,
            detail=detail,
        )

    @staticmethod
    def _coverage(*, claims, symbols, contributed, filtered_out, skipped, doc_filter, detail):
        """The one coverage record this producer writes, built in one place.

        The walked path and the short-circuit above share this builder, so they cannot drift into
        two shapes a reader has to reconcile.
        """
        return {
            "unit": "docstring_corpus",
            # Never re-denominated by a filter: this is the whole corpus either way.
            "symbols_walked": len(symbols),
            "symbols_contributed": len(contributed),
            "claims_emitted": len(claims),
            "status": "complete",
            # Split by cause, so a predicate change and the parsing filter can never move the
            # same number. A doc the filter dropped is not a decline, and is counted apart.
            "skipped": dict(skipped),
            "doc_filter": doc_filter,
            "param_docs_filtered_out": filtered_out,
            "detail": detail,
        }
