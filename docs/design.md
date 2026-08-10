# loc-polsia current design

## Status

The current checkpoint implements the local V1 `loc-polsia check` product:
configuration admission, deterministic discovery, frozen descriptor-relative
reads, strict-UTF-8 nonblank physical-line counting, baseline reconciliation,
ratchet policy, and deterministic text/JSON output.

Deferred scope includes baseline generation or editing, inventory/proposals,
autofix, language parsing, Git-root inference, upward search, hooks, CI,
plugins, hosted state, consumer integration, publication, and deployment.

## North star and non-goals

**North star:** provide a local, deterministic, offline, read-only repository
line-policy and ratchet check.

V1 reads only the process current working directory and its
`.loc-polsia.toml`. It does not write, contact a network, inspect Git history,
search for another root, or interpret source-language syntax. It is a
file/glob-based line checker, not a language-aware analyzer.

## Data flow

```text
CLI: loc-polsia check [--format text|json]
  |
  v
open current directory + admit .loc-polsia.toml
  |
  v
validate exact config schema and patterns
  |
  v
descriptor-relative, no-follow discovery
  |
  v
freeze and deterministically order candidates
  |
  v
descriptor-relative bounded reads of regular files
  |
  v
strict UTF-8 -> nonblank physical-line counts
  |
  v
reconcile exact-path baselines -> evaluate ratchet policy
  |
  v
canonical result -> deterministic text or JSON on stdout
```

## Module ownership

| Module | Responsibility |
|---|---|
| `src/loc_polsia/__main__.py` | `argparse` command surface, current-directory check, format selection, stdout emission, and fail-closed internal-error handling |
| `src/loc_polsia/filesystem.py` | Trusted-root admission, config read, deterministic traversal, no-follow classification, frozen candidates, bounded source reads, and filesystem error mapping |
| `src/loc_polsia/config.py` | Exact TOML schema validation, path and pattern grammar, matching, canonical path display/order, and config byte limit |
| `src/loc_polsia/check.py` | Strict-UTF-8 line counting, baseline reconciliation, ratchet policy, result/exit selection, and format projection |
| `src/loc_polsia/protocol.py` | Closed result/finding schema, error/action mapping, canonical ordering, and byte-exact text/JSON serialization |
| `src/loc_polsia/__init__.py` | Narrow package export of `check_root` |

## Configuration and semantics

The only configuration file is `.loc-polsia.toml` directly under the current
working directory. The exact top-level schema is:

```toml
schema = "loc-polsia.config/v1"
include = ["src/**/*.py"]
exclude = ["**/generated/**"]
target_lines = 500
hard_lines = 700
count = "nonblank_physical_lines"

[baselines]
"src/legacy.py" = { lines = 900, reason = "Reviewed debt at adoption" }
```

- The top-level keys are exactly `schema`, `include`, `exclude`,
  `target_lines`, `hard_lines`, `count`, and `baselines`.
- `include` is non-empty; both pattern lists contain unique relative POSIX
  segment patterns; exclusion wins.
- `target_lines >= 1`; `hard_lines > target_lines`; booleans are not integers.
- `count` is exactly `nonblank_physical_lines`.
- Baseline keys are exact decoded repository-relative paths. Entries contain
  exactly an integer `lines > hard_lines` and a nonblank `reason`.

Pattern `*` stays within one decoded segment. Complete-segment `**` spans zero
or more decoded segments. `.git` is always pruned, and `.gitignore` is not
consulted.

Source bytes are decoded as strict UTF-8 and split into physical lines. A line
counts when its decoded content is not whitespace-only. Comments and final
unterminated lines count; empty or whitespace-only lines do not. A BOM is
content. No parser or language semantics participate.

## Policy, results, and exits

Without a baseline, counts at or below the target pass silently; counts above
the target through the hard limit are warnings; counts above the hard limit
fail. A reviewed baseline above the hard limit passes only at exact debt.
Growth, a baseline left loose after reduction, or a baseline left after the
file reaches the hard limit fails and reports the required next action.

Every validly parsed check emits one `loc-polsia.result/v1` result on stdout.
Text and JSON are projections of the same ordered result; both end with one LF.
`checked=0` is a valid pass when no regular files match the configured patterns;
callers that expected files must verify the current directory and patterns.
Runtime results leave stderr empty. Invalid CLI syntax is handled by
`argparse`, outside the result envelope.

| Exit | Result |
|---:|---|
| `0` | Pass, warning, or exact baseline debt |
| `1` | Policy failure |
| `2` | Config, filesystem, source, serialization, or internal error |

## Filesystem safety claim ceiling

The implemented boundary opens the trusted root once, uses descriptor-relative
no-follow metadata and admission, rejects non-regular admitted entries before
reading, freezes all candidates before source reads, and bounds config/source
reads. Symlink targets are not read. Type changes or disappearance between
discovery and admission fail closed.

The current checkpoint covers descriptor-relative, no-follow admission and
regular-file `fstat` and type-change behavior in production code and tests. The
evidence ceiling remains the exercised implementation and tests; it does not
prove all kernel or filesystem behavior.

V1 does not claim mount, bind, or magic-link topology containment, and it does
not claim content immutability after a regular-file descriptor is admitted.

## Language and file model

Selection is entirely path-pattern based. `.py` is a configuration example,
not a privileged language. JavaScript, TypeScript, Go, Rust, Markdown, and any
other selected strict-UTF-8 regular file are handled by the same byte-read,
decode, and physical-line-count path.

## Public surface and developer commands

Installed command:

```bash
loc-polsia check
loc-polsia check --format json
```

Source-tree command, run with the checked repository as the current directory:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/absolute/path/to/loc-polsia/src python3 -B -m loc_polsia check
```

The package also exports `loc_polsia.check_root` as its narrow Python surface.

Install for local development:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Run all tests without bytecode caches:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

## Document authority note

This public snapshot keeps current product status and usage in `README.md` and
this document. Internal planning, reconstruction, and session-derived approval
artifacts are intentionally excluded; they are not needed to run the tool and
must not be treated as public product documentation.
