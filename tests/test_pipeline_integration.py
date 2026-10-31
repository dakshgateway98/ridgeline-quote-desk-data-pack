import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline
from pipeline import validate_output


class PipelineIntegrationTests(unittest.TestCase):
    def test_batch_writes_valid_outputs_and_removes_stale_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "rfqs").mkdir()
            (root / "outputs").mkdir()
            (root / "outputs" / "removed_input.json").write_text("{}")
            (root / "rfqs" / "good.txt").write_text(
                "From: buyer@example.org\nTo: quotes@example.org\nSubject: RFQ\n\nExample Customer\n- 3 EA Carbon filter\n")
            (root / "rfqs" / "bad.unsupported").write_bytes(b"unsupported")
            with (root / "products.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["part_number", "description", "unit", "status", "replaced_by"])
                writer.writeheader()
                writer.writerow({"part_number": "P-1", "description": "Carbon filter", "unit": "EA", "status": "active"})
            with (root / "cross_reference.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["competitor_part_number", "ridgeline_part_number", "notes"])
                writer.writeheader()
            with (root / "customers.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["customer_id", "name"])
                writer.writeheader()
                writer.writerow({"customer_id": "C1", "name": "Example Customer"})

            with patch.object(pipeline, "ROOT", root):
                pipeline.run()

            outputs = sorted((root / "outputs").glob("*.json"))
            self.assertEqual({path.name for path in outputs}, {"good.json", "bad.json"})
            for path in outputs:
                validate_output(json.loads(path.read_text()))
            failed = json.loads((root / "outputs" / "bad.json").read_text())
            self.assertTrue(failed["lines"][0]["needs_review"])
            self.assertTrue(failed["lines"][0]["note"])


if __name__ == "__main__":
    unittest.main()
