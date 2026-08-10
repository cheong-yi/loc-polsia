"""Descriptor-relative filesystem boundary for ``loc-polsia check``."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import os
import stat

from .check import CheckResult, MAX_SOURCE_BYTES, SourceError, count_nonblank_physical_lines, evaluate_counts
from .config import (
    MAX_CONFIG_BYTES,
    Config,
    ConfigError,
    canonical_path,
    canonical_raw_path,
    parse_config_bytes,
    pattern_matches,
)
from .protocol import error_result


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW
_CONFIG_NAME = b".loc-polsia.toml"
_READ_CHUNK = 65_536


class _Syscalls:
    """Private real-syscall adapter used for deterministic race tests."""

    def open_root(self, root: str | bytes | os.PathLike[str], flags: int) -> int:
        return os.open(root, flags)

    def open_at(self, name: bytes, flags: int, *, dir_fd: int) -> int:
        return os.open(name, flags, dir_fd=dir_fd)

    def stat_at(self, name: bytes, *, dir_fd: int) -> os.stat_result:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)

    def listdir(self, fd: int) -> list[str]:
        return os.listdir(fd)

    def fstat(self, fd: int) -> os.stat_result:
        return os.fstat(fd)

    def read(self, fd: int, size: int) -> bytes:
        return os.read(fd, size)

    def close(self, fd: int) -> None:
        os.close(fd)


def check_root(
    root: str | bytes | os.PathLike[str] = ".",
    *,
    _ops: _Syscalls | None = None,
) -> CheckResult:
    """Check one trusted root without reconstructing paths after root admission."""

    operations = _ops if _ops is not None else _Syscalls()
    retained: list[int] = []
    try:
        try:
            root_fd = operations.open_root(root, _DIRECTORY_FLAGS)
        except OSError:
            return _error("unreadable_root")
        retained.append(root_fd)
        try:
            root_metadata = operations.fstat(root_fd)
        except OSError:
            return _error("unreadable_root")
        if not stat.S_ISDIR(root_metadata.st_mode):
            return _error("unreadable_root")

        config = _read_config(operations, root_fd)
        candidates = _discover(operations, root_fd, config, retained)
        counts: dict[str, int] = {}
        for candidate in candidates:
            counts["/".join(candidate.components)] = _read_candidate(operations, candidate)
        result, exit_code = evaluate_counts(counts, config)
        return CheckResult(result, exit_code)
    except _BoundaryError as failure:
        return _error(failure.code, failure.path)
    finally:
        for fd in reversed(retained):
            try:
                operations.close(fd)
            except OSError:
                pass


@dataclass(frozen=True)
class _Candidate:
    parent_fd: int
    raw_name: bytes
    components: tuple[str, ...]
    display: str


class _BoundaryError(Exception):
    def __init__(self, code: str, path: str | None = None) -> None:
        super().__init__(code, path)
        self.code = code
        self.path = path


def _error(code: str, path: str | None = None) -> CheckResult:
    return CheckResult(error_result(code, path), 2)


def _read_bounded(operations: _Syscalls, fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    observed = 0
    while observed <= limit:
        request = min(_READ_CHUNK, limit + 1 - observed)
        chunk = operations.read(fd, request)
        if not chunk:
            break
        chunks.append(chunk)
        observed += len(chunk)
    return b"".join(chunks)


def _read_config(operations: _Syscalls, root_fd: int) -> Config:
    try:
        config_fd = operations.open_at(_CONFIG_NAME, _FILE_FLAGS, dir_fd=root_fd)
    except OSError as failure:
        if failure.errno == errno.ENOENT:
            raise _BoundaryError("missing_config") from failure
        if failure.errno == errno.ELOOP:
            raise _BoundaryError("invalid_config") from failure
        raise _BoundaryError("unreadable_config") from failure
    try:
        try:
            metadata = operations.fstat(config_fd)
        except OSError as failure:
            raise _BoundaryError("unreadable_config") from failure
        if not stat.S_ISREG(metadata.st_mode):
            raise _BoundaryError("invalid_config")
        if metadata.st_size > MAX_CONFIG_BYTES:
            raise _BoundaryError("config_too_large")
        try:
            data = _read_bounded(operations, config_fd, MAX_CONFIG_BYTES)
        except OSError as failure:
            raise _BoundaryError("unreadable_config") from failure
        if len(data) > MAX_CONFIG_BYTES:
            raise _BoundaryError("config_too_large")
        try:
            return parse_config_bytes(data)
        except ConfigError as failure:
            raise _BoundaryError(failure.code) from failure
    finally:
        try:
            operations.close(config_fd)
        except OSError:
            pass


def _matches(patterns: tuple[str, ...], components: tuple[str, ...]) -> bool:
    return any(pattern_matches(pattern, components) for pattern in patterns)


def _discover(
    operations: _Syscalls,
    root_fd: int,
    config: Config,
    retained: list[int],
) -> list[_Candidate]:
    frozen: dict[tuple[str, ...], _Candidate] = {}

    def visit(
        directory_fd: int,
        decoded_parent: tuple[str, ...],
        raw_parent: tuple[bytes, ...],
        *,
        root_directory: bool,
    ) -> None:
        try:
            names = operations.listdir(directory_fd)
        except OSError as failure:
            if root_directory:
                raise _BoundaryError("unreadable_root") from failure
            raise _BoundaryError("unreadable_directory", canonical_path(decoded_parent)) from failure

        children: list[tuple[str, bytes, str | None]] = []
        for listed_name in names:
            raw_name = os.fsencode(listed_name)
            try:
                decoded_name = raw_name.decode("utf-8")
            except UnicodeDecodeError:
                display = canonical_raw_path((*raw_parent, raw_name))
                children.append((display, raw_name, None))
            else:
                display = canonical_path((*decoded_parent, decoded_name))
                children.append((display, raw_name, decoded_name))

        for display, raw_name, decoded_name in sorted(children, key=lambda child: child[0]):
            if decoded_name == ".git":
                continue
            if decoded_name is None:
                raise _BoundaryError("invalid_filename_bytes", display)

            components = (*decoded_parent, decoded_name)
            try:
                metadata = operations.stat_at(raw_name, dir_fd=directory_fd)
            except OSError as failure:
                raise _BoundaryError("unreadable_directory", display) from failure

            excluded = _matches(config.exclude, components)
            if excluded:
                continue
            included = _matches(config.include, components)
            mode = metadata.st_mode
            if stat.S_ISLNK(mode):
                if included:
                    raise _BoundaryError("unsupported_symlink", display)
                continue
            if stat.S_ISDIR(mode):
                if included:
                    raise _BoundaryError("unsupported_file_type", display)
                try:
                    child_fd = operations.open_at(raw_name, _DIRECTORY_FLAGS, dir_fd=directory_fd)
                except OSError as failure:
                    raise _BoundaryError("unreadable_directory", display) from failure
                retained.append(child_fd)
                try:
                    admitted = operations.fstat(child_fd)
                except OSError as failure:
                    raise _BoundaryError("unreadable_directory", display) from failure
                if not stat.S_ISDIR(admitted.st_mode):
                    raise _BoundaryError("unreadable_directory", display)
                visit(child_fd, components, (*raw_parent, raw_name), root_directory=False)
                continue
            if not included:
                continue
            if not stat.S_ISREG(mode):
                raise _BoundaryError("unsupported_file_type", display)
            frozen.setdefault(components, _Candidate(directory_fd, raw_name, components, display))

    visit(root_fd, (), (), root_directory=True)
    return sorted(frozen.values(), key=lambda candidate: candidate.display)


def _read_candidate(operations: _Syscalls, candidate: _Candidate) -> int:
    try:
        source_fd = operations.open_at(candidate.raw_name, _FILE_FLAGS, dir_fd=candidate.parent_fd)
    except OSError as open_failure:
        try:
            metadata = operations.stat_at(candidate.raw_name, dir_fd=candidate.parent_fd)
        except OSError as metadata_failure:
            if metadata_failure.errno == errno.ENOENT:
                raise _BoundaryError("path_drift", candidate.display) from open_failure
            raise _BoundaryError("unreadable_file", candidate.display) from open_failure
        if stat.S_ISREG(metadata.st_mode):
            raise _BoundaryError("unreadable_file", candidate.display) from open_failure
        raise _BoundaryError("path_drift", candidate.display) from open_failure

    try:
        try:
            metadata = operations.fstat(source_fd)
        except OSError as failure:
            raise _BoundaryError("unreadable_file", candidate.display) from failure
        if not stat.S_ISREG(metadata.st_mode):
            raise _BoundaryError("path_drift", candidate.display)
        if metadata.st_size > MAX_SOURCE_BYTES:
            raise _BoundaryError("source_too_large", candidate.display)
        try:
            data = _read_bounded(operations, source_fd, MAX_SOURCE_BYTES)
        except OSError as failure:
            raise _BoundaryError("unreadable_file", candidate.display) from failure
        if len(data) > MAX_SOURCE_BYTES:
            raise _BoundaryError("source_too_large", candidate.display)
        try:
            return count_nonblank_physical_lines(data)
        except SourceError as failure:
            raise _BoundaryError(failure.code, candidate.display) from failure
    finally:
        try:
            operations.close(source_fd)
        except OSError:
            pass
