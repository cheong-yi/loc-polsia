"""Pure counting and ratchet-policy core for loc-polsia check."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .config import Config, MAX_CONFIG_BYTES, canonical_path, validate_candidate_path, validate_path
from .protocol import error_result, finding, json_bytes, policy_result, text_bytes


MAX_SOURCE_BYTES = 8_388_608


class SourceError(ValueError):
    """A bounded source read or strict UTF-8 decoding failure."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def count_nonblank_physical_lines(data: bytes, *, max_bytes: int = MAX_SOURCE_BYTES) -> int:
    """Count nonblank ``str.splitlines`` records in strict UTF-8 bytes."""

    if len(data) > max_bytes:
        raise SourceError("source_too_large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceError("invalid_utf8") from exc
    return sum(bool(line.strip()) for line in text.splitlines())


count_lines = count_nonblank_physical_lines


_POLICY = {
    "above_target": ("warning", ("reduce_before_growth",)),
    "unbaselined_over_hard": (
        "failure",
        ("split_cohesive_domain", "request_reviewed_baseline_exception"),
    ),
    "baseline_debt": ("debt", ("reduce_cohesive_debt",)),
    "baseline_growth": (
        "failure",
        ("remove_growth", "split_cohesive_domain", "request_reviewed_baseline_increase"),
    ),
    "baseline_not_tight": ("failure", ("lower_baseline_to_current",)),
    "stale_baseline_compliant": ("failure", ("remove_baseline",)),
}


def _policy_row(
    path: str,
    code: str,
    lines: int,
    target: int,
    hard: int,
    baseline: int | None,
) -> dict[str, object]:
    severity, actions = _POLICY[code]
    return finding(path, code, severity, lines, target, hard, baseline, actions)


def evaluate_policy(
    counts: Mapping[str, int],
    target_lines: int,
    hard_lines: int,
    baselines: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    """Return one exhaustive, path/code-ordered policy row per finding."""

    baseline_values = baselines or {}
    rows: list[dict[str, object]] = []
    missing = object()
    for path in sorted(counts, key=lambda value: canonical_path(validate_candidate_path(value))):
        lines = counts[path]
        if not isinstance(lines, int) or isinstance(lines, bool) or lines < 0:
            raise ValueError("count must be a non-negative integer")
        baseline_entry = baseline_values.get(path, missing)
        if baseline_entry is missing:
            baseline = None
        else:
            if not isinstance(baseline_entry, Mapping) or set(baseline_entry) != {"lines", "reason"}:
                raise ValueError("baseline entry must contain exactly lines and reason")
            raw_baseline = baseline_entry["lines"]
            if not isinstance(raw_baseline, int) or isinstance(raw_baseline, bool) or raw_baseline <= hard_lines:
                raise ValueError("baseline lines must be an integer > hard_lines")
            reason = baseline_entry["reason"]
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("baseline reason must be non-empty and non-whitespace")
            baseline = raw_baseline
        if baseline_entry is missing:
            if lines <= target_lines:
                continue
            code = "above_target" if lines <= hard_lines else "unbaselined_over_hard"
        elif lines > baseline:
            code = "baseline_growth"
        elif lines == baseline:
            code = "baseline_debt"
        elif lines > hard_lines:
            code = "baseline_not_tight"
        else:
            code = "stale_baseline_compliant"
        rows.append(_policy_row(canonical_path(validate_candidate_path(path)), code, lines, target_lines, hard_lines, baseline))
    return rows


def stale_baseline(counts: Mapping[str, int], baselines: Mapping[str, object]) -> str | None:
    """Return the first baseline key absent from the frozen candidate set."""

    for path in sorted(baselines, key=lambda value: canonical_path(validate_path(value))):
        if path not in counts:
            return path
    return None


def evaluate_counts(counts: Mapping[str, int], config: Config) -> tuple[dict[str, object], int]:
    """Reconcile baselines, evaluate policy, and return result plus exit code."""

    stale = stale_baseline(counts, config.baselines)
    if stale is not None:
        return error_result("stale_baseline_path", canonical_path(validate_path(stale))), 2
    rows = evaluate_policy(counts, config.target_lines, config.hard_lines, config.baselines)
    result = policy_result(len(counts), rows)
    return result, 1 if result["status"] == "fail" else 0


def project(result: Mapping[str, object], format: str = "json") -> bytes:
    if format == "json":
        return json_bytes(result)
    if format == "text":
        return text_bytes(result)
    raise ValueError("format must be json or text")


@dataclass(frozen=True)
class CheckResult:
    result: dict[str, object]
    exit_code: int


__all__ = [
    "CheckResult",
    "MAX_CONFIG_BYTES",
    "MAX_SOURCE_BYTES",
    "SourceError",
    "count_lines",
    "count_nonblank_physical_lines",
    "evaluate_counts",
    "evaluate_policy",
    "project",
]
