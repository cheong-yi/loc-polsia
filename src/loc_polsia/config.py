"""Pure configuration, path, and segment-pattern semantics for loc-polsia."""

from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Mapping, Sequence
import tomllib


MAX_CONFIG_BYTES = 1_048_576
CONFIG_SCHEMA = "loc-polsia.config/v1"
COUNT_MODE = "nonblank_physical_lines"
_TOP_LEVEL_KEYS = frozenset(
    {"schema", "include", "exclude", "target_lines", "hard_lines", "count", "baselines"}
)


class ConfigError(ValueError):
    """A deterministic configuration or pattern admission failure."""

    def __init__(self, message: str, code: str = "invalid_config") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Config:
    schema: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    target_lines: int
    hard_lines: int
    count: str
    baselines: dict[str, dict[str, object]]


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_segments(path: str, *, pattern: bool) -> tuple[str, ...]:
    if not isinstance(path, str):
        raise ConfigError("path or pattern must be a string")
    if not path or path.startswith("/"):
        raise ConfigError("path or pattern must be non-empty and relative")
    if "\\" in path or "\x00" in path:
        raise ConfigError("backslash and NUL are not permitted")
    parts = tuple(path.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ConfigError("empty, dot, and dot-dot segments are not permitted")
    for part in parts:
        if pattern and ".git" == part:
            raise ConfigError(".git is not a valid path or pattern segment")
        if pattern:
            if "{" in part or "}" in part or "[" in part or "]" in part or "?" in part:
                raise ConfigError("unsupported pattern syntax")
            if "**" in part and part != "**":
                raise ConfigError("** must be a complete segment")
            if part == "***":
                raise ConfigError("*** is not a valid pattern")
        elif any(char in part for char in "*?[]{}"):
            raise ConfigError("wildcards are not permitted in baseline paths")
    return parts


def validate_path(path: str) -> tuple[str, ...]:
    """Validate and return one decoded repository-relative path."""

    return _valid_segments(path, pattern=False)


def validate_candidate_path(path: str) -> tuple[str, ...]:
    """Validate a decoded candidate path without baseline wildcard rules."""

    if not isinstance(path, str):
        raise ConfigError("candidate path must be a string")
    # Backslash and wildcard characters are legal decoded filename characters;
    # only baseline keys apply the stricter path grammar.
    if not path or path.startswith("/") or "\x00" in path:
        raise ConfigError("candidate path must be relative and NUL-free")
    parts = tuple(path.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ConfigError("candidate path has an invalid segment")
    return parts


def validate_pattern(pattern: str) -> tuple[str, ...]:
    """Validate and return one decoded repository-relative segment pattern."""

    return _valid_segments(pattern, pattern=True)


def _encode_component(component: str) -> str:
    result: list[str] = []
    for char in component:
        value = ord(char)
        if 0x20 <= value <= 0x7E and char != "\\":
            result.append(char)
        elif char == "\\":
            result.append("\\\\")
        elif value <= 0x1F or value == 0x7F:
            # Contract controls are single-byte UTF-8; this also keeps the
            # implementation correct for any scalar that is not printable.
            result.extend(f"\\x{byte:02X}" for byte in char.encode("utf-8"))
        else:
            result.append(char)
    return "".join(result)


def canonical_path(path: str | Sequence[str]) -> str:
    """Return the canonical display/order encoding of a decoded path."""

    components = path.split("/") if isinstance(path, str) else tuple(path)
    return "/".join(_encode_component(component) for component in components)


def canonical_raw_path(components: Sequence[bytes]) -> str:
    """Encode raw POSIX components without decoding invalid UTF-8 bytes."""

    encoded: list[str] = []
    for component in components:
        text: list[str] = []
        for byte in component:
            if 0x20 <= byte <= 0x7E and byte != 0x5C:
                text.append(chr(byte))
            elif byte == 0x5C:
                text.append("\\\\")
            else:
                text.append(f"\\x{byte:02X}")
        encoded.append("".join(text))
    return "/".join(encoded)


def _pattern_key(pattern: str) -> tuple[str, ...]:
    return validate_pattern(pattern)


def pattern_matches(pattern: str | Sequence[str], path: str | Sequence[str]) -> bool:
    """Match a segment pattern using ``*`` and complete-segment ``**``."""

    pattern_parts = _pattern_key(pattern) if isinstance(pattern, str) else tuple(pattern)
    path_parts = tuple(path.split("/") if isinstance(path, str) else path)

    def segment_match(pattern_segment: str, value: str) -> bool:
        expression = "".join(".*" if char == "*" else re.escape(char) for char in pattern_segment)
        return re.fullmatch(expression, value, flags=re.DOTALL) is not None

    memo: dict[tuple[int, int], bool] = {}

    def match(pattern_index: int, path_index: int) -> bool:
        key = (pattern_index, path_index)
        if key in memo:
            return memo[key]
        if pattern_index == len(pattern_parts):
            answer = path_index == len(path_parts)
        elif pattern_parts[pattern_index] == "**":
            answer = match(pattern_index + 1, path_index) or (
                path_index < len(path_parts) and match(pattern_index, path_index + 1)
            )
        else:
            answer = path_index < len(path_parts) and segment_match(
                pattern_parts[pattern_index], path_parts[path_index]
            ) and match(pattern_index + 1, path_index + 1)
        memo[key] = answer
        return answer

    return match(0, 0)


def _canonical_baseline_order(key: object) -> str:
    if isinstance(key, str):
        try:
            return canonical_path(validate_path(key))
        except ConfigError:
            return key
    return repr(key)


def validate_config(document: Mapping[str, object]) -> Config:
    """Validate a TOML document in the contract's fixed semantic order."""

    if not isinstance(document, Mapping):
        raise ConfigError("top-level TOML value must be a table")
    if set(document) != _TOP_LEVEL_KEYS:
        raise ConfigError("top-level keys must exactly match the contract")

    if document["schema"] != CONFIG_SCHEMA:
        raise ConfigError("schema is invalid")

    def patterns(name: str, *, required: bool) -> tuple[str, ...]:
        value = document[name]
        if not isinstance(value, list) or (required and not value):
            raise ConfigError(f"{name} must be a {'non-empty ' if required else ''}array")
        if any(not isinstance(item, str) for item in value):
            raise ConfigError(f"{name} entries must be strings")
        if len(set(value)) != len(value):
            raise ConfigError(f"{name} entries must be unique")
        for item in value:
            validate_pattern(item)
        return tuple(value)

    include = patterns("include", required=True)
    exclude = patterns("exclude", required=False)

    target = document["target_lines"]
    if not _is_integer(target) or target < 1:
        raise ConfigError("target_lines must be a non-boolean integer >= 1")
    hard = document["hard_lines"]
    if not _is_integer(hard) or hard <= target:
        raise ConfigError("hard_lines must be a non-boolean integer > target_lines")
    if document["count"] != COUNT_MODE:
        raise ConfigError("count is invalid")

    baselines_value = document["baselines"]
    if not isinstance(baselines_value, Mapping):
        raise ConfigError("baselines must be a table")
    baselines: dict[str, dict[str, object]] = {}
    for key in sorted(baselines_value, key=_canonical_baseline_order):
        if not isinstance(key, str):
            raise ConfigError("baseline keys must be strings")
        validate_path(key)
        value = baselines_value[key]
        if not isinstance(value, Mapping) or set(value) != {"lines", "reason"}:
            raise ConfigError("baseline entries must contain exactly lines and reason")
        lines = value["lines"]
        if not _is_integer(lines) or lines <= hard:
            raise ConfigError("baseline lines must be a non-boolean integer > hard_lines")
        reason = value["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigError("baseline reason must be non-empty and non-whitespace")
        baselines[key] = {"lines": lines, "reason": reason}

    return Config(CONFIG_SCHEMA, include, exclude, target, hard, COUNT_MODE, baselines)


def parse_config_bytes(data: bytes) -> Config:
    """Strictly parse and validate bounded TOML configuration bytes."""

    if len(data) > MAX_CONFIG_BYTES:
        raise ConfigError("configuration exceeds the fixed byte bound", "config_too_large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError("configuration is not strict UTF-8", "invalid_toml") from exc
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("configuration is not valid TOML", "invalid_toml") from exc
    return validate_config(document)


load_config = parse_config_bytes
match_pattern = pattern_matches
