# Persisted schema policy

The harness uses JSON Schema 2020-12 with `schema_version: "1.0"` on every
language-neutral record. Minor, backward-compatible additions require a new
minor schema version and readers must continue accepting the previous version.
Removing a field, changing its type or semantics, or changing an identifier
requires a new major schema directory and an explicit migration. A run stores
the resolved manifest and provenance that produced it; existing records are
never rewritten in place.

The canonical schemas are packaged under `calibration/schemas/` because they
are loaded by the runtime. This directory documents the compatibility policy
for contributors and independent readers.
