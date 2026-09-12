# Evidence signing

## Goal

Add an opt-in Ed25519 trust root that signs canonical evidence manifests and is independently verified when evidence is read.

## Tasks

- [x] Pin the crypto dependency and define the Ed25519 signer/verifier port, key fingerprint, and signing payload. → Verify: focused unit tests reject wrong keys, altered manifests, and malformed signatures.
- [x] Wire optional signing into `BundleWriter`, the release gate, and `VerifyRequest`. → Verify: a signed gate bundle self-verifies while unsigned bundles remain byte-compatible.
- [x] Add `--signing-key` and `--trust-key` CLI boundaries without exposing private-key bytes. → Verify: signed CLI output passes only with the matching trust root and artifact reads enforce it.
- [x] Record the trust-root decision in ADR 0005, architecture, current state, changelog, and CLI documentation. → Verify: docs-as-code and targeted searches agree with runtime constants.
- [x] Run focused tests, package-wide lint/typecheck, then the full offline suite. → Verify: all commands exit 0; no WSL, Docker, push, or deploy.

## Done When

- [x] `integrity=signed` is emitted only after Ed25519 self-verification and independently passes with the matching public trust root.
- [x] Missing/wrong trust roots and signature or manifest tampering cannot produce a passing inspection.
- [x] Private key material is never persisted in evidence, logs, docs, or Git.
