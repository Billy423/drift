from drift.domain.repo import RepoRef
from drift.symbols.griffe_provider import GriffeSymbolProvider
from drift.symbols.provider import SymbolKind


def _write_pkg(root):
    pkg = root / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(
        'def greet(name, punct=\'!\'):\n    """Say hi."""\n    return name\n'
    )


def test_resolve_finds_module_and_member(tmp_path):
    _write_pkg(tmp_path)
    p = GriffeSymbolProvider(RepoRef(path=str(tmp_path)))
    assert p.resolve("mypkg") is not None
    assert p.resolve("mypkg.mod") is not None
    greet = p.resolve("mypkg.mod.greet")
    assert greet is not None
    assert greet.dotted_name == "mypkg.mod.greet"
    assert greet.kind == SymbolKind.FUNCTION


def test_resolve_returns_none_for_missing_and_external(tmp_path):
    _write_pkg(tmp_path)
    p = GriffeSymbolProvider(RepoRef(path=str(tmp_path)))
    assert p.resolve("mypkg.mod.nonexistent") is None
    assert p.resolve("os.path.join") is None


def test_resolve_handles_src_layout(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_pkg(src)
    p = GriffeSymbolProvider(RepoRef(path=str(tmp_path)))
    assert p.resolve("mypkg.mod.greet") is not None


def _write_documented_pkg(root):
    pkg = root / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(
        "def greet(name, punct='!'):\n"
        '    """Say hi.\n'
        "\n"
        "    Args:\n"
        "        name: who to greet.\n"
        "        title: a param the code does NOT have.\n"
        '    """\n'
        "    return name\n"
        "\n"
        "def undocumented(x):\n"
        "    return x\n"
    )


def test_documented_symbols_yields_signature_and_claim(tmp_path):
    _write_documented_pkg(tmp_path)
    p = GriffeSymbolProvider(RepoRef(path=str(tmp_path)))
    docs = {s.dotted_name: s for s in p.documented_symbols()}
    assert set(docs) == {"mypkg.mod.greet"}
    greet = docs["mypkg.mod.greet"]
    assert tuple(pm.name for pm in greet.signature.parameters) == ("name", "punct")
    assert greet.signature.parameters[1].has_default is True
    assert greet.docstring.documented_params == ("name", "title")


def test_documented_symbol_carries_repo_relative_location(tmp_path):
    _write_documented_pkg(tmp_path)
    p = GriffeSymbolProvider(RepoRef(path=str(tmp_path)))
    greet = {s.dotted_name: s for s in p.documented_symbols()}["mypkg.mod.greet"]
    assert greet.location is not None
    assert greet.location.file == "mypkg/mod.py"  # relative to repo root, not absolute
    assert greet.location.start_line >= 1
    assert greet.location.end_line >= greet.location.start_line


def test_resolve_populates_signature_for_function(tmp_path):
    _write_pkg(tmp_path)  # defines mypkg.mod.greet(name, punct='!')
    p = GriffeSymbolProvider(RepoRef(path=str(tmp_path)))
    greet = p.resolve("mypkg.mod.greet")
    assert greet is not None and greet.signature is not None
    assert tuple(pm.name for pm in greet.signature.parameters) == ("name", "punct")


def test_resolve_populates_location_for_function(tmp_path):
    _write_pkg(tmp_path)  # defines mypkg.mod.greet on line 1 of mypkg/mod.py
    p = GriffeSymbolProvider(RepoRef(path=str(tmp_path)))
    greet = p.resolve("mypkg.mod.greet")
    assert greet is not None and greet.location is not None
    assert greet.location.file == "mypkg/mod.py"
    assert greet.location.start_line == 1
    assert greet.location.end_line >= greet.location.start_line


def test_resolve_location_none_for_missing(tmp_path):
    _write_pkg(tmp_path)
    p = GriffeSymbolProvider(RepoRef(path=str(tmp_path)))
    assert p.resolve("mypkg.mod.nonexistent") is None


def _write_class_pkg(root):
    pkg = root / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "client.py").write_text(
        "class Client:\n"
        "    def __init__(self, url, timeout=5):\n"
        "        self.url = url\n"
        "\n"
        "class Plain:\n"
        "    pass\n"
    )


def test_resolve_builds_class_signature_from_init(tmp_path):
    _write_class_pkg(tmp_path)
    p = GriffeSymbolProvider(RepoRef(path=str(tmp_path)))
    sym = p.resolve("mypkg.client.Client")
    assert sym is not None
    assert sym.kind == SymbolKind.CLASS
    assert sym.signature is not None
    # constructor params, self stripped — the surface a doc call site is checked against
    assert tuple(pm.name for pm in sym.signature.parameters) == ("url", "timeout")


def test_resolve_class_without_own_init_has_no_signature(tmp_path):
    _write_class_pkg(tmp_path)
    p = GriffeSymbolProvider(RepoRef(path=str(tmp_path)))
    sym = p.resolve("mypkg.client.Plain")
    assert sym is not None and sym.kind == SymbolKind.CLASS
    assert sym.signature is None  # no own __init__ → kwargs unknowable → FP-safe skip


def test_resolve_builds_dataclass_signature_from_synthesized_init(tmp_path):
    # FP-safety lock: griffe synthesizes a dataclass __init__; its fields are the constructor
    # surface, so a doc kwarg check against a dataclass is a true positive, not skipped
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "conf.py").write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\nclass Config:\n    x: int\n    y: str = 'a'\n"
    )
    p = GriffeSymbolProvider(RepoRef(path=str(tmp_path)))
    sym = p.resolve("mypkg.conf.Config")
    assert sym is not None and sym.signature is not None
    assert tuple(pm.name for pm in sym.signature.parameters) == ("x", "y")


def test_the_provider_resolves_nothing_in_a_repository_that_does_not_exist():
    """A reader pointed at an absent tree answers empty rather than raising."""
    provider = GriffeSymbolProvider(RepoRef(path="/tmp/repo"))
    assert provider.resolve("os.path.join") is None
    assert list(provider.documented_symbols()) == []


def test_loader_refuses_inspection(tmp_path, monkeypatch):
    """The scanned tree is untrusted, and Griffe imports what it cannot parse.

    A construction test rather than a behavioural one: provoking the fallback needs a module the
    parser rejects and the interpreter accepts, which in practice means shipping a compiled
    artifact into the suite. The keyword is the whole guarantee, so the keyword is what is pinned.
    """
    import griffe

    seen = {}
    real = griffe.GriffeLoader

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(griffe, "GriffeLoader", spy)
    _write_pkg(tmp_path)
    GriffeSymbolProvider(RepoRef(path=str(tmp_path))).resolve("mypkg.mod.greet")

    assert seen["allow_inspection"] is False
