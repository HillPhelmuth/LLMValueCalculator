import json
import tempfile
import unittest
from pathlib import Path

from calibration.rehearsal import run_rehearsal


class Phase6RehearsalTests(unittest.TestCase):
    def test_offline_rehearsal_resumes_without_duplicate_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = run_rehearsal(Path(directory) / "rehearsal")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual("passed", report["status"])
            self.assertTrue(report["checks"]["interruption_resume"])
            self.assertTrue(report["checks"]["no_duplicate_request_hashes"])
            self.assertEqual("review_required_unpromoted", report["promotion_status"])


if __name__ == "__main__":
    unittest.main()
