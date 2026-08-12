"""The document half of the replay check: is the anchor text still where a claim said it was?"""

from __future__ import annotations

import os


def doc_contains(repo_root: str, doc_path: str, literal: str) -> bool:
    """Is `literal` still present in the document at `doc_path`?

    A missing file is an absent anchor and answers False. Anything else that stops the read is not
    an answer and propagates, so a caller cannot mistake "I could not look" for "it is gone".

    Decoded with `errors="replace"`, matching how the document was read when the claim was
    made: a stray non-UTF-8 byte must yield the same text here, not an exception.

    Raises:
        OSError: the document exists but could not be read.
    """
    try:
        with open(os.path.join(repo_root, doc_path), "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return False
    return literal in data.decode("utf-8", errors="replace")
