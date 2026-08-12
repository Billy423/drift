"""documented_param_docs: per-param doc line extraction with spans."""

from drift.domain.repo import RepoRef
from drift.symbols.griffe_provider import GriffeSymbolProvider

_MOD = '''
def send(msg, error_msg=None):
    """Send a message.

    Parameters
    ----------
    msg : str
        The message.
    error_msg : str
        Shown on failure.
    """
    return msg
'''


def _provider(tmp_path):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(_MOD)
    return GriffeSymbolProvider(RepoRef(path=str(tmp_path)))


def test_param_doc_lines_extracted(tmp_path):
    docs = list(_provider(tmp_path).documented_param_docs())
    by_param = {d.param: d for d in docs}
    assert by_param["error_msg"].doc_line == "error_msg : str"
    assert by_param["error_msg"].file == "mypkg/mod.py"
    assert by_param["error_msg"].line_no > 0
    assert by_param["error_msg"].dotted_name == "mypkg.mod.send"
