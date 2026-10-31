import unittest

from app_config import CONFIG, validate_config


class ConfigurationTests(unittest.TestCase):
    def test_settings_are_grouped_and_valid(self):
        validate_config(CONFIG)
        self.assertIn("supported_units", CONFIG)
        self.assertIn("unit_aliases", CONFIG)
        self.assertIn("pdf_dpi", CONFIG["ocr"])
        self.assertIn("max_attachment_bytes", CONFIG["processing_limits"])
        self.assertFalse(CONFIG["matching"]["llm"]["enabled"])

    def test_invalid_threshold_and_semantic_settings_fail_early(self):
        config = {
            "review_threshold": 1.2,
            "ocr": {"review_confidence_threshold": 0.65},
            "processing_limits": {"max_pdf_pages": 10},
            "matching": {"semantic": {"enabled": False}},
        }
        with self.assertRaisesRegex(ValueError, "thresholds"):
            validate_config(config)
        config["review_threshold"] = 0.8
        config["matching"]["llm"] = {"enabled": True}
        with self.assertRaisesRegex(ValueError, "endpoint_url"):
            validate_config(config)


if __name__ == "__main__":
    unittest.main()
