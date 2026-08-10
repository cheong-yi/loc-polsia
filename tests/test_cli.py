import ast
import hashlib
import importlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch


PRODUCT_ROOT = Path(__file__).parents[1]
SOURCE_ROOT = PRODUCT_ROOT / "src"


def config_bytes(*, target=10, hard=20, baselines=""):
    return (
        'schema = "loc-polsia.config/v1"\n'
        'include = ["src/**/*.py"]\n'
        'exclude = []\n'
        f"target_lines = {target}\n"
        f"hard_lines = {hard}\n"
        'count = "nonblank_physical_lines"\n'
        "[baselines]\n"
        f"{baselines}"
    ).encode("utf-8")


def make_repository(root, *, lines=None, config=None):
    root = Path(root)
    if config is not None:
        (root / ".loc-polsia.toml").write_bytes(config)
    if lines is not None:
        source = root / "src" / "a.py"
        source.parent.mkdir()
        source.write_bytes(b"x\n" * lines)


def run_cli(root, *arguments):
    environment = os.environ.copy()
    environment.update(
        {
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(SOURCE_ROOT),
        }
    )
    return subprocess.run(
        [sys.executable, "-B", "-m", "loc_polsia", *arguments],
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def tree_manifest(root):
    manifest = {}
    for path in sorted(Path(root).rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or relative.parts[0] in {".git", ".omx"}:
            continue
        manifest[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


PASS_TEXT = b"PASS checked=1 warnings=0 failures=0 debt=0 errors=0\n"
PASS_JSON = (
    b'{"schema":"loc-polsia.result/v1","status":"pass","summary":{"checked":1,'
    b'"warnings":0,"failures":0,"debt":0,"errors":0},"findings":[]}\n'
)
INTERNAL_TEXT = (
    b"ERROR checked=0 warnings=0 failures=0 debt=0 errors=1\n"
    b"ERROR <none> internal_error lines=- target=- hard=- baseline=- "
    b"actions=report_internal_error\n"
)
INTERNAL_JSON = (
    b'{"schema":"loc-polsia.result/v1","status":"error","summary":{"checked":0,'
    b'"warnings":0,"failures":0,"debt":0,"errors":1},"findings":[{"path":null,'
    b'"code":"internal_error","severity":"error","lines":null,"target":null,'
    b'"hard":null,"baseline":null,"legal_next_actions":["report_internal_error"]}]}\n'
)


class CliSubprocessTests(unittest.TestCase):
    def temporary_directory(self):
        return tempfile.TemporaryDirectory(dir="/tmp")

    def test_help_and_command_parsing(self):
        with self.temporary_directory() as root:
            top_help = run_cli(root, "--help")
            check_help = run_cli(root, "check", "--help")
            invalid = run_cli(root, "scan")

        self.assertEqual(top_help.returncode, 0)
        self.assertEqual(
            top_help.stdout,
            b"usage: loc-polsia [-h] {check} ...\n\n"
            b"positional arguments:\n"
            b"  {check}\n\n"
            b"options:\n"
            b"  -h, --help  show this help message and exit\n",
        )
        self.assertEqual(top_help.stderr, b"")
        self.assertEqual(check_help.returncode, 0)
        self.assertEqual(
            check_help.stdout,
            b"usage: loc-polsia check [-h] [--format {text,json}]\n\n"
            b"options:\n"
            b"  -h, --help            show this help message and exit\n"
            b"  --format {text,json}\n",
        )
        self.assertEqual(check_help.stderr, b"")
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, b"")
        self.assertIn(b"usage: loc-polsia", invalid.stderr)
        self.assertIn(b"invalid choice: 'scan'", invalid.stderr)

    def test_pass_output_is_exact_in_text_and_json(self):
        with self.temporary_directory() as root:
            make_repository(root, lines=1, config=config_bytes())
            text = run_cli(root, "check")
            json = run_cli(root, "check", "--format", "json")

        self.assertEqual((text.returncode, text.stdout, text.stderr), (0, PASS_TEXT, b""))
        self.assertEqual((json.returncode, json.stdout, json.stderr), (0, PASS_JSON, b""))
        self.assertTrue(text.stdout.endswith(b"\n"))
        self.assertFalse(text.stdout.endswith(b"\n\n"))
        self.assertTrue(json.stdout.endswith(b"\n"))
        self.assertFalse(json.stdout.endswith(b"\n\n"))

    def test_warning_and_baseline_debt_exit_zero(self):
        with self.temporary_directory() as warning_root, self.temporary_directory() as debt_root:
            make_repository(warning_root, lines=11, config=config_bytes())
            make_repository(
                debt_root,
                lines=30,
                config=config_bytes(
                    baselines='"src/a.py" = { lines = 30, reason = "adoption" }\n'
                ),
            )
            warning = run_cli(warning_root, "check")
            debt = run_cli(debt_root, "check")

        self.assertEqual(warning.returncode, 0)
        self.assertEqual(
            warning.stdout,
            b"PASS checked=1 warnings=1 failures=0 debt=0 errors=0\n"
            b"WARNING src/a.py above_target lines=11 target=10 hard=20 baseline=- "
            b"actions=reduce_before_growth\n",
        )
        self.assertEqual(warning.stderr, b"")
        self.assertEqual(debt.returncode, 0)
        self.assertEqual(
            debt.stdout,
            b"PASS checked=1 warnings=0 failures=0 debt=1 errors=0\n"
            b"DEBT src/a.py baseline_debt lines=30 target=10 hard=20 baseline=30 "
            b"actions=reduce_cohesive_debt\n",
        )
        self.assertEqual(debt.stderr, b"")

    def test_policy_failure_exit_one(self):
        with self.temporary_directory() as root:
            make_repository(root, lines=21, config=config_bytes())
            completed = run_cli(root, "check")

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            completed.stdout,
            b"FAIL checked=1 warnings=0 failures=1 debt=0 errors=0\n"
            b"FAILURE src/a.py unbaselined_over_hard lines=21 target=10 hard=20 baseline=- "
            b"actions=split_cohesive_domain,request_reviewed_baseline_exception\n",
        )
        self.assertEqual(completed.stderr, b"")

    def test_structured_configuration_error_exit_two(self):
        with self.temporary_directory() as root:
            completed = run_cli(root, "check", "--format", "json")

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            completed.stdout,
            b'{"schema":"loc-polsia.result/v1","status":"error","summary":{"checked":0,'
            b'"warnings":0,"failures":0,"debt":0,"errors":1},"findings":[{"path":null,'
            b'"code":"missing_config","severity":"error","lines":null,"target":null,'
            b'"hard":null,"baseline":null,"legal_next_actions":["add_valid_config"]}]}\n',
        )
        self.assertEqual(completed.stderr, b"")

    def test_current_directory_is_the_only_config_root(self):
        with self.temporary_directory() as outer:
            make_repository(outer, config=config_bytes())
            Path(outer, ".git").mkdir()
            child = Path(outer, "nested")
            child.mkdir()
            completed = run_cli(child, "check")

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            completed.stdout,
            b"ERROR checked=0 warnings=0 failures=0 debt=0 errors=1\n"
            b"ERROR <config> missing_config lines=- target=- hard=- baseline=- "
            b"actions=add_valid_config\n",
        )
        self.assertEqual(completed.stderr, b"")

    def test_cli_does_not_mutate_checked_or_product_tree(self):
        with self.temporary_directory() as root:
            make_repository(root, lines=1, config=config_bytes())
            checked_before = tree_manifest(root)
            product_before = tree_manifest(PRODUCT_ROOT)
            completed = run_cli(root, "check")
            checked_after = tree_manifest(root)
            product_after = tree_manifest(PRODUCT_ROOT)

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(checked_after, checked_before)
        self.assertEqual(product_after, product_before)
        self.assertFalse(any("__pycache__" in path for path in product_after))


class CliBoundaryTests(unittest.TestCase):
    def run_main(self, arguments, **replacements):
        cli = importlib.import_module("loc_polsia.__main__")
        stdout = type("Stdout", (), {"buffer": io.BytesIO()})()
        stderr = io.StringIO()
        patches = [patch.object(cli, name, value) for name, value in replacements.items()]
        for active_patch in patches:
            active_patch.start()
        try:
            with patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
                exit_code = cli.main(arguments)
        finally:
            for active_patch in reversed(patches):
                active_patch.stop()
        return exit_code, stdout.buffer.getvalue(), stderr.getvalue()

    def test_unexpected_check_and_projection_exceptions_fail_closed(self):
        def fail(*_arguments, **_keywords):
            raise RuntimeError("private detail")

        for arguments, replacements, expected in (
            (["check"], {"check_root": fail}, INTERNAL_TEXT),
            (["check", "--format", "json"], {"project": fail}, INTERNAL_JSON),
        ):
            with self.subTest(arguments=arguments):
                exit_code, stdout, stderr = self.run_main(arguments, **replacements)
                self.assertEqual((exit_code, stdout, stderr), (2, expected, ""))
                self.assertNotIn(b"private detail", stdout)

    def test_fallback_projection_exception_does_not_escape(self):
        def fail(*_arguments, **_keywords):
            raise RuntimeError("private detail")

        for output_format, serializer, expected in (
            ("text", "text_bytes", INTERNAL_TEXT),
            ("json", "json_bytes", INTERNAL_JSON),
        ):
            with self.subTest(output_format=output_format):
                arguments = ["check", "--format", output_format]
                exit_code, stdout, stderr = self.run_main(
                    arguments, check_root=fail, **{serializer: fail}
                )
                self.assertEqual((exit_code, stdout, stderr), (2, expected, ""))

    def test_package_metadata_and_import_surface_are_narrow(self):
        metadata = tomllib.loads((PRODUCT_ROOT / "pyproject.toml").read_text("utf-8"))
        self.assertEqual(metadata["build-system"], {
            "requires": ["setuptools>=68"],
            "build-backend": "setuptools.build_meta",
        })
        self.assertEqual(metadata["project"]["name"], "loc-polsia")
        self.assertEqual(metadata["project"]["version"], "0.1.0")
        self.assertEqual(metadata["project"]["requires-python"], ">=3.11")
        self.assertEqual(metadata["project"]["dependencies"], [])
        self.assertEqual(
            metadata["project"]["scripts"], {"loc-polsia": "loc_polsia.__main__:main"}
        )
        self.assertEqual(metadata["tool"]["setuptools"]["package-dir"], {"": "src"})
        self.assertEqual(metadata["tool"]["setuptools"]["packages"]["find"], {
            "where": ["src"],
            "include": ["loc_polsia"],
        })

        package = importlib.import_module("loc_polsia")
        filesystem = importlib.import_module("loc_polsia.filesystem")
        self.assertEqual(package.__all__, ["check_root"])
        self.assertIs(package.check_root, filesystem.check_root)
        self.assertFalse(hasattr(package, "CheckResult"))

    def test_cli_source_has_no_runtime_escape_hatch(self):
        source = (SOURCE_ROOT / "loc_polsia" / "__main__.py").read_text("utf-8")
        tree = ast.parse(source)
        imported_roots = set()
        called_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
        self.assertTrue(
            imported_roots.isdisjoint(
                {"ctypes", "http", "importlib", "os", "pathlib", "socket", "subprocess", "urllib"}
            )
        )
        self.assertTrue(called_names.isdisjoint({"__import__", "compile", "eval", "exec", "open"}))


if __name__ == "__main__":
    unittest.main()
