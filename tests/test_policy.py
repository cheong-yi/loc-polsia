import unittest

from loc_polsia.check import evaluate_counts, evaluate_policy
from loc_polsia.config import parse_config_bytes


def config(baselines=b""):
    return parse_config_bytes(
        b'''schema = "loc-polsia.config/v1"
include = ["src/*.py"]
exclude = []
target_lines = 10
hard_lines = 20
count = "nonblank_physical_lines"
[baselines]
'''
        + baselines
    )


class PolicyTests(unittest.TestCase):
    def test_every_policy_row(self):
        rows = evaluate_policy(
            {
                "a.py": 10,
                "b.py": 11,
                "c.py": 21,
                "d.py": 30,
                "e.py": 25,
                "f.py": 20,
                "g.py": 22,
            },
            10,
            20,
            {
                "d.py": {"lines": 30, "reason": "debt"},
                "e.py": {"lines": 24, "reason": "debt"},
                "f.py": {"lines": 25, "reason": "debt"},
                "g.py": {"lines": 25, "reason": "debt"},
            },
        )
        self.assertEqual(rows, [
            {
                "path": "b.py",
                "code": "above_target",
                "severity": "warning",
                "lines": 11,
                "target": 10,
                "hard": 20,
                "baseline": None,
                "legal_next_actions": ["reduce_before_growth"],
            },
            {
                "path": "c.py",
                "code": "unbaselined_over_hard",
                "severity": "failure",
                "lines": 21,
                "target": 10,
                "hard": 20,
                "baseline": None,
                "legal_next_actions": [
                    "split_cohesive_domain",
                    "request_reviewed_baseline_exception",
                ],
            },
            {
                "path": "d.py",
                "code": "baseline_debt",
                "severity": "debt",
                "lines": 30,
                "target": 10,
                "hard": 20,
                "baseline": 30,
                "legal_next_actions": ["reduce_cohesive_debt"],
            },
            {
                "path": "e.py",
                "code": "baseline_growth",
                "severity": "failure",
                "lines": 25,
                "target": 10,
                "hard": 20,
                "baseline": 24,
                "legal_next_actions": [
                    "remove_growth",
                    "split_cohesive_domain",
                    "request_reviewed_baseline_increase",
                ],
            },
            {
                "path": "f.py",
                "code": "stale_baseline_compliant",
                "severity": "failure",
                "lines": 20,
                "target": 10,
                "hard": 20,
                "baseline": 25,
                "legal_next_actions": ["remove_baseline"],
            },
            {
                "path": "g.py",
                "code": "baseline_not_tight",
                "severity": "failure",
                "lines": 22,
                "target": 10,
                "hard": 20,
                "baseline": 25,
                "legal_next_actions": ["lower_baseline_to_current"],
            },
        ])

    def test_baseline_debt_passes_and_stale_is_error(self):
        debt_config = config(b'"src/a.py" = { lines = 30, reason = "adoption" }\n')
        result, exit_code = evaluate_counts({"src/a.py": 30}, debt_config)
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["summary"], {"checked": 1, "warnings": 0, "failures": 0, "debt": 1, "errors": 0})
        stale_result, stale_exit = evaluate_counts({}, debt_config)
        self.assertEqual(stale_exit, 2)
        self.assertEqual(stale_result["findings"][0]["code"], "stale_baseline_path")

    def test_candidate_wildcards_are_not_baseline_validated(self):
        rows = evaluate_policy({"src/a*.py": 11}, 10, 20)
        self.assertEqual(rows[0]["path"], "src/a*.py")

    def test_direct_policy_input_rejects_invalid_baseline(self):
        for invalid in (
            None,
            {"lines": True, "reason": "ok"},
            {"lines": 30},
            {"lines": 30, "reason": "ok", "extra": 1},
            {"lines": 30, "reason": "   "},
        ):
            with self.assertRaises(ValueError):
                evaluate_policy({"src/a.py": 11}, 10, 20, {"src/a.py": invalid})

    def test_git_baseline_is_stale_after_config_admission(self):
        git_config = config(b'".git/hidden.py" = { lines = 30, reason = "debt" }\n')
        result, exit_code = evaluate_counts({}, git_config)
        self.assertEqual(exit_code, 2)
        self.assertEqual(result["findings"][0]["code"], "stale_baseline_path")

    def test_failure_exit(self):
        result, exit_code = evaluate_counts({"src/a.py": 21}, config())
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["status"], "fail")


if __name__ == "__main__":
    unittest.main()
