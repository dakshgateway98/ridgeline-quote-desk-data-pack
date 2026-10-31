import unittest
from unittest.mock import patch

from app_config import CONFIG
from matching.engine import catalog_indexes, match_item


class LLMFallbackTests(unittest.TestCase):
    def setUp(self):
        self.products = [
            {"part_number": "P-1", "description": "Carbon filter 10 micron", "unit": "EA", "status": "active"},
            {"part_number": "P-2", "description": "Oil filter 20 micron", "unit": "EA", "status": "active"},
        ]
        indexes = catalog_indexes(self.products, [])
        self.by_id, self.refs, self.ref_notes, self.ambiguous, self.descriptions, self.idf = indexes
        self.item = {"as_written": "replacement element for the polishing unit", "description": "replacement element for polishing unit"}

    def run_match(self):
        return match_item(self.item, self.products, self.by_id, self.refs,
                          self.ref_notes, self.ambiguous, self.descriptions, self.idf)

    def test_llm_is_opt_in_and_success_is_always_reviewed(self):
        llm = CONFIG["matching"]["llm"]
        original = llm["enabled"]
        llm["enabled"] = True
        try:
            with patch("matching.engine.llm_candidate", return_value=("P-1", 0.91, "Compatible element")) as call:
                result = self.run_match()
            call.assert_called_once()
            self.assertEqual(result[0], "P-1")
            self.assertEqual(result[1], "llm")
            self.assertIn("llm_match_needs_review", result[3])
        finally:
            llm["enabled"] = original

    def test_llm_provider_failure_is_reported_without_guessing(self):
        llm = CONFIG["matching"]["llm"]
        original = llm["enabled"]
        llm["enabled"] = True
        try:
            with patch("matching.engine.llm_candidate", side_effect=RuntimeError("provider down")):
                result = self.run_match()
            self.assertIsNone(result[0])
            self.assertIn("llm_fallback_failed", result[3])
            self.assertIn("provider down", result[4])
        finally:
            llm["enabled"] = original


if __name__ == "__main__":
    unittest.main()
