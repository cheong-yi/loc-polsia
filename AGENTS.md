# Repository instructions for coding agents

## Read first

1. Read `README.md` for the user workflow and current product boundary.
2. Read `docs/design.md` for current architecture, module ownership, status,
   and the filesystem claim ceiling.
3. Before changing behavior, inspect the owning source and test modules named
   below and preserve the documented V1 contract.

Internal planning, reconstruction, and session-derived approval artifacts are
intentionally omitted from this public snapshot; do not infer authority or
current status from files outside the repository.

## Environment and commands

Requirements are Linux/POSIX, Python >= 3.11, and the standard library at
runtime.

Local editable install:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Run that install block from the `loc-polsia` checkout—the directory containing
`pyproject.toml`. The repository being checked is a separate current working
directory. If installation is unavailable or an offline source-tree run is
preferred, use the command below instead.

Run the product from source, with the repository to check as the current
directory:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/absolute/path/to/loc-polsia/src python3 -B -m loc_polsia check
```

Run the full test suite from the `loc-polsia` checkout:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

Use `PYTHONDONTWRITEBYTECODE=1` and `python3 -B` for Python probes. Prefer AST
or import-free static checks when they suffice, and avoid cache-generating
checks such as unguarded imports, `compileall`, or tools that create repository
caches.

## Product invariants

- The process current working directory is the only checked root.
- `.loc-polsia.toml` directly under that root is the only configuration.
- There is no upward search or Git-root inference.
- Runtime behavior is read-only, offline, deterministic, and standard-library
  only.
- V1 has no baseline generator or editor; `[baselines]` entries are manually
  authored reviewed debt.
- Do not add writes, network access, autofix, baseline writing, parsers, or
  language-specific behavior unless a task explicitly changes the contract.
- Preserve descriptor-relative, no-follow admission; never read symlink targets
  or weaken regular-file checks.
- Preserve frozen candidate ordering, fixed error precedence, closed result
  fields/actions, byte-exact text/JSON projections, stdout/stderr rules, and
  exit codes `0/1/2`.
- Treat `checked=0` as a valid empty selection, not proof that the intended
  files were checked; verify the current directory and patterns when a check
  unexpectedly selects nothing.
- Do not expand safety claims to mount/bind/magic-link containment or
  post-admission content immutability.

## Change discipline

- Keep every change inside the task's explicit path allowlist.
- Do not modify generated/ignored files or another workspace.
- Do not install packages, contact remotes, change hooks/CI, commit, or push
  unless the task explicitly authorizes that action.
- Add or update tests before changing behavior, then run focused tests and the
  full no-bytecode suite.
- Verify the final changed-path union, staged state, and `git diff --check`
  before reporting completion.

## Where behavior belongs

- CLI parsing, stdout, and fail-closed entry behavior:
  `src/loc_polsia/__main__.py`; tests in `tests/test_cli.py`.
- Filesystem admission, traversal, frozen reads, and safety errors:
  `src/loc_polsia/filesystem.py`; tests in
  `tests/test_filesystem_safety.py`.
- TOML schema, paths, patterns, and canonical ordering:
  `src/loc_polsia/config.py`; tests in `tests/test_contract.py`.
- Counting, baselines, and ratchet policy: `src/loc_polsia/check.py`; tests in
  `tests/test_contract.py` and `tests/test_policy.py`.
- Result envelopes and text/JSON bytes: `src/loc_polsia/protocol.py`; tests in
  `tests/test_protocol.py`.
- Keep `src/loc_polsia/__init__.py` narrow; import/package-surface coverage
  belongs in `tests/test_cli.py`.

For a behavior change, add the smallest focused regression in the owning test
module. For cross-boundary behavior, add an end-to-end CLI test as well as the
lower-level test; do not move core rules into the CLI.
