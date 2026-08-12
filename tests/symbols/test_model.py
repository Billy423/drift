from drift.symbols.provider import (
    DocstringClaim,
    Parameter,
    Signature,
    Symbol,
    SymbolKind,
)


def test_symbol_carries_signature_and_docstring_claim():
    sig = Signature(parameters=(Parameter("name", "positional or keyword", False),))
    claim = DocstringClaim(documented_params=("name", "title"))
    sym = Symbol(
        dotted_name="pkg.mod.greet",
        kind=SymbolKind.FUNCTION,
        signature=sig,
        docstring=claim,
    )
    assert sym.signature.parameters[0].name == "name"
    assert sym.docstring.documented_params == ("name", "title")


def test_symbol_defaults_keep_m0_callsites_working():
    sym = Symbol(dotted_name="pkg", kind=SymbolKind.MODULE)
    assert sym.signature is None and sym.docstring is None
