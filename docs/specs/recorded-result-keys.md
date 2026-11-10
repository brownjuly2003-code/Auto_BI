# Capability: Recorded results name supplied verified assets

Synthetic design decision T-84, 2026-10-22, under ADR 0006. This tightens the
previously documented behavior that silently ignored unknown result keys.

## Requirement: Validate result keys against the full supplied asset repository

`decision verify --results` SHALL accept a key only when it exactly equals a
`metadata.id` in the full repository supplied through `--assets`. The repository
SHALL be loaded once, before results are read. Product scoping SHALL NOT restrict
the set of known keys. No URI parsing, trimming, coercion or version removal applies.

After the existing schema validation of all result values, the first unknown key
in document order SHALL raise a usage error:

`<file> holds a recorded result for unknown verified asset <repr(key)>`

CLI exit is 2, stdout is empty, and the diagnostic escapes control characters in
the key. No environment probe or evidence store is reached. Earlier input refusals
and malformed result value diagnostics retain their priority.

Without `--assets`, the known set is empty: any nonempty results mapping is refused.
An empty mapping or omitted results file remains valid input. Missing answers for
known assets retain their existing reconciliation and provenance grading.

Known assets of other products remain valid keys in a shared results file; their
answers do not become findings or claims of the selected product. Both bare and
attested result forms retain their existing validation and provenance semantics.

## Scenarios

- A valid result plus an unknown key, bare or attested: exit 2 before probe/evidence.
- Only an unknown key, with or without assets: exit 2, rather than a completed run.
- An unknown key followed by a malformed value: the malformed value is reported first.
- A known asset outside the selected product: accepted, not reconciled for that product.
- Missing answers, empty results mapping, or no results file: previous grading remains.

This is an input preflight rule, not a new gate finding. Verifier/check versions
98/73 stay unchanged; completed runs on still-valid inputs preserve their bytes.
Historical bundles are unchanged. Direct `run_gate`/`RecordedResults` port callers
are outside this filesystem use-case contract.
