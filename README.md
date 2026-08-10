# loc-polsia

`loc-polsia` is a local, deterministic, offline, read-only repository line-policy and ratchet checker.

Use it to keep selected files within reviewed line limits and to prevent accepted
brownfield debt from growing, without a service, a Git integration, or
language-specific tooling.

## Quickstart

### Requirements

- Linux/POSIX
- Python >= 3.11
- No runtime dependencies

### Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Run these commands from the `loc-polsia` checkout—the directory containing
`pyproject.toml`—not from the repository you want to check. The editable
install installs the checker; it does not create `.loc-polsia.toml` in a
target repository.

For source-tree development, you can skip installation and set `PYTHONPATH` to
the absolute path of this checkout's `src/` directory instead.
The checker has no runtime dependencies. The editable install uses the build
backend declared in `pyproject.toml` (`setuptools>=68`); if that build tool is
unavailable or you want an offline first run, use the source-tree form below.

### Configure the repository to check

Create `.loc-polsia.toml` in that repository's root:

```toml
schema = "loc-polsia.config/v1"
include = ["src/**/*.py"]
exclude = ["**/generated/**"]
target_lines = 500
hard_lines = 700
count = "nonblank_physical_lines"

[baselines]
```

The top-level keys are exact; none may be omitted or added.

- `schema` selects the fixed V1 configuration schema.
- `include` is a non-empty list of unique repository-relative patterns.
- `exclude` is a list of unique patterns; exclusion wins over inclusion.
- `target_lines` is the preferred maximum. Files above it produce warnings.
- `hard_lines` is the enforced maximum and must be greater than the target.
- `count` must be `nonblank_physical_lines`.
- `baselines` records reviewed debt above `hard_lines`; it may be empty.

V1 has no baseline-generation or baseline-editing command. Leave `[baselines]`
empty for a clean adoption, or add exact reviewed entries manually; the
checker never writes them.

### Run

From the root of the repository being checked:

```bash
loc-polsia check
loc-polsia check --format json
```

Source-tree form:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/absolute/path/to/loc-polsia/src python3 -B -m loc_polsia check
```

The command's current directory is the repository being checked. It does not
need to be the `loc-polsia` checkout.

For a complete source-tree first run, set the two paths, create the config in
the target repository, and run the checker:

```bash
TOOL=/absolute/path/to/loc-polsia
TARGET=/absolute/path/to/repository-to-check
cd "$TARGET"
cat > .loc-polsia.toml <<'TOML'
schema = "loc-polsia.config/v1"
include = ["src/**/*.py"]
exclude = ["**/generated/**"]
target_lines = 500
hard_lines = 700
count = "nonblank_physical_lines"

[baselines]
TOML
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$TOOL/src" python3 -B -m loc_polsia check
```

`TOOL` is the `loc-polsia` checkout; `TARGET` is the repository being checked.
They may be the same directory, but the command always checks `TARGET`'s
current directory.

A minimal passing text result is:

```text
PASS checked=1 warnings=0 failures=0 debt=0 errors=0
```

The same result as JSON is:

```json
{"schema":"loc-polsia.result/v1","status":"pass","summary":{"checked":1,"warnings":0,"failures":0,"debt":0,"errors":0},"findings":[]}
```

`checked` is the frozen candidate count. Findings identify warnings, policy
failures, reviewed debt, or errors and include finite `legal_next_actions`.
`checked=0` is a valid pass when the patterns match no regular files; if you
expected files to be checked, verify the current directory and the include and
exclude patterns before trusting the result.

| Exit | Meaning |
|---:|---|
| `0` | Pass, including warnings and unchanged baseline debt |
| `1` | Policy failure |
| `2` | Configuration, filesystem, source, or internal error |

Validly parsed `check` commands write one text or JSON result to stdout and
leave stderr empty. CLI syntax errors are ordinary `argparse` errors outside
the result envelope: stdout is empty, stderr contains usage/error text, and the
exit code is `2`.

## Product boundary

`loc-polsia` reads only the current working directory and its
`.loc-polsia.toml`. It does not search upward, infer a Git root, write files,
contact a network, autofix, install hooks, integrate with CI, write baselines,
parse source languages, use hosted state, or perform language-aware analysis.

### What gets counted

Selected regular files must be strict UTF-8. The checker counts nonblank
physical lines using decoded line boundaries and whitespace:

- comments count;
- a final unterminated line counts;
- empty and whitespace-only lines do not count;
- no parser or language semantics are involved.

`.py` is only an example. JavaScript, TypeScript, Go, Rust, Markdown, and other
strict-UTF-8 regular files work when the configured patterns select them. The
tool decodes file bytes as UTF-8 and counts lines; it does not parse any
language.

### Pattern rules

Patterns are case-sensitive, repository-relative POSIX segment patterns.

- `*` matches zero or more characters within one path segment.
- A complete-segment `**` matches zero or more complete segments.
- Exclusion wins over inclusion.
- `.git` is always pruned.
- `.gitignore` has no implicit effect.

For example, `src/**/*.py` matches both `src/a.py` and
`src/nested/a.py`.

### Baselines

Baselines are only for reviewed debt strictly above `hard_lines`. Each key is
one exact repository-relative path, and each value contains exactly `lines`
and a nonblank `reason`:

```toml
[baselines]
"src/legacy.py" = { lines = 900, reason = "Reviewed debt at adoption" }
```

The recorded line count is a ratchet: growth fails, a reduced count above the
hard limit requires tightening the baseline, and reaching the hard limit
requires removing it. There is no baseline creation or editing command.

### Filesystem safety ceiling

Admission is descriptor-relative and no-follow. Symlink targets are not read,
included non-regular entries are rejected, and the complete candidate set is
frozen before source reads. This claim does **not** include containment against
mount, bind, or magic-link topology, or content immutability after a regular
file descriptor has been admitted.

## Development

Run the standard-library test suite without creating bytecode caches:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

## Documentation map

- `README.md`: user quickstart and operating boundary.
- `AGENTS.md`: repository instructions for coding agents.
- `docs/design.md`: current architecture and implementation status.

Internal planning, reconstruction, and session-derived approval artifacts are
intentionally excluded from the public snapshot.
