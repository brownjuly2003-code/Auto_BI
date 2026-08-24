# RFC 8785 (JCS) conformance vectors

Source: <https://github.com/cyberphone/json-canonicalization> `testdata/input|output`
(reference corpus of RFC 8785, Apache-2.0), fetched 2026-08-24.

`<name>.input.json` is the raw input, `<name>.expected.json` the canonical output
(the source repo ends each with one newline, stripped by the test).

These are byte-exact fixtures: `.gitattributes` pins them to `-text`, so no checkout
converts their line endings. DECISIONOPS_ARCHITECTURE §9.1 forbids declaring an
implementation "JCS" before it passes official vectors — this corpus is that gate.
