"""Canonical result envelope and text/JSON projections."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping


RESULT_SCHEMA = "loc-polsia.result/v1"
_RESULT_KEYS = ("schema", "status", "summary", "findings")
_SUMMARY_KEYS = ("checked", "warnings", "failures", "debt", "errors")
_FINDING_KEYS = (
    "path",
    "code",
    "severity",
    "lines",
    "target",
    "hard",
    "baseline",
    "legal_next_actions",
)
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
ERROR_ACTIONS = {
    "unreadable_root": "restore_root_access",
    "missing_config": "add_valid_config",
    "invalid_toml": "fix_config",
    "invalid_config": "fix_config",
    "unreadable_config": "restore_config_readability",
    "config_too_large": "reduce_config_size",
    "unreadable_directory": "restore_directory_readability_or_retry",
    "invalid_filename_bytes": "rename_to_utf8_or_exclude",
    "unsupported_symlink": "exclude_or_replace_symlink",
    "unsupported_file_type": "replace_or_exclude_nonregular",
    "path_drift": "stabilize_tree_and_retry",
    "unreadable_file": "restore_readability_or_exclude",
    "source_too_large": "reduce_file_size_or_exclude",
    "invalid_utf8": "convert_to_utf8_or_exclude",
    "stale_baseline_path": "remove_or_correct_baseline",
    "internal_error": "report_internal_error",
}
_PATHLESS_ERRORS = frozenset(
    {
        "unreadable_root",
        "missing_config",
        "invalid_toml",
        "invalid_config",
        "unreadable_config",
        "config_too_large",
        "internal_error",
    }
)
_HEX = frozenset("0123456789ABCDEF")


def _is_nonnegative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _canonical_decoded_path(path: object, *, baseline: bool = False) -> str:
    if not isinstance(path, str) or not path or path.startswith("/"):
        raise ValueError("path must be a non-empty canonical relative path")
    for component in path.split("/"):
        if component in {"", ".", ".."}:
            raise ValueError("path contains an invalid component")
        index = 0
        while index < len(component):
            char = component[index]
            value = ord(char)
            if char != "\\":
                if value <= 0x1F or value == 0x7F or 0xD800 <= value <= 0xDFFF:
                    raise ValueError("path contains a non-canonical scalar")
                if baseline and char in "*?[]{}":
                    raise ValueError("baseline path contains a wildcard")
                index += 1
                continue
            if index + 1 < len(component) and component[index + 1] == "\\":
                if baseline:
                    raise ValueError("baseline path contains a backslash")
                index += 2
                continue
            escape = component[index + 1:index + 4]
            if (
                len(escape) != 3
                or escape[0] != "x"
                or escape[1] not in _HEX
                or escape[2] not in _HEX
            ):
                raise ValueError("path contains a non-canonical escape")
            byte = int(escape[1:], 16)
            if byte == 0 or (byte > 0x1F and byte != 0x7F):
                raise ValueError("path contains a non-canonical escape")
            index += 4
    return path


def _canonical_invalid_raw_path(path: object) -> str:
    if not isinstance(path, str) or not path or path.startswith("/"):
        raise ValueError("raw path must be a non-empty canonical relative path")
    raw_components: list[bytes] = []
    for component in path.split("/"):
        if component in {"", ".", ".."}:
            raise ValueError("raw path contains an invalid component")
        raw = bytearray()
        index = 0
        while index < len(component):
            char = component[index]
            if char != "\\":
                value = ord(char)
                if value < 0x20 or value > 0x7E:
                    raise ValueError("raw path contains a non-canonical byte")
                raw.append(value)
                index += 1
                continue
            if index + 1 < len(component) and component[index + 1] == "\\":
                raw.append(0x5C)
                index += 2
                continue
            escape = component[index + 1:index + 4]
            if (
                len(escape) != 3
                or escape[0] != "x"
                or escape[1] not in _HEX
                or escape[2] not in _HEX
            ):
                raise ValueError("raw path contains a non-canonical escape")
            byte = int(escape[1:], 16)
            if byte == 0 or 0x20 <= byte <= 0x7E:
                raise ValueError("raw path contains a non-canonical escape")
            raw.append(byte)
            index += 4
        raw_components.append(bytes(raw))
    try:
        b"/".join(raw_components).decode("utf-8")
    except UnicodeDecodeError:
        return path
    raise ValueError("invalid_filename_bytes path must contain invalid UTF-8")


def _normalize_finding(item: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(item, Mapping) or set(item) != set(_FINDING_KEYS):
        raise ValueError("finding keys must exactly match the result contract")
    code = item["code"]
    severity = item["severity"]
    actions = item["legal_next_actions"]
    if not isinstance(actions, list) or any(not isinstance(action, str) for action in actions):
        raise ValueError("legal_next_actions must be a list of contract tokens")

    if not isinstance(code, str):
        raise ValueError("finding code must be a contract token")
    if code in _POLICY:
        expected_severity, expected_actions = _POLICY[code]
        if severity != expected_severity or actions != list(expected_actions):
            raise ValueError("policy severity or actions do not match the code")
        _canonical_decoded_path(item["path"])
        lines, target, hard, baseline = (
            item["lines"],
            item["target"],
            item["hard"],
            item["baseline"],
        )
        if not _is_nonnegative_integer(lines):
            raise ValueError("policy lines must be a non-negative integer")
        if not _is_nonnegative_integer(target) or target < 1:
            raise ValueError("policy target must be a positive integer")
        if not _is_nonnegative_integer(hard) or hard <= target:
            raise ValueError("policy hard must be an integer greater than target")
        if baseline is not None and (
            not _is_nonnegative_integer(baseline) or baseline <= hard
        ):
            raise ValueError("policy baseline must be null or an integer above hard")
        valid_state = {
            "above_target": baseline is None and target < lines <= hard,
            "unbaselined_over_hard": baseline is None and lines > hard,
            "baseline_debt": baseline is not None and lines == baseline,
            "baseline_growth": baseline is not None and lines > baseline,
            "baseline_not_tight": baseline is not None and hard < lines < baseline,
            "stale_baseline_compliant": baseline is not None and lines <= hard,
        }[code]
        if not valid_state:
            raise ValueError("policy values do not match the code")
    elif code in ERROR_ACTIONS:
        if severity != "error" or actions != [ERROR_ACTIONS[code]]:
            raise ValueError("error severity or action does not match the code")
        if any(item[key] is not None for key in ("lines", "target", "hard", "baseline")):
            raise ValueError("error policy fields must be null")
        if code in _PATHLESS_ERRORS:
            if item["path"] is not None:
                raise ValueError("this error code requires a null path")
        elif item["path"] is None:
            raise ValueError("this error code requires a path")
        elif code == "invalid_filename_bytes":
            _canonical_invalid_raw_path(item["path"])
        else:
            _canonical_decoded_path(item["path"], baseline=code == "stale_baseline_path")
    else:
        raise ValueError("finding code is not in the contract")
    return {
        key: list(item[key]) if key == "legal_next_actions" else item[key]
        for key in _FINDING_KEYS
    }


def finding(
    path: str | None,
    code: str,
    severity: str,
    lines: int | None,
    target: int | None,
    hard: int | None,
    baseline: int | None,
    legal_next_actions: Iterable[str],
) -> dict[str, object]:
    return _normalize_finding({
        "path": path,
        "code": code,
        "severity": severity,
        "lines": lines,
        "target": target,
        "hard": hard,
        "baseline": baseline,
        "legal_next_actions": list(legal_next_actions),
    })


def result(
    status: str,
    checked: int,
    warnings: int,
    failures: int,
    debt: int,
    errors: int,
    findings: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    counts = (checked, warnings, failures, debt, errors)
    if status not in {"pass", "fail", "error"}:
        raise ValueError("status must be pass, fail, or error")
    if any(not _is_nonnegative_integer(value) for value in counts):
        raise ValueError("summary counts must be non-boolean non-negative integers")
    ordered_findings: list[dict[str, object]] = []
    for item in findings:
        ordered_findings.append(_normalize_finding(item))
    ordered_findings.sort(key=lambda item: (str(item["path"] or ""), str(item["code"])))
    error_rows = [item for item in ordered_findings if item["severity"] == "error"]
    if error_rows:
        if ordered_findings != error_rows or counts != (0, 0, 0, 0, 1) or status != "error":
            raise ValueError("error results require one error and zero policy counts")
        if len(error_rows) != 1:
            raise ValueError("error results require exactly one finding")
    else:
        actual = {
            severity: sum(item["severity"] == severity for item in ordered_findings)
            for severity in ("warning", "failure", "debt")
        }
        if errors != 0 or (warnings, failures, debt) != (
            actual["warning"], actual["failure"], actual["debt"]
        ):
            raise ValueError("summary counts must match policy findings")
        if checked < len(ordered_findings):
            raise ValueError("checked must cover every policy finding")
        if len({item["path"] for item in ordered_findings}) != len(ordered_findings):
            raise ValueError("a policy path may have at most one finding")
        expected_status = "fail" if failures else "pass"
        if status != expected_status:
            raise ValueError("status does not match the policy summary")
    return {
        "schema": RESULT_SCHEMA,
        "status": status,
        "summary": {
            "checked": checked,
            "warnings": warnings,
            "failures": failures,
            "debt": debt,
            "errors": errors,
        },
        "findings": ordered_findings,
    }


def policy_result(checked: int, findings: Iterable[Mapping[str, object]]) -> dict[str, object]:
    rows = [_normalize_finding(row) for row in findings]
    warnings = sum(row.get("severity") == "warning" for row in rows)
    failures = sum(row.get("severity") == "failure" for row in rows)
    debt = sum(row.get("severity") == "debt" for row in rows)
    return result("fail" if failures else "pass", checked, warnings, failures, debt, 0, rows)


def error_result(code: str, path: str | None = None) -> dict[str, object]:
    if not isinstance(code, str) or code not in ERROR_ACTIONS:
        raise ValueError("error code is not in the contract")
    action = ERROR_ACTIONS[code]
    return result(
        "error",
        0,
        0,
        0,
        0,
        1,
        [finding(path, code, "error", None, None, None, None, [action])],
    )


def _internal_error_result() -> dict[str, object]:
    return {
        "schema": RESULT_SCHEMA,
        "status": "error",
        "summary": {"checked": 0, "warnings": 0, "failures": 0, "debt": 0, "errors": 1},
        "findings": [{
            "path": None,
            "code": "internal_error",
            "severity": "error",
            "lines": None,
            "target": None,
            "hard": None,
            "baseline": None,
            "legal_next_actions": ["report_internal_error"],
        }],
    }


def _preflight(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or tuple(value) != _RESULT_KEYS:
        raise ValueError("result keys or order are invalid")
    if value["schema"] != RESULT_SCHEMA:
        raise ValueError("result schema is invalid")
    summary = value["summary"]
    findings = value["findings"]
    if not isinstance(summary, Mapping) or tuple(summary) != _SUMMARY_KEYS:
        raise ValueError("summary keys or order are invalid")
    if not isinstance(findings, list):
        raise ValueError("findings must be a list")
    if any(not isinstance(item, Mapping) or tuple(item) != _FINDING_KEYS for item in findings):
        raise ValueError("finding keys or order are invalid")
    normalized = result(
        value["status"],
        *(summary[key] for key in _SUMMARY_KEYS),
        findings,
    )
    if normalized != value:
        raise ValueError("result is not in canonical finding order")
    return normalized


def json_bytes(value: Mapping[str, object]) -> bytes:
    """Serialize a validated result, failing closed to ``internal_error``."""

    try:
        safe_value = _preflight(value)
    except Exception:
        safe_value = _internal_error_result()
    return (json.dumps(safe_value, ensure_ascii=False, separators=(",", ":"), sort_keys=False) + "\n").encode(
        "utf-8"
    )


_PLAIN_PATH = re.compile(r"^[A-Za-z0-9._/@+\-]+$")
_SENTINEL_PATHS = {
    "unreadable_root": "<root>",
    "missing_config": "<config>",
    "invalid_toml": "<config>",
    "invalid_config": "<config>",
    "unreadable_config": "<config>",
    "config_too_large": "<config>",
    "internal_error": "<none>",
}


def _text_path(path: object, code: str) -> str:
    if code in _SENTINEL_PATHS:
        return _SENTINEL_PATHS[code]
    if path is None:
        return "<none>"
    value = str(path)
    return value if _PLAIN_PATH.fullmatch(value) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _value(value: object) -> str:
    return "-" if value is None else str(value)


def text_bytes(value: Mapping[str, object]) -> bytes:
    """Serialize the deterministic human projection, including one final LF."""

    try:
        safe_value = _preflight(value)
    except Exception:
        safe_value = _internal_error_result()
    summary = safe_value["summary"]
    lines = [
        f"{str(safe_value['status']).upper()} checked={summary['checked']} warnings={summary['warnings']} "
        f"failures={summary['failures']} debt={summary['debt']} errors={summary['errors']}"
    ]
    findings = safe_value["findings"]
    for row in findings:
        severity = str(row["severity"]).upper()
        actions = row["legal_next_actions"]
        lines.append(
            f"{severity} {_text_path(row['path'], str(row['code']))} {row['code']} "
            f"lines={_value(row['lines'])} target={_value(row['target'])} hard={_value(row['hard'])} "
            f"baseline={_value(row['baseline'])} actions={','.join(str(action) for action in actions)}"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


render_json = json_bytes
render_text = text_bytes
