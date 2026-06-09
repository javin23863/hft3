"""Exceptions for the HFT3 certification registry hardening (Phase 11)."""
from __future__ import annotations


class RegistryError(Exception):
    """Base class for registry errors. Carries a stable error_code."""

    error_code: str = "registry_error"

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code


class RegistryLockTimeout(RegistryError):
    error_code = "registry_lock_timeout"


class RegistryCorruptError(RegistryError):
    error_code = "registry_corrupt"


class HashChainBroken(RegistryError):
    error_code = "hash_chain_broken"


class RegistrySchemaError(RegistryError):
    error_code = "registry_schema"


class HashChainUnavailable(RegistryError):
    """Raised when no chain is found in either the JSONL log or the legacy mirror."""
    error_code = "hash_chain_unavailable"
