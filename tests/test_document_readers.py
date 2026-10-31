import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch
import zipfile

from document_readers import DocumentReadError, html_to_text, read_docx, read_image, read_pdf, read_source


class DocumentReaderTests(unittest.TestCase):
    def test_word_paragraphs_and_table_rows_are_separate(self):
        document = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
          <w:p><w:r><w:t>Quote request</w:t></w:r></w:p>
          <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Item</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Qty</w:t></w:r></w:p></w:tc></w:tr>
          <w:tr><w:tc><w:p><w:r><w:t>Bearing 6001</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>5</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        </w:body></w:document>'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", document)
            result = read_docx(path)
        self.assertEqual(result.text, "Quote request")
        self.assertEqual(result.tables[0][0], "Table 1")
        self.assertEqual(result.tables[0][1][1], ["Bearing 6001", "5"])

    def test_email_html_body_and_attachments_are_kept(self):
        message = EmailMessage()
        message["From"] = "buyer@example.org"
        message["Subject"] = "RFQ"
        message.set_content("Please quote 5 EA bearings")
        message.add_alternative("<html><body><p>Please quote 5 EA bearings</p><table><tr><td>Part</td><td>Qty</td></tr></table></body></html>", subtype="html")
        message.add_attachment(b"pdf bytes", maintype="application", subtype="pdf", filename="list.pdf")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.eml"
            path.write_bytes(message.as_bytes())
            result = read_source(path)
        self.assertIn("bearings", result.text)
        self.assertTrue(result.alternate_texts)
        self.assertEqual(result.attachments[0].filename, "list.pdf")
        self.assertIn("buyer@example.org", " ".join(result.other_content))

    def test_html_keeps_table_cells_and_omits_scripts(self):
        rendered = html_to_text("<p>Order</p><table><tr><td>Motor</td><td>2 EA</td></tr></table><script>bad()</script>")
        self.assertIn("Motor", rendered)
        self.assertIn("2 EA", rendered)
        self.assertNotIn("bad()", rendered)

    def test_pdf_uses_text_first_and_skips_ocr_when_sufficient(self):
        with patch("readers.adapters._pdf_text", return_value=("A sufficiently long digital product request with several useful words.", False)) as pdf_text, \
             patch("readers.adapters._ocr_pdf") as ocr:
            result = read_pdf(Path("digital.pdf"), minimum_text_chars=10, minimum_text_words=5)
        self.assertIn("digital product request", result.text)
        pdf_text.assert_called_once()
        ocr.assert_not_called()

    def test_pdf_uses_ocr_when_text_is_empty_and_carries_confidence(self):
        with patch("readers.adapters._pdf_text", return_value=""), \
             patch("readers.adapters._ocr_pdf", return_value=("Recognized order text", 0.42, False)) as ocr:
            result = read_pdf(Path("scanned.pdf"), ocr_threshold=0.65)
        self.assertEqual(result.text, "Recognized order text")
        self.assertEqual(result.confidence, 0.42)
        self.assertTrue(any("low" in warning.lower() for warning in result.warnings))
        ocr.assert_called_once()

    def test_image_ocr_confidence_and_unsupported_format_errors(self):
        with patch("readers.adapters._tesseract", return_value=("blurred text", 0.3)):
            result = read_image(Path("scan.webp"))
        self.assertEqual(result.confidence, 0.3)
        self.assertTrue(result.warnings)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(b"data")
            with self.assertRaises(DocumentReadError):
                read_source(path)


if __name__ == "__main__":
    unittest.main()
