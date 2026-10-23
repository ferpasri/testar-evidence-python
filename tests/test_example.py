from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.local_model import create_trace_directory, main
from testar_evidence import EvidenceReader


class ExampleTraceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.clock = patch("examples.local_model.datetime")
        clock = self.clock.start()
        self.addCleanup(self.clock.stop)
        clock.now.return_value = datetime(2026, 10, 22, 15, 30, 12, 123456)

    def test_trace_name_includes_application_objective_and_millisecond_timestamp(self):
        directory = create_trace_directory(self.parent, "parabank", "denied-loan")
        self.assertEqual(directory.name, "parabank_denied-loan_trace-2026-10-22_15-30-12-123")
        self.assertTrue(directory.is_dir())

    def test_collision_keeps_existing_content_and_reserves_a_new_directory(self):
        first = create_trace_directory(self.parent, "app", "objective")
        original = first / "existing-evidence.txt"
        original.write_text("keep this evidence", encoding="utf-8")
        second = create_trace_directory(self.parent, "app", "objective")
        third = create_trace_directory(self.parent, "app", "objective")
        self.assertEqual(second.name, first.name + "-2")
        self.assertEqual(third.name, first.name + "-3")
        self.assertEqual(original.read_text(encoding="utf-8"), "keep this evidence")
        self.assertEqual(list(second.iterdir()), [])

    def test_labels_cannot_escape_the_parent_or_create_invalid_filenames(self):
        directory = create_trace_directory(self.parent, "../../App: one?", "C:\\work\\a*|<test>")
        self.assertEqual(directory.parent, self.parent)
        self.assertNotRegex(directory.name, r'[<>:"/\\|?*]')
        fallback = create_trace_directory(self.parent, "...", "???")
        self.assertTrue(fallback.name.startswith("sut_objective_trace-"))
        long_label = create_trace_directory(self.parent, "a" * 1000, "b" * 1000)
        self.assertLess(len(long_label.name), 120)

    def test_each_run_has_independent_evidence_even_when_timestamps_match(self):
        with redirect_stdout(StringIO()):
            first = main(self.parent, "Application / demo", "Submit form")
            first_bytes = (first / "evidence.jsonl").read_bytes()
            second = main(self.parent, "Application / demo", "Submit form")
        left = EvidenceReader.read(first)
        right = EvidenceReader.read(second)
        self.assertNotEqual(first, second)
        self.assertNotEqual(left.manifest["model"], right.manifest["model"])
        self.assertTrue(set(left.runs).isdisjoint(right.runs))
        self.assertEqual((first / "evidence.jsonl").read_bytes(), first_bytes)
        for model in (left, right):
            self.assertEqual(model.event_count, 9)
            self.assertEqual(len(model.links()), 1)
            self.assertEqual(model.checks[0]["status"], "passed")
            run = next(iter(model.runs.values()))["run"]
            self.assertEqual(run["properties"], {
                "example.sut": "Application / demo",
                "evidence.test_objective": "Submit form",
            })


    def test_omitted_descriptions_remain_absent_after_reopening(self):
        with redirect_stdout(StringIO()):
            directory = main(self.parent)
        model = EvidenceReader.read(directory)
        properties = next(iter(model.runs.values()))["run"]["properties"]
        self.assertEqual(properties, {"example.sut": "demo"})
        self.assertTrue(directory.name.startswith("demo_run_trace-"))

    def test_descriptions_preserve_flexible_labels_unicode_and_spacing(self):
        with redirect_stdout(StringIO()):
            directory = main(self.parent, "demo", "  Request loan — prêt  ", "  QA équipe / custom CI  ")
        model = EvidenceReader.read(directory)
        properties = next(iter(model.runs.values()))["run"]["properties"]
        self.assertEqual(properties["evidence.origin"], "  QA équipe / custom CI  ")
        self.assertEqual(properties["evidence.test_objective"], "  Request loan — prêt  ")
        self.assertNotIn("evidence.adapter", properties)


if __name__ == "__main__":
    unittest.main()
