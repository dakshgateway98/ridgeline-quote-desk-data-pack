import unittest

from pipeline import (CONFIG, catalog_indexes, customer_for_source, match_item,
                      rows_to_items, text_to_items, validate_output, OutputValidationError)


class ExtractionMatchingTests(unittest.TestCase):
    def setUp(self):
        self.units = {"EA", "BX", "CS"}
        self.aliases = {"pcs": "EA", "pieces": "EA", "boxes": "BX"}
        self.products = [
            {"part_number": "RL100001", "description": "Sealed bearing 6204 2RS", "unit": "EA", "status": "active", "replaced_by": ""},
            {"part_number": "RL100002", "description": "Hydraulic valve 3/4 inch brass", "unit": "EA", "status": "active", "replaced_by": ""},
            {"part_number": "RL100003", "description": "Hydraulic valve 3/4 inch brass", "unit": "EA", "status": "active", "replaced_by": ""},
        ]

    def test_text_quantity_aliases_notes_and_multiline_specs(self):
        text = """Request for quote
- qty: 10.0 pcs sealed bearing 6204
  2RS rubber seal
Notes: ship in two batches
- valve model V7 x 4
"""
        items, other = text_to_items(text, self.units, self.aliases)
        self.assertEqual(items[0]["quantity"], 10)
        self.assertEqual(items[0]["unit"], "EA")
        self.assertIn("2RS rubber seal", items[0]["description"])
        self.assertIn("two batches", items[0]["note"])
        self.assertEqual(items[1]["quantity"], 4)
        self.assertEqual(items[1]["as_written"], "valve model V7 x 4")
        self.assertTrue(other)

    def test_table_handles_reordered_headers_notes_and_unknown_unit(self):
        rows = [
            ["Customer note", "Requested Qty", "Product Description", "Supplier Part #", "UOM", "Model"],
            ["Urgent", "10 pcs", "Bearing", "V-42", "pcs", "6001-ZZ"],
        ]
        items, _ = rows_to_items(rows, self.units, self.aliases, "Orders")
        self.assertEqual(items[0]["quantity"], 10)
        self.assertEqual(items[0]["unit"], "EA")
        self.assertEqual(items[0]["customer_part_number"], "V-42")
        self.assertIn("6001-ZZ", items[0]["description"])
        self.assertIn("Urgent", items[0]["note"])

    def test_matching_hierarchy_and_no_partial_part_match(self):
        by_id, refs, ref_notes, ambiguous, descriptions, idf = catalog_indexes(
            self.products, [{"competitor_part_number": "VEND-1", "ridgeline_part_number": "RL100001", "notes": ""}])
        item = {"as_written": "RL100001 x 3", "description": "sealed bearing", "quantity": 3}
        result = match_item(item, self.products, by_id, refs, ref_notes, ambiguous, descriptions, idf)
        self.assertEqual(result[0], "RL100001")
        self.assertEqual(result[1], "ridgeline_part_number")
        competitor = {"as_written": "VEND-1 qty 2", "description": "other", "quantity": 2}
        self.assertEqual(match_item(competitor, self.products, by_id, refs, ref_notes, ambiguous, descriptions, idf)[1], "cross_reference")
        partial = {"as_written": "RL1000010", "description": "unrelated", "quantity": 1}
        self.assertIsNone(match_item(partial, self.products, by_id, refs, ref_notes, ambiguous, descriptions, idf)[0])

    def test_invalid_cross_reference_and_description_tie_need_review(self):
        by_id, refs, ref_notes, ambiguous, descriptions, idf = catalog_indexes(
            self.products, [{"competitor_part_number": "BAD-1", "ridgeline_part_number": "MISSING", "notes": ""}])
        invalid = {"as_written": "BAD-1", "description": "Hydraulic valve", "quantity": 1}
        match = match_item(invalid, self.products, by_id, refs, ref_notes, ambiguous, descriptions, idf)
        self.assertIsNone(match[0])
        self.assertIn("invalid_cross_reference", match[3])
        tied = {"as_written": "generic request", "description": "Hydraulic valve 3/4 inch brass", "quantity": 1}
        match = match_item(tied, self.products, by_id, refs, ref_notes, ambiguous, descriptions, idf)
        self.assertIsNone(match[0])
        self.assertIn("ambiguous_description_match", match[3])

    def test_customer_priority_and_ambiguity(self):
        customers = [
            {"customer_id": "C1", "name": "Example Industrial", "email_address": "buyer@one.example", "email_domain": "one.example"},
            {"customer_id": "C2", "name": "Example Industrial West", "email_address": "buyer@two.example", "email_domain": "two.example"},
        ]
        customer, evidence = customer_for_source("From: buyer@one.example", customers)
        self.assertEqual(customer["customer_id"], "C1")
        self.assertEqual(evidence, "email")
        customer, evidence = customer_for_source("buyer@one.example buyer@two.example", customers)
        self.assertIsNone(customer)
        self.assertEqual(evidence, "ambiguous")
        customer, _ = customer_for_source("Customer C2", customers)
        self.assertEqual(customer["customer_id"], "C2")

    def test_output_contract_validation_rejects_bad_quantity_and_review_state(self):
        valid = {"rfq_id": "r1", "customer_id": None, "lines": [{
            "line": 1, "as_written": "part x 0", "quantity": None, "unit": None,
            "ridgeline_part_number": None, "match_method": None, "confidence": 0,
            "needs_review": True, "flags": ["quantity_unparsed"]}]}
        self.assertIs(validate_output(valid), valid)
        valid["lines"][0]["quantity"] = -1
        with self.assertRaises(OutputValidationError):
            validate_output(valid)


if __name__ == "__main__":
    unittest.main()
