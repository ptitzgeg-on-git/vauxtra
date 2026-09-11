"""The npm audit runner must tell a verdict apart from a registry that gave none.

Both used to arrive as exit code 1, which is the whole defect: a 400 from the registry read
exactly like a high-severity advisory, so CI went red with a message naming no package and
the repair was to re-run the job until the registry answered.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_npm_audit import CLEAN, UNREACHABLE, VULNERABLE, classify, counts_at_or_above


def report(**severities) -> str:
    counts = dict.fromkeys(["info", "low", "moderate", "high", "critical"], 0)
    counts.update(severities)
    counts["total"] = sum(counts[name] for name in counts if name != "total")
    return json.dumps({"metadata": {"vulnerabilities": counts}})


class TestClassifyVerdicts:
    def test_no_advisories_is_clean(self):
        outcome, _ = classify(report(), "high")
        assert outcome == CLEAN

    def test_advisories_below_the_level_are_clean(self):
        outcome, message = classify(report(low=3, moderate=2), "high")
        assert outcome == CLEAN
        assert "5 advisory/advisories below high" in message

    def test_an_advisory_at_the_level_fails(self):
        outcome, message = classify(report(high=1), "high")
        assert outcome == VULNERABLE
        assert "1 high" in message

    def test_severities_above_the_level_count_too(self):
        outcome, message = classify(report(critical=2), "high")
        assert outcome == VULNERABLE
        assert "2 critical" in message

    def test_the_level_is_honoured(self):
        assert classify(report(moderate=1), "high")[0] == CLEAN
        assert classify(report(moderate=1), "moderate")[0] == VULNERABLE

    def test_reported_severities_keep_npm_order(self):
        found = counts_at_or_above(
            {"low": 1, "moderate": 4, "high": 2, "critical": 1}, "moderate"
        )
        assert list(found) == ["moderate", "high", "critical"]


class TestClassifySilence:
    """None of these is a vulnerability, and none of them may be reported as one."""

    def test_a_registry_error_is_not_a_finding(self):
        outcome, message = classify(
            json.dumps(
                {
                    "error": {
                        "code": "E400",
                        "summary": "400 Bad Request - POST https://registry.npmjs.org/-/npm/v1/security/audits/quick",
                        "detail": "",
                    }
                }
            ),
            "high",
        )
        assert outcome == UNREACHABLE
        assert "400 Bad Request" in message

    def test_plain_text_output_is_not_a_finding(self):
        outcome, message = classify("npm ERR! code E503\nnpm ERR! 503 Service Unavailable", "high")
        assert outcome == UNREACHABLE
        assert "E503" in message

    def test_empty_output_is_not_a_finding(self):
        outcome, message = classify("", "high")
        assert outcome == UNREACHABLE
        assert "no output" in message

    def test_a_report_without_metadata_is_not_a_finding(self):
        outcome, _ = classify(json.dumps({"auditReportVersion": 2}), "high")
        assert outcome == UNREACHABLE

    def test_a_json_list_is_not_a_finding(self):
        outcome, _ = classify(json.dumps(["something", "else"]), "high")
        assert outcome == UNREACHABLE

    def test_null_severities_are_read_as_zero(self):
        # npm has shipped nulls here; `None > 0` raises, and a crashing gate is a red build
        # for the same non-reason as the flake this script exists to remove.
        outcome, _ = classify(
            json.dumps({"metadata": {"vulnerabilities": {"high": None, "critical": None}}}),
            "high",
        )
        assert outcome == CLEAN
