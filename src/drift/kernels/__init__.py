"""Predicate kernels and the registry that closes the vocabulary."""

from drift.kernels import (  # noqa: F401
    class_has_member,
    link_resolves,
    make_target,
    manifest_key_exists,
    path_exists,
    signature_has_param,
    symbol_resolves,
)
from drift.kernels.registry import Predicate, predicate_registry, register_predicate, vocabulary

__all__ = ["Predicate", "predicate_registry", "register_predicate", "vocabulary"]
