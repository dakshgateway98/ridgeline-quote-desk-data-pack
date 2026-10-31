import tempfile
import unittest
import zipfile
from pathlib import Path

from document_readers import read_xlsx
from pipeline import rows_to_items


def make_workbook(path):
    workbook = '''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets><sheet name="Orders A" sheetId="1" r:id="rId1"/><sheet name="Orders B" sheetId="2" r:id="rId2"/></sheets>
    </workbook>'''
    relationships = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Target="/xl/worksheets/sheet1.xml"/>
      <Relationship Id="rId2" Target="/xl/worksheets/sheet2.xml"/>
    </Relationships>'''
    sheet_one = '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData>
        <row r="1"><c r="A1" t="inlineStr"><is><t>Part #</t></is></c><c r="B1" t="inlineStr"><is><t>Item Description</t></is></c><c r="C1" t="inlineStr"><is><t>Qty</t></is></c><c r="D1" t="inlineStr"><is><t>UOM</t></is></c></row>
        <row r="2"><c r="A2" t="inlineStr"><is><t>COMP-1</t></is></c><c r="B2" t="inlineStr"><is><t>Steel bearing</t></is></c><c r="C2"><v>10</v></c><c r="D2" t="inlineStr"><is><t>EA</t></is></c></row>
        <row r="3"><c r="B3" t="inlineStr"><is><t>sealed, model X</t></is></c></row>
      </sheetData><mergeCells count="1"><mergeCell ref="A2:A3"/></mergeCells></worksheet>'''
    sheet_two = '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData>
        <row r="1"><c r="A1" t="inlineStr"><is><t>Description</t></is></c><c r="B1" t="inlineStr"><is><t>Quantity</t></is></c><c r="C1" t="inlineStr"><is><t>Quantity</t></is></c></row>
        <row r="2"><c r="A2" t="inlineStr"><is><t>Valve body</t></is></c><c r="B2"><v>4</v></c><c r="C2"><v>5</v></c></row>
      </sheetData></worksheet>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_one)
        archive.writestr("xl/worksheets/sheet2.xml", sheet_two)


class ExcelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "multi.xlsx"
        make_workbook(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_all_sheets_and_expands_merged_cells(self):
        sheets = read_xlsx(self.path)
        self.assertEqual([name for name, _ in sheets], ["Orders A", "Orders B"])
        first = sheets[0][1]
        self.assertEqual(first[2][0], "COMP-1")

    def test_extracts_rows_and_preserves_continuation_and_sheet_name(self):
        name, rows = read_xlsx(self.path)[0]
        items, _ = rows_to_items(rows, {"EA"}, {}, source_name=name)
        self.assertEqual(len(items), 1)
        self.assertIn("Steel bearing", items[0]["description"])
        self.assertEqual(items[0]["quantity"], 10)
        self.assertIn("Orders A", items[0]["note"])

    def test_duplicate_quantity_columns_are_flagged(self):
        _, rows = read_xlsx(self.path)[1]
        items, _ = rows_to_items(rows, {"EA"}, {})
        self.assertIn("conflicting_duplicate_columns", items[0]["flags"])


if __name__ == "__main__":
    unittest.main()
