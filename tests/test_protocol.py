import copy
import unittest
from typing import cast

from loc_polsia.protocol import ERROR_ACTIONS, error_result, json_bytes, policy_result, result, text_bytes


class ProtocolTests(unittest.TestCase):
    def policy_row(self):
        return {
            "path": "a.py",
            "code": "above_target",
            "severity": "warning",
            "lines": 11,
            "target": 10,
            "hard": 20,
            "baseline": None,
            "legal_next_actions": ["reduce_before_growth"],
        }

    def test_json_is_fixed_and_terminal_lf(self):
        value = error_result("missing_config")
        self.assertEqual(
            json_bytes(value),
            b'{"schema":"loc-polsia.result/v1","status":"error","summary":{"checked":0,"warnings":0,"failures":0,"debt":0,"errors":1},"findings":[{"path":null,"code":"missing_config","severity":"error","lines":null,"target":null,"hard":null,"baseline":null,"legal_next_actions":["add_valid_config"]}]}\n',
        )

    def test_text_sentinels_and_unusual_path(self):
        error = error_result("invalid_utf8", "src/a b.py")
        self.assertEqual(
            text_bytes(error),
            'ERROR checked=0 warnings=0 failures=0 debt=0 errors=1\n'
            'ERROR "src/a b.py" invalid_utf8 lines=- target=- hard=- baseline=- actions=convert_to_utf8_or_exclude\n'.encode(),
        )
        self.assertEqual(text_bytes(policy_result(0, [])), b"PASS checked=0 warnings=0 failures=0 debt=0 errors=0\n")

    def test_finding_order_and_nullability(self):
        value = policy_result(1, [{
            "path": "z.py", "code": "above_target", "severity": "warning",
            "lines": 11, "target": 10, "hard": 20, "baseline": None,
            "legal_next_actions": ["reduce_before_growth"],
        }])
        self.assertEqual(value["findings"][0]["baseline"], None)
        self.assertTrue(json_bytes(value).endswith(b"\n"))

    def test_result_normalizes_and_validates_finding_keys(self):
        unordered = {
            "legal_next_actions": ["reduce_before_growth"],
            "baseline": None,
            "hard": 20,
            "target": 10,
            "lines": 11,
            "severity": "warning",
            "code": "above_target",
            "path": "a.py",
        }
        value = result("pass", 1, 1, 0, 0, 0, [unordered])
        findings = cast(list[dict[str, object]], value["findings"])
        self.assertEqual(list(findings[0]), [
            "path", "code", "severity", "lines", "target", "hard",
            "baseline", "legal_next_actions",
        ])
        self.assertEqual(
            json_bytes(value),
            b'{"schema":"loc-polsia.result/v1","status":"pass","summary":{"checked":1,"warnings":1,"failures":0,"debt":0,"errors":0},"findings":[{"path":"a.py","code":"above_target","severity":"warning","lines":11,"target":10,"hard":20,"baseline":null,"legal_next_actions":["reduce_before_growth"]}]}\n',
        )
        with self.assertRaises(ValueError):
            result("pass", 1, 1, 0, 0, 0, [{**unordered, "extra": 1}])

    def test_result_rejects_invalid_status_counts_and_summary_mismatches(self):
        row = self.policy_row()
        invalid_calls = (
            ("unknown", 1, 1, 0, 0, 0, [row]),
            ("pass", True, 1, 0, 0, 0, [row]),
            ("pass", 1, -1, 0, 0, 0, [row]),
            ("pass", 1, 0, 0, 0, 0, [row]),
            ("fail", 1, 1, 0, 0, 0, [row]),
            ("pass", 0, 1, 0, 0, 0, [row]),
        )
        for arguments in invalid_calls:
            with self.subTest(arguments=arguments[:-1]), self.assertRaises(ValueError):
                result(*arguments)

    def test_policy_findings_enforce_closed_domains_and_state_rows(self):
        mutations = (
            ("code", "unknown"),
            ("code", "above target"),
            ("severity", "failure"),
            ("legal_next_actions", ["unknown_action"]),
            ("legal_next_actions", ["reduce before growth"]),
            ("path", "/absolute.py"),
            ("path", "a\n.py"),
            ("lines", True),
            ("lines", -1),
            ("lines", 10),
            ("target", True),
            ("target", 0),
            ("hard", 10),
            ("baseline", True),
            ("baseline", 20),
            ("baseline", 21),
        )
        for key, value in mutations:
            row = self.policy_row()
            row[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                result("pass", 1, 1, 0, 0, 0, [row])

        duplicate = self.policy_row()
        duplicate["code"] = "unbaselined_over_hard"
        duplicate["severity"] = "failure"
        duplicate["lines"] = 21
        duplicate["legal_next_actions"] = [
            "split_cohesive_domain",
            "request_reviewed_baseline_exception",
        ]
        with self.assertRaises(ValueError):
            result("fail", 1, 1, 1, 0, 0, [self.policy_row(), duplicate])

    def test_error_findings_enforce_mapping_path_form_and_nullability(self):
        for code, action in ERROR_ACTIONS.items():
            path = None if code in {
                "unreadable_root", "missing_config", "invalid_toml", "invalid_config",
                "unreadable_config", "config_too_large", "internal_error",
            } else ("src/bad\\xFF.py" if code == "invalid_filename_bytes" else "src/a.py")
            value = error_result(code, path)
            self.assertEqual(value["findings"][0]["legal_next_actions"], [action])

        with self.assertRaises(ValueError):
            error_result("missing_config", "src/a.py")
        with self.assertRaises(ValueError):
            error_result("invalid_utf8")
        with self.assertRaises(ValueError):
            error_result("unknown")
        for code, path in (
            ("invalid_utf8", "/absolute.py"),
            ("invalid_utf8", "src/a\n.py"),
            ("invalid_filename_bytes", "src/a.py"),
            ("stale_baseline_path", "src/*.py"),
        ):
            with self.subTest(code=code, path=path), self.assertRaises(ValueError):
                error_result(code, path)

        row = copy.deepcopy(error_result("invalid_utf8", "src/a.py")["findings"][0])
        for key, invalid in (
            ("severity", "failure"),
            ("lines", 1),
            ("legal_next_actions", ["report_internal_error"]),
        ):
            changed = dict(row)
            changed[key] = invalid
            with self.subTest(key=key), self.assertRaises(ValueError):
                result("error", 0, 0, 0, 0, 1, [changed])

    def test_serializers_fail_closed_without_leaking_invalid_values(self):
        invalid = result("pass", 1, 1, 0, 0, 0, [self.policy_row()])
        invalid["findings"][0]["path"] = "/private/secret\nvalue.py"
        self.assertEqual(
            json_bytes(invalid),
            b'{"schema":"loc-polsia.result/v1","status":"error","summary":{"checked":0,"warnings":0,"failures":0,"debt":0,"errors":1},"findings":[{"path":null,"code":"internal_error","severity":"error","lines":null,"target":null,"hard":null,"baseline":null,"legal_next_actions":["report_internal_error"]}]}\n',
        )
        self.assertEqual(
            text_bytes(invalid),
            b"ERROR checked=0 warnings=0 failures=0 debt=0 errors=1\n"
            b"ERROR <none> internal_error lines=- target=- hard=- baseline=- actions=report_internal_error\n",
        )

    def test_policy_projection_is_exact(self):
        value = policy_result(3, [
            {
                "path": "c.py", "code": "baseline_growth", "severity": "failure",
                "lines": 25, "target": 10, "hard": 20, "baseline": 24,
                "legal_next_actions": [
                    "remove_growth", "split_cohesive_domain",
                    "request_reviewed_baseline_increase",
                ],
            },
            {
                "path": "a.py", "code": "above_target", "severity": "warning",
                "lines": 11, "target": 10, "hard": 20, "baseline": None,
                "legal_next_actions": ["reduce_before_growth"],
            },
            {
                "path": "b.py", "code": "baseline_debt", "severity": "debt",
                "lines": 30, "target": 10, "hard": 20, "baseline": 30,
                "legal_next_actions": ["reduce_cohesive_debt"],
            },
        ])
        self.assertEqual(
            json_bytes(value),
            b'{"schema":"loc-polsia.result/v1","status":"fail","summary":{"checked":3,"warnings":1,"failures":1,"debt":1,"errors":0},"findings":[{"path":"a.py","code":"above_target","severity":"warning","lines":11,"target":10,"hard":20,"baseline":null,"legal_next_actions":["reduce_before_growth"]},{"path":"b.py","code":"baseline_debt","severity":"debt","lines":30,"target":10,"hard":20,"baseline":30,"legal_next_actions":["reduce_cohesive_debt"]},{"path":"c.py","code":"baseline_growth","severity":"failure","lines":25,"target":10,"hard":20,"baseline":24,"legal_next_actions":["remove_growth","split_cohesive_domain","request_reviewed_baseline_increase"]}]}\n',
        )
        self.assertEqual(
            text_bytes(value),
            b"FAIL checked=3 warnings=1 failures=1 debt=1 errors=0\n"
            b"WARNING a.py above_target lines=11 target=10 hard=20 baseline=- actions=reduce_before_growth\n"
            b"DEBT b.py baseline_debt lines=30 target=10 hard=20 baseline=30 actions=reduce_cohesive_debt\n"
            b"FAILURE c.py baseline_growth lines=25 target=10 hard=20 baseline=24 actions=remove_growth,split_cohesive_domain,request_reviewed_baseline_increase\n",
        )


if __name__ == "__main__":
    unittest.main()
