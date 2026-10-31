import unittest

from evaluate import evaluate_records


class EvaluationTests(unittest.TestCase):
    def test_metrics_include_customer_review_and_threshold_sweep(self):
        gold = [{"rfq_id": "r1", "customer_id": "C1", "lines": [
            {"line": 1, "as_written": "part", "quantity": 2, "unit": "EA", "expected_part_number": "P1"},
            {"line": 2, "as_written": "unknown", "quantity": 1, "unit": "EA", "expected_part_number": None},
        ]}]
        predicted = {"r1": {"customer_id": "C1", "lines": [
            {"line": 1, "as_written": "part", "quantity": 2, "unit": "EA", "ridgeline_part_number": "P1",
             "confidence": 0.9, "needs_review": False, "flags": []},
            {"line": 2, "as_written": "unknown", "quantity": 1, "unit": "EA", "ridgeline_part_number": "P2",
             "confidence": 0.4, "needs_review": True, "flags": ["unmatched"]},
        ]}}
        result = evaluate_records(gold, predicted, [0.5, 0.8])
        metrics = result["additional_metrics"]
        self.assertEqual(result["match_precision"], 0.5)
        self.assertEqual(result["match_recall"], 1.0)
        self.assertEqual(metrics["customer_identification_rate"], 1.0)
        self.assertEqual(metrics["review_rate"], 0.5)
        self.assertEqual(metrics["false_positive_matches"], 1)
        self.assertEqual(metrics["false_negative_matches"], 0)


if __name__ == "__main__":
    unittest.main()
