import unittest

from loc_polsia.check import SourceError, count_nonblank_physical_lines
from loc_polsia.config import (
    ConfigError,
    canonical_path,
    canonical_raw_path,
    parse_config_bytes,
    pattern_matches,
    validate_candidate_path,
    validate_pattern,
    validate_path,
)


VALID = b'''schema = "loc-polsia.config/v1"
include = ["src/**/*.py"]
exclude = ["**/generated/**"]
target_lines = 500
hard_lines = 700
count = "nonblank_physical_lines"
[baselines]
"src/legacy.py" = { lines = 900, reason = "Brownfield debt at adoption" }
'''


class ContractTests(unittest.TestCase):
    def test_config_and_thresholds(self):
        config = parse_config_bytes(VALID)
        self.assertEqual(config.target_lines, 500)
        self.assertEqual(config.baselines["src/legacy.py"]["lines"], 900)
        for old, replacement, message in (
            (b"target_lines = 500", b"target_lines = true", "target_lines"),
            (b"hard_lines = 700", b"hard_lines = 500", "hard_lines"),
            (b'count = "nonblank_physical_lines"', b'count = "lines"', "count is invalid"),
            (
                b'include = ["src/**/*.py"]',
                b'include = ["src/**/x.py", "src/**/x.py"]',
                "include entries must be unique",
            ),
        ):
            with self.subTest(replacement=replacement):
                self.assertEqual(VALID.count(old), 1)
                with self.assertRaises(ConfigError) as caught:
                    parse_config_bytes(VALID.replace(old, replacement))
                self.assertEqual(caught.exception.code, "invalid_config")
                self.assertIn(message, str(caught.exception))

    def test_pattern_validation_and_matching(self):
        self.assertTrue(pattern_matches("src/**/*.py", "src/a.py"))
        self.assertTrue(pattern_matches("src/**/*.py", "src/nested/a.py"))
        self.assertTrue(pattern_matches("**/generated/**", "generated/a.py"))
        self.assertTrue(pattern_matches("**/generated/**", "src/generated/nested/a.py"))
        self.assertFalse(pattern_matches("src/**/*.py", "src/a.txt"))
        for value in ("", "/src", "src//x", "src/../x", "src/a**/x", "src/[x].py", "src/.git/x.py"):
            with self.assertRaises(ConfigError):
                validate_pattern(value)
        for value in ("/src/a.py", "src//a.py", "src/../a.py", "src/*.py"):
            with self.assertRaises(ConfigError):
                validate_path(value)

    def test_canonical_paths_are_injective(self):
        self.assertEqual(canonical_path("a\\b/\x01/é"), "a\\\\b/\\x01/é")
        self.assertEqual(canonical_raw_path([b"bad\xff", b"a\\b"]), "bad\\xFF/a\\\\b")
        self.assertEqual(validate_candidate_path("src/a*.py"), ("src", "a*.py"))
        self.assertEqual(validate_candidate_path("src/a\\b.py"), ("src", "a\\b.py"))

    def test_counting_boundaries(self):
        self.assertEqual(count_nonblank_physical_lines(b""), 0)
        self.assertEqual(count_nonblank_physical_lines("a\nb\r\nc\r\n".encode()), 3)
        self.assertEqual(count_nonblank_physical_lines(" \t\u2003\n# comment\n\ufeff\n".encode()), 2)
        with self.assertRaisesRegex(SourceError, "invalid_utf8"):
            count_nonblank_physical_lines(b"\xff")
        with self.assertRaisesRegex(SourceError, "source_too_large"):
            count_nonblank_physical_lines(b"x", max_bytes=0)


if __name__ == "__main__":
    unittest.main()
