"""The language-neutral symbol model: what a reader returns for one symbol.

A scan reaches a reader through `kernels.pysymbols.provider_for`, which constructs the Python
reader directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from drift.domain.findings import Location


class SymbolKind(StrEnum):
    """What kind of code entity a `Symbol` names."""

    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    MODULE = "module"
    ATTRIBUTE = "attribute"


@dataclass(frozen=True)
class Parameter:
    """One parameter of a callable, as the code declares it rather than as a docstring says.

    Attributes:
        kind: A griffe `ParameterKind` value, such as `"positional or keyword"`.
    """

    name: str
    kind: str
    has_default: bool


@dataclass(frozen=True)
class Signature:
    """A callable's parameter list, read from the code — what a docstring is checked against."""

    parameters: tuple[Parameter, ...]


@dataclass(frozen=True)
class ParamDoc:
    """One documented parameter, with the doc line that names it and where that line sits.

    Attributes:
        doc_line: The stripped docstring line, which is the anchor: a bare parameter name
            occurs all over a file, and the line is what repairing the docstring deletes.
        line_no: Approximate — the docstring's own start plus an offset into its cleaned
            text. Good enough to point a reader at, not to edit by.
    """

    dotted_name: str
    param: str
    doc_line: str
    file: str
    line_no: int
    signature: Signature


@dataclass(frozen=True)
class DocstringClaim:
    """What a docstring asserts about a symbol: only the parameter names it documents.

    Built by `documented_symbols` alone, which nothing in `drift` calls.
    """

    documented_params: tuple[str, ...]


@dataclass(frozen=True)
class Symbol:
    """One symbol, normalized so a caller never learns which language it came from.

    Everything language-specific stays inside the reader that builds this.
    """

    dotted_name: str
    kind: str
    location: Location | None = None
    signature: Signature | None = None
    docstring: DocstringClaim | None = None
