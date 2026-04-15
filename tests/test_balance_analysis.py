import os
import tempfile
import unittest

from balance_analysis import analyze_report_file, parse_contributor_scores


SAMPLE_REPORT = """# Contribution Report: sample-org/sample-repo

- Repository URL: https://github.com/sample-org/sample-repo
- Fork: Yes

## Contributor Rankings

| Rank | Contributor | Type | Commits | Lines Added | Lines Removed | Merged PRs | Issues Closed | Issue Comments | Weighted Score |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | alice | user | 10 | 100 | 50 | 2 | 3 | 4 | 30.5 |
| 2 | build[bot] | bot | 5 | 20 | 10 | 0 | 0 | 1 | 8.0 |
| 3 | bob | user | 4 | 10 | 10 | 0 | 1 | 0 | 6.0 |
"""


class BalanceAnalysisTests(unittest.TestCase):
    def test_parse_contributor_scores(self):
        self.assertEqual(parse_contributor_scores(SAMPLE_REPORT), [30.5, 8.0, 6.0])

    def test_analyze_report_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = os.path.join(temp_dir, "report.md")
            with open(report_path, "w", encoding="utf-8") as handle:
                handle.write(SAMPLE_REPORT)

            result = analyze_report_file(report_path)
            self.assertEqual(result.repository, "sample-org/sample-repo")
            self.assertEqual(result.is_fork, "Yes")
            self.assertEqual(result.contributor_count, 3)
            self.assertGreater(result.score_stddev, 0)


if __name__ == "__main__":
    unittest.main()
