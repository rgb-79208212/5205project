"""Evaluation must keep denominators and pending human labels honest."""

import csv
import tempfile
import unittest
from pathlib import Path

from evaluation.cases import cases
from evaluation.run import metrics, score_review


class EvaluationMetrics(unittest.TestCase):
    def test_undefined_precision_is_not_reported_as_perfect(self):
        result = metrics(
            [{"expected_supported": True, "published": False}], "published"
        )
        self.assertIsNone(result["approved_support_rate"])
        self.assertIsNone(result["false_release_rate"])
        self.assertEqual(result["false_withhold_rate"], 1)

    def test_uncertain_human_labels_are_pending_and_publication_flag_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=["human_support", "system_published"]
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"human_support": "supported", "system_published": "true"},
                        {"human_support": "unsupported", "system_published": "true"},
                        {"human_support": "uncertain", "system_published": "false"},
                        {"human_support": "", "system_published": "false"},
                    ]
                )
            result = score_review(path)
        self.assertEqual(result["annotated"], 2)
        self.assertEqual(result["pending_or_ambiguous"], 2)
        self.assertEqual(result["metrics"]["approved_support_rate"], 0.5)

    def test_synthetic_expectations_are_explicitly_distinct_from_human_gold(self):
        dataset = cases()
        self.assertEqual(len(dataset), 16)
        self.assertEqual(sum(len(c["candidates"]) for c in dataset), 64)
        self.assertTrue(all(c["synthetic"] for c in dataset))
        self.assertTrue(all("not independent" in c["label_origin"] for c in dataset))
