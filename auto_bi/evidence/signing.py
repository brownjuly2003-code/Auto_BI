"""Ed25519 signing and trust-root verification for evidence manifests.

Private keys enter only through an explicit filesystem path and are never serialized into
the evidence bundle.  A trust root is one or more pinned Ed25519 public keys; their stable
key ids are SHA-256 fingerprints of the raw 32-byte public keys.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from auto_bi import filesystem
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.evidence.model import EvidenceSignature

ED25519_ALGORITHM = "ed25519"


class EvidenceSignatureError(Exception):
    """A key could not be loaded or a signature could not be trusted."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class EvidenceVerifier(Protocol):
    """Out-of-band trust root used to authenticate a manifest signature."""

    def verify(self, payload: bytes, signature: EvidenceSignature) -> None: ...


class EvidenceSigner(EvidenceVerifier, Protocol):
    """Private-key port used only while sealing a bundle."""

    @property
    def algorithm(self) -> str: ...

    @property
    def key_id(self) -> str: ...

    def sign(self, payload: bytes) -> str: ...


class Ed25519TrustStore:
    """A set of explicitly pinned Ed25519 public keys, indexed by fingerprint."""

    def __init__(self, keys: list[Ed25519PublicKey]) -> None:
        if not keys:
            raise EvidenceSignatureError(
                "evidence.signature_trust_root_empty",
                "the Ed25519 trust root must contain at least one public key",
            )
        self._keys = {_key_id(key): key for key in keys}

    def verify(self, payload: bytes, signature: EvidenceSignature) -> None:
        if signature.algorithm != ED25519_ALGORITHM:
            raise EvidenceSignatureError(
                "evidence.signature_algorithm_unsupported",
                f"signature algorithm {signature.algorithm!r} is not {ED25519_ALGORITHM}",
            )
        key = self._keys.get(signature.key_id)
        if key is None:
            raise EvidenceSignatureError(
                "evidence.signature_key_untrusted",
                f"signature key {signature.key_id!r} is not in the configured trust root",
            )
        raw = _decode_signature(signature.value)
        try:
            key.verify(raw, payload)
        except InvalidSignature as exc:
            raise EvidenceSignatureError(
                "evidence.signature_invalid",
                f"signature from key {signature.key_id!r} does not authenticate the manifest",
            ) from exc


class Ed25519Signer:
    """In-memory Ed25519 private key with a matching public-key verifier."""

    def __init__(self, key: Ed25519PrivateKey) -> None:
        self._key = key
        self._trust = Ed25519TrustStore([key.public_key()])

    @property
    def algorithm(self) -> str:
        return ED25519_ALGORITHM

    @property
    def key_id(self) -> str:
        return _key_id(self._key.public_key())

    def sign(self, payload: bytes) -> str:
        return _encode_signature(self._key.sign(payload))

    def verify(self, payload: bytes, signature: EvidenceSignature) -> None:
        self._trust.verify(payload, signature)


def load_ed25519_signer(path: str | Path) -> Ed25519Signer:
    """Load one unencrypted PKCS#8 PEM private key without exposing its bytes in errors."""
    source = Path(path)
    try:
        pem = source.read_bytes()
    except (OSError, UnicodeEncodeError) as exc:
        raise EvidenceSignatureError(
            "evidence.signing_key_unreadable",
            f"cannot read Ed25519 private key {filesystem.display_path(source)}",
        ) from exc
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
        raise EvidenceSignatureError(
            "evidence.signing_key_invalid",
            f"cannot load Ed25519 PKCS#8 private key {source}",
        ) from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise EvidenceSignatureError(
            "evidence.signing_key_invalid",
            f"private key {source} is not Ed25519",
        )
    return Ed25519Signer(key)


def load_ed25519_trust_store(paths: list[str | Path]) -> Ed25519TrustStore:
    """Load pinned SubjectPublicKeyInfo PEM files as one local trust root."""
    keys: list[Ed25519PublicKey] = []
    for path in paths:
        source = Path(path)
        try:
            pem = source.read_bytes()
        except (OSError, UnicodeEncodeError) as exc:
            raise EvidenceSignatureError(
                "evidence.trust_key_unreadable",
                f"cannot read Ed25519 public trust key {filesystem.display_path(source)}",
            ) from exc
        try:
            key = serialization.load_pem_public_key(pem)
        except (ValueError, UnsupportedAlgorithm) as exc:
            raise EvidenceSignatureError(
                "evidence.trust_key_invalid",
                f"cannot load Ed25519 public trust key {source}",
            ) from exc
        if not isinstance(key, Ed25519PublicKey):
            raise EvidenceSignatureError(
                "evidence.trust_key_invalid",
                f"public trust key {source} is not Ed25519",
            )
        keys.append(key)
    return Ed25519TrustStore(keys)


def _key_id(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return digest_bytes(raw)


def _encode_signature(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_signature(value: str) -> bytes:
    try:
        encoded = value.encode("ascii")
        raw = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise EvidenceSignatureError(
            "evidence.signature_invalid", "Ed25519 signature is not canonical base64url"
        ) from exc
    if len(raw) != 64 or _encode_signature(raw) != value:
        raise EvidenceSignatureError(
            "evidence.signature_invalid", "Ed25519 signature is not canonical base64url"
        )
    return raw


__all__ = [
    "ED25519_ALGORITHM",
    "Ed25519Signer",
    "Ed25519TrustStore",
    "EvidenceSignatureError",
    "EvidenceSigner",
    "EvidenceVerifier",
    "load_ed25519_signer",
    "load_ed25519_trust_store",
]
