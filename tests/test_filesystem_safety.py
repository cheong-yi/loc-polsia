from __future__ import annotations

import ast
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from loc_polsia.check import MAX_SOURCE_BYTES
from loc_polsia.config import MAX_CONFIG_BYTES
from loc_polsia.filesystem import _Syscalls, check_root


CONFIG_NAME = b".loc-polsia.toml"
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW


def config_bytes(*, include=("**/*.py",), exclude=(), target=10, hard=20, baselines=""):
    includes = ", ".join(json.dumps(item) for item in include)
    excludes = ", ".join(json.dumps(item) for item in exclude)
    return (
        'schema = "loc-polsia.config/v1"\n'
        f"include = [{includes}]\n"
        f"exclude = [{excludes}]\n"
        f"target_lines = {target}\n"
        f"hard_lines = {hard}\n"
        'count = "nonblank_physical_lines"\n'
        "[baselines]\n"
        f"{baselines}"
    ).encode()


def put_config(root: str, data: bytes | None = None) -> None:
    with open(os.path.join(root, ".loc-polsia.toml"), "wb") as stream:
        stream.write(config_bytes() if data is None else data)


def code_of(value) -> str:
    return value.result["findings"][0]["code"]


def path_of(value) -> str | None:
    return value.result["findings"][0]["path"]


def snapshot(root: Path, *, skip=()) -> tuple[tuple[str, int, str], ...]:
    rows = []
    for directory, dirnames, filenames in os.walk(root):
        relative_dir = Path(directory).relative_to(root)
        dirnames[:] = sorted(
            name for name in dirnames
            if not (relative_dir == Path(".") and name in skip) and name != "__pycache__"
        )
        for name in sorted(filenames):
            relative = relative_dir / name
            if relative.parts and relative.parts[0] in skip:
                continue
            path = root / relative
            metadata = path.lstat()
            if path.is_symlink():
                digest = "link:" + os.readlink(path)
            elif path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                digest = "other"
            rows.append((relative.as_posix(), metadata.st_mode, digest))
    return tuple(rows)


class RecordingOps(_Syscalls):
    def __init__(self):
        self.events = []

    def open_root(self, root, flags):
        self.events.append(("open_root", flags))
        return super().open_root(root, flags)

    def open_at(self, name, flags, *, dir_fd):
        self.events.append(("open_at", name, flags))
        return super().open_at(name, flags, dir_fd=dir_fd)

    def stat_at(self, name, *, dir_fd):
        self.events.append(("stat_at", name))
        return super().stat_at(name, dir_fd=dir_fd)

    def listdir(self, fd):
        self.events.append(("listdir", fd))
        return super().listdir(fd)

    def fstat(self, fd):
        self.events.append(("fstat", fd))
        return super().fstat(fd)

    def read(self, fd, size):
        self.events.append(("read", fd, size))
        return super().read(fd, size)


class ShortReadOps(_Syscalls):
    def read(self, fd, size):
        return super().read(fd, min(size, 7))


class LyingSizeOps(ShortReadOps):
    def __init__(self, target_name: bytes):
        self.target_name = target_name
        self.target_fd = None

    def open_at(self, name, flags, *, dir_fd):
        fd = super().open_at(name, flags, dir_fd=dir_fd)
        if name == self.target_name:
            self.target_fd = fd
        return fd

    def fstat(self, fd):
        value = super().fstat(fd)
        if fd != self.target_fd:
            return value
        fields = list(value)
        fields[6] = 1
        return os.stat_result(fields)


class SwapOps(_Syscalls):
    def __init__(self, candidate: str, replacement, target: str | None = None):
        self.candidate = candidate
        self.replacement = replacement
        self.target = target
        self.triggered = False
        self.target_identity = os.stat(target)[:2] if target is not None else None

    def open_at(self, name, flags, *, dir_fd):
        if name == os.fsencode(os.path.basename(self.candidate)) and not self.triggered:
            self.triggered = True
            os.unlink(self.candidate)
            self.replacement(self.candidate)
        return super().open_at(name, flags, dir_fd=dir_fd)

    def read(self, fd, size):
        if self.target_identity is not None:
            metadata = os.fstat(fd)
            if (metadata.st_dev, metadata.st_ino) == self.target_identity:
                raise AssertionError("race target was read")
        return super().read(fd, size)


class FilesystemConfigTests(unittest.TestCase):
    def test_root_and_descriptor_relative_flags_are_exact(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root)
            os.mkdir(os.path.join(root, "src"))
            Path(root, "src", "a.py").write_bytes(b"x\n")
            ops = RecordingOps()
            value = check_root(root, _ops=ops)
        self.assertEqual(value.exit_code, 0)
        self.assertIn(("open_root", DIR_FLAGS), ops.events)
        opens = [event for event in ops.events if event[0] == "open_at"]
        self.assertIn(("open_at", CONFIG_NAME, FILE_FLAGS), opens)
        self.assertIn(("open_at", b"src", DIR_FLAGS), opens)
        self.assertIn(("open_at", b"a.py", FILE_FLAGS), opens)

    def test_root_failures_are_unreadable_root(self):
        class RootFstatFailure(_Syscalls):
            def fstat(self, fd):
                raise OSError(errno.EIO, "injected")

        class RootUseFailure(_Syscalls):
            def listdir(self, fd):
                raise OSError(errno.EIO, "injected")

        with tempfile.TemporaryDirectory() as root:
            put_config(root)
            self.assertEqual(code_of(check_root(os.path.join(root, "absent"))), "unreadable_root")
            self.assertEqual(code_of(check_root(root, _ops=RootFstatFailure())), "unreadable_root")
            self.assertEqual(code_of(check_root(root, _ops=RootUseFailure())), "unreadable_root")

    def test_missing_symlink_and_nonregular_config(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            self.assertEqual(code_of(check_root(root)), "missing_config")
            target = os.path.join(outside, "secret")
            Path(target).write_bytes(config_bytes())
            os.symlink(target, os.path.join(root, ".loc-polsia.toml"))
            ops = RecordingOps()
            self.assertEqual(code_of(check_root(root, _ops=ops)), "invalid_config")
            self.assertFalse(any(event[0] == "read" for event in ops.events))
            os.unlink(os.path.join(root, ".loc-polsia.toml"))
            os.mkdir(os.path.join(root, ".loc-polsia.toml"))
            self.assertEqual(code_of(check_root(root)), "invalid_config")
            os.rmdir(os.path.join(root, ".loc-polsia.toml"))
            os.mkfifo(os.path.join(root, ".loc-polsia.toml"))
            self.assertEqual(code_of(check_root(root)), "invalid_config")

    def test_config_exact_limit_and_limit_plus_one(self):
        base = config_bytes()
        exact = base + b"#" + b"x" * (MAX_CONFIG_BYTES - len(base) - 1)
        with tempfile.TemporaryDirectory() as root:
            put_config(root, exact)
            self.assertEqual(check_root(root).exit_code, 0)
            Path(root, ".loc-polsia.toml").write_bytes(exact + b"x")
            ops = RecordingOps()
            self.assertEqual(code_of(check_root(root, _ops=ops)), "config_too_large")
            config_open = next(event for event in ops.events if event[:2] == ("open_at", CONFIG_NAME))
            config_fd_event_index = ops.events.index(config_open)
            self.assertFalse(any(event[0] == "read" for event in ops.events[config_fd_event_index:]))

    def test_config_short_reads_and_observed_extra_byte(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root)
            self.assertEqual(check_root(root, _ops=ShortReadOps()).exit_code, 0)
            oversized = config_bytes() + b"#" + b"x" * MAX_CONFIG_BYTES
            put_config(root, oversized)
            value = check_root(root, _ops=LyingSizeOps(CONFIG_NAME))
            self.assertEqual(code_of(value), "config_too_large")

    def test_invalid_toml_utf8_and_semantics(self):
        with tempfile.TemporaryDirectory() as root:
            for data, code in (
                (b"\xff", "invalid_toml"),
                (b"not = [toml", "invalid_toml"),
                (config_bytes(target=20, hard=20), "invalid_config"),
            ):
                with self.subTest(code=code, data=data[:20]):
                    put_config(root, data)
                    self.assertEqual(code_of(check_root(root)), code)

    def test_config_fstat_precedes_read_and_io_failures_are_unreadable(self):
        class ConfigFailure(_Syscalls):
            def __init__(self, operation):
                self.operation = operation
                self.config_fd = None
                self.fstatted = False
                self.triggered = False

            def open_at(self, name, flags, *, dir_fd):
                if name == CONFIG_NAME and self.operation == "open":
                    self.triggered = True
                    raise PermissionError(errno.EACCES, "injected")
                fd = super().open_at(name, flags, dir_fd=dir_fd)
                if name == CONFIG_NAME:
                    self.config_fd = fd
                return fd

            def fstat(self, fd):
                if fd == self.config_fd:
                    if self.operation == "fstat":
                        self.triggered = True
                        raise OSError(errno.EIO, "injected")
                    self.fstatted = True
                return super().fstat(fd)

            def read(self, fd, size):
                if fd == self.config_fd:
                    if not self.fstatted:
                        raise AssertionError("config read preceded fstat")
                    if self.operation == "read":
                        self.triggered = True
                        raise OSError(errno.EIO, "injected")
                return super().read(fd, size)

        with tempfile.TemporaryDirectory() as root:
            put_config(root)
            for operation in ("open", "fstat", "read"):
                with self.subTest(operation=operation):
                    ops = ConfigFailure(operation)
                    self.assertEqual(code_of(check_root(root, _ops=ops)), "unreadable_config")
                    self.assertTrue(ops.triggered)


class FilesystemDiscoveryTests(unittest.TestCase):
    def test_include_exclude_and_git_prune_before_bad_descendants(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("**/*.py",), exclude=("vendor/**",)))
            os.mkdir(os.path.join(root, "src"))
            os.mkdir(os.path.join(root, "vendor"))
            os.mkdir(os.path.join(root, ".git"))
            Path(root, "src", "a.py").write_text("x\n\n", encoding="utf-8")
            os.open(os.fsencode(root) + b"/vendor/bad\xff", os.O_CREAT | os.O_WRONLY, 0o600)
            os.open(os.fsencode(root) + b"/.git/bad\xfe", os.O_CREAT | os.O_WRONLY, 0o600)
            os.symlink("/no/such/target", os.path.join(root, "vendor", "bad.py"))
            value = check_root(root)
        self.assertEqual(value.exit_code, 0)
        self.assertEqual(value.result["summary"]["checked"], 1)

    def test_exclusion_wins_for_non_directories_and_targets_are_not_followed(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            put_config(root, config_bytes(include=("skip*",), exclude=("skip*",)))
            target = Path(outside, "target.py")
            target.write_text("secret", encoding="utf-8")
            os.symlink(target, os.path.join(root, "skip-link"))
            os.mkfifo(os.path.join(root, "skip-fifo"))
            Path(root, "skip.py").write_text("ignored", encoding="utf-8")
            value = check_root(root)
        self.assertEqual(value.exit_code, 0)
        self.assertEqual(value.result["summary"]["checked"], 0)

    def test_active_symlink_fifo_directory_and_socket_matrix(self):
        constructors = {
            "symlink": lambda path: os.symlink("missing-target", path),
            "fifo": os.mkfifo,
            "directory": os.mkdir,
        }
        for label, constructor in constructors.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root:
                put_config(root, config_bytes(include=("entry",)))
                constructor(os.path.join(root, "entry"))
                expected = "unsupported_symlink" if label == "symlink" else "unsupported_file_type"
                self.assertEqual(code_of(check_root(root)), expected)
        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("entry",)))
            os.mknod(os.path.join(root, "entry"), stat.S_IFSOCK | 0o600)
            self.assertEqual(code_of(check_root(root)), "unsupported_file_type")

    def test_real_device_is_unsupported_without_reading_it(self):
        class ExternalConfigOps(RecordingOps):
            def __init__(self, config_fd):
                super().__init__()
                self.config_fd = config_fd
                self.config_dups = set()

            def open_at(self, name, flags, *, dir_fd):
                self.events.append(("open_at", name, flags))
                if name == CONFIG_NAME:
                    duplicate = os.dup(self.config_fd)
                    self.config_dups.add(duplicate)
                    return duplicate
                return _Syscalls.open_at(self, name, flags, dir_fd=dir_fd)

        self.assertTrue(stat.S_ISCHR(os.stat("/dev/null", follow_symlinks=False).st_mode))
        with tempfile.TemporaryDirectory() as fixture:
            put_config(fixture, config_bytes(include=("null",), exclude=("pts/**", "shm/**")))
            config_fd = os.open(os.path.join(fixture, ".loc-polsia.toml"), os.O_RDONLY)
            try:
                ops = ExternalConfigOps(config_fd)
                value = check_root("/dev", _ops=ops)
            finally:
                os.close(config_fd)
        self.assertEqual((code_of(value), path_of(value)), ("unsupported_file_type", "null"))
        self.assertIn(("stat_at", b"null"), ops.events)
        self.assertTrue(all(event[1] in ops.config_dups for event in ops.events if event[0] == "read"))

    def test_nonmatching_nonregular_entries_are_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("*.py",)))
            os.symlink("missing", os.path.join(root, "ignored-link"))
            os.mkfifo(os.path.join(root, "ignored-fifo"))
            self.assertEqual(check_root(root).exit_code, 0)

    def test_invalid_raw_utf8_has_canonical_path_and_is_never_read(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root)
            fd = os.open(os.fsencode(root) + b"/bad\xff.py", os.O_CREAT | os.O_WRONLY, 0o600)
            os.write(fd, b"secret")
            os.close(fd)
            ops = RecordingOps()
            value = check_root(root, _ops=ops)
        self.assertEqual(code_of(value), "invalid_filename_bytes")
        self.assertEqual(path_of(value), "bad\\xFF.py")
        self.assertFalse(any(event[:2] == ("open_at", b"bad\xff.py") for event in ops.events))

    def test_complete_path_preorder_controls_first_error(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root)
            os.mkdir(os.path.join(root, "a"))
            os.symlink("missing", os.path.join(root, "a", "z.py"))
            os.symlink("missing", os.path.join(root, "b.py"))
            value = check_root(root)
        self.assertEqual((code_of(value), path_of(value)), ("unsupported_symlink", "a/z.py"))

    def test_frozen_candidate_order_controls_first_source_error(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("*.py",)))
            Path(root, "z.py").write_bytes(b"\xff")
            Path(root, "a.py").write_bytes(b"\xfe")
            value = check_root(root)
        self.assertEqual((code_of(value), path_of(value)), ("invalid_utf8", "a.py"))

    def test_invalid_and_valid_children_share_deterministic_order(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root)
            os.symlink("missing", os.path.join(root, "z.py"))
            fd = os.open(os.fsencode(root) + b"/a\xff.py", os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
            value = check_root(root)
        self.assertEqual((code_of(value), path_of(value)), ("invalid_filename_bytes", "a\\xFF.py"))

    def test_hardlink_paths_remain_distinct(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("*.py",)))
            Path(root, "a.py").write_text("x\n", encoding="utf-8")
            os.link(os.path.join(root, "a.py"), os.path.join(root, "b.py"))
            value = check_root(root)
        self.assertEqual(value.exit_code, 0)
        self.assertEqual(value.result["summary"]["checked"], 2)

    def test_nonroot_metadata_and_enumeration_failures(self):
        class StatFailure(_Syscalls):
            def stat_at(self, name, *, dir_fd):
                if name == b"bad":
                    raise OSError(errno.EIO, "injected")
                return super().stat_at(name, dir_fd=dir_fd)

        class DirectoryUseFailure(_Syscalls):
            def __init__(self):
                self.directory_fd = None

            def open_at(self, name, flags, *, dir_fd):
                fd = super().open_at(name, flags, dir_fd=dir_fd)
                if name == b"sub":
                    self.directory_fd = fd
                return fd

            def listdir(self, fd):
                if fd == self.directory_fd:
                    raise OSError(errno.EIO, "injected")
                return super().listdir(fd)

        with tempfile.TemporaryDirectory() as root:
            put_config(root)
            Path(root, "bad").write_bytes(b"")
            value = check_root(root, _ops=StatFailure())
            self.assertEqual((code_of(value), path_of(value)), ("unreadable_directory", "bad"))
            os.unlink(os.path.join(root, "bad"))
            os.mkdir(os.path.join(root, "sub"))
            value = check_root(root, _ops=DirectoryUseFailure())
            self.assertEqual((code_of(value), path_of(value)), ("unreadable_directory", "sub"))


class FilesystemFrozenReadTests(unittest.TestCase):
    def test_candidate_fstat_and_complete_discovery_precede_first_read(self):
        class OrderedOps(_Syscalls):
            def __init__(self):
                self.listdir_count = 0
                self.candidate_fds = set()
                self.fstatted = set()

            def listdir(self, fd):
                self.listdir_count += 1
                return super().listdir(fd)

            def open_at(self, name, flags, *, dir_fd):
                fd = super().open_at(name, flags, dir_fd=dir_fd)
                if name in {b"a.py", b"b.py"}:
                    self.candidate_fds.add(fd)
                return fd

            def fstat(self, fd):
                if fd in self.candidate_fds:
                    self.fstatted.add(fd)
                return super().fstat(fd)

            def read(self, fd, size):
                if fd in self.candidate_fds:
                    if self.listdir_count != 2:
                        raise AssertionError("source read began before complete discovery")
                    if fd not in self.fstatted:
                        raise AssertionError("candidate read preceded fstat")
                return super().read(fd, size)

        with tempfile.TemporaryDirectory() as root:
            put_config(root)
            os.mkdir(os.path.join(root, "sub"))
            Path(root, "a.py").write_bytes(b"a\n")
            Path(root, "sub", "b.py").write_bytes(b"b\n")
            ops = OrderedOps()
            value = check_root(root, _ops=ops)
        self.assertEqual(value.exit_code, 0)
        self.assertEqual(value.result["summary"]["checked"], 2)

    def test_source_exact_limit_limit_plus_one_and_short_reads(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("*.py",)))
            source = Path(root, "a.py")
            source.write_bytes(b"a" + b" " * (MAX_SOURCE_BYTES - 1))
            self.assertEqual(check_root(root, _ops=ShortReadOps()).exit_code, 0)
            source.write_bytes(b"a" + b" " * MAX_SOURCE_BYTES)
            self.assertEqual(code_of(check_root(root)), "source_too_large")

    def test_source_observed_extra_byte_with_small_pre_read_size(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("*.py",)))
            Path(root, "a.py").write_bytes(b"a" + b" " * MAX_SOURCE_BYTES)
            value = check_root(root, _ops=LyingSizeOps(b"a.py"))
            self.assertEqual(code_of(value), "source_too_large")

    def test_strict_source_utf8(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("*.py",)))
            Path(root, "a.py").write_bytes(b"\xff")
            self.assertEqual(code_of(check_root(root)), "invalid_utf8")

    def test_all_opened_descriptors_close_without_hiding_first_error(self):
        class CloseTrackingOps(_Syscalls):
            def __init__(self):
                self.opened = []
                self.closed = []

            def open_root(self, root, flags):
                fd = super().open_root(root, flags)
                self.opened.append(fd)
                return fd

            def open_at(self, name, flags, *, dir_fd):
                fd = super().open_at(name, flags, dir_fd=dir_fd)
                self.opened.append(fd)
                return fd

            def close(self, fd):
                super().close(fd)
                self.closed.append(fd)
                raise OSError(errno.EIO, "injected after real close")

        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("**/*.py",)))
            os.mkdir(os.path.join(root, "sub"))
            Path(root, "sub", "a.py").write_bytes(b"\xff")
            ops = CloseTrackingOps()
            value = check_root(root, _ops=ops)
        self.assertEqual(code_of(value), "invalid_utf8")
        self.assertEqual(sorted(ops.opened), sorted(ops.closed))

    def test_real_swaps_are_path_drift_and_never_read_symlink_target(self):
        replacements = {
            "missing": lambda path: None,
            "symlink": None,
            "fifo": os.mkfifo,
            "directory": os.mkdir,
        }
        for label, replacement in replacements.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
                put_config(root, config_bytes(include=("*.py",)))
                candidate = os.path.join(root, "a.py")
                Path(candidate).write_bytes(b"safe\n")
                target = os.path.join(outside, "target.py")
                Path(target).write_bytes(b"secret\n")
                actual = replacement
                target_arg = None
                if label == "symlink":
                    actual = lambda path, target=target: os.symlink(target, path)
                    target_arg = target
                ops = SwapOps(candidate, actual, target_arg)
                value = check_root(root, _ops=ops)
                self.assertTrue(ops.triggered)
                self.assertEqual((code_of(value), path_of(value)), ("path_drift", "a.py"))

    def test_open_failure_with_still_regular_metadata_is_unreadable(self):
        class OpenFailure(_Syscalls):
            def __init__(self):
                self.triggered = False

            def open_at(self, name, flags, *, dir_fd):
                if name == b"a.py":
                    self.triggered = True
                    raise PermissionError(errno.EACCES, "injected")
                return super().open_at(name, flags, dir_fd=dir_fd)

        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("*.py",)))
            Path(root, "a.py").write_bytes(b"x")
            ops = OpenFailure()
            value = check_root(root, _ops=ops)
        self.assertTrue(ops.triggered)
        self.assertEqual(code_of(value), "unreadable_file")

    def test_ordinary_candidate_fstat_and_read_failures_are_unreadable(self):
        class FailureOps(_Syscalls):
            def __init__(self, fail):
                self.fail = fail
                self.candidate_fd = None
                self.triggered = False

            def open_at(self, name, flags, *, dir_fd):
                fd = super().open_at(name, flags, dir_fd=dir_fd)
                if name == b"a.py":
                    self.candidate_fd = fd
                return fd

            def fstat(self, fd):
                if self.fail == "fstat" and fd == self.candidate_fd:
                    self.triggered = True
                    raise OSError(errno.EIO, "injected")
                return super().fstat(fd)

            def read(self, fd, size):
                if self.fail == "read" and fd == self.candidate_fd:
                    self.triggered = True
                    raise OSError(errno.EIO, "injected")
                return super().read(fd, size)

        for operation in ("fstat", "read"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as root:
                put_config(root, config_bytes(include=("*.py",)))
                Path(root, "a.py").write_bytes(b"x")
                ops = FailureOps(operation)
                value = check_root(root, _ops=ops)
                self.assertTrue(ops.triggered)
                self.assertEqual(code_of(value), "unreadable_file")

    def test_open_failure_metadata_failure_classification(self):
        class BothFail(_Syscalls):
            def __init__(self, metadata_errno):
                self.metadata_errno = metadata_errno
                self.open_triggered = False
                self.stat_calls = 0

            def open_at(self, name, flags, *, dir_fd):
                if name == b"a.py":
                    self.open_triggered = True
                    raise PermissionError(errno.EACCES, "injected")
                return super().open_at(name, flags, dir_fd=dir_fd)

            def stat_at(self, name, *, dir_fd):
                if name == b"a.py":
                    self.stat_calls += 1
                    if self.open_triggered:
                        raise OSError(self.metadata_errno, "injected")
                return super().stat_at(name, dir_fd=dir_fd)

        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("*.py",)))
            Path(root, "a.py").write_bytes(b"x")
            for metadata_errno, expected in ((errno.ENOENT, "path_drift"), (errno.EIO, "unreadable_file")):
                with self.subTest(metadata_errno=metadata_errno):
                    ops = BothFail(metadata_errno)
                    value = check_root(root, _ops=ops)
                    self.assertTrue(ops.open_triggered)
                    self.assertEqual(ops.stat_calls, 2)
                    self.assertEqual(code_of(value), expected)


class FilesystemAuditTests(unittest.TestCase):
    def test_production_call_is_no_write_in_isolated_audit_window(self):
        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("*.py",)))
            Path(root, "a.py").write_bytes(b"x\n")
            pid = os.fork()
            if pid == 0:
                def audit(event, args):
                    if event == "open" and len(args) >= 3 and isinstance(args[2], int):
                        forbidden = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
                        if args[2] & forbidden:
                            raise RuntimeError(f"write open: {event}")
                    if event in {
                        "os.remove", "os.rename", "os.replace", "os.rmdir", "os.mkdir",
                        "os.system", "subprocess.Popen", "socket.__new__", "socket.connect",
                        "socket.bind",
                    }:
                        raise RuntimeError(f"forbidden audit event: {event}")
                sys.addaudithook(audit)
                try:
                    result = check_root(root)
                    os._exit(0 if result.exit_code == 0 else 20 + result.exit_code)
                except BaseException:
                    os._exit(99)
            _, status_value = os.waitpid(pid, 0)
            self.assertTrue(os.WIFEXITED(status_value))
            self.assertEqual(os.WEXITSTATUS(status_value), 0)

    def test_temp_and_product_manifests_are_unchanged_by_check(self):
        product = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as root:
            put_config(root, config_bytes(include=("*.py",)))
            Path(root, "a.py").write_bytes(b"x\n")
            temp_before = snapshot(Path(root))
            product_before = snapshot(product, skip=(".git", ".omx"))
            self.assertEqual(check_root(root).exit_code, 0)
            self.assertEqual(snapshot(Path(root)), temp_before)
            self.assertEqual(snapshot(product, skip=(".git", ".omx")), product_before)

    def test_exact_production_source_surface_is_fail_closed(self):
        source = Path(__file__).resolve().parents[1] / "src" / "loc_polsia" / "filesystem.py"
        tree = ast.parse(source.read_bytes(), filename=str(source))
        prohibited_modules = {
            "ctypes", "importlib", "subprocess", "socket", "urllib", "http", "requests"
        }
        prohibited_calls = {"eval", "exec", "compile", "__import__", "getattr"}
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in prohibited_modules:
                        violations.append((node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in prohibited_modules:
                    violations.append((node.lineno, f"from {node.module}"))
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in prohibited_calls | {"open"}:
                    violations.append((node.lineno, node.func.id))
                if isinstance(node.func, ast.Attribute):
                    owner = node.func.value.id if isinstance(node.func.value, ast.Name) else None
                    if node.func.attr in {
                        "resolve", "rglob", "walk", "fwalk", "read_bytes", "read_text",
                        "import_module",
                    }:
                        violations.append((node.lineno, f"{owner}.{node.func.attr}"))
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
