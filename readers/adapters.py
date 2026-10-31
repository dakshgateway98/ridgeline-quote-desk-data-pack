"""Independent readers for common RFQ document formats.

Every reader returns text, named tables, attachments, warnings and (when
available) extraction confidence. Unsupported or unreadable content raises a
descriptive RuntimeError so the caller can preserve it as a review item.
"""

from __future__ import annotations

import csv
import email
import email.policy
from dataclasses import dataclass, field
from html.parser import HTMLParser
import json
import mimetypes
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


class DocumentReadError(RuntimeError):
    """Expected failure while reading or converting a source document."""


@dataclass
class Attachment:
    filename: str
    data: bytes
    content_type: str = "application/octet-stream"


@dataclass
class DocumentData:
    text: str = ""
    tables: list[tuple[str, list[list[str]]]] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    other_content: list[str] = field(default_factory=list)
    alternate_texts: list[str] = field(default_factory=list)
    confidence: float | None = None


class HTMLText(HTMLParser):
    """Convert HTML to readable text while keeping blocks and table cells apart."""

    BLOCKS = {"address", "article", "br", "div", "footer", "h1", "h2", "h3", "h4",
              "li", "p", "section", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag == "img" and not self.suppressed:
            alt = dict(attrs).get("alt")
            if alt:
                self.parts.append(f" [image: {alt}] ")
        if tag in {"script", "style", "head"}:
            self.suppressed += 1
        elif not self.suppressed and tag in self.BLOCKS:
            self.parts.append("\n")
        elif not self.suppressed and tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head"} and self.suppressed:
            self.suppressed -= 1
        elif not self.suppressed and tag in self.BLOCKS:
            self.parts.append("\n")
        elif not self.suppressed and tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_data(self, data):
        if not self.suppressed and data.strip():
            self.parts.append(data.strip())


def html_to_text(source: str) -> str:
    parser = HTMLText()
    parser.feed(source)
    return re.sub(r"[ \t]+", " ", "".join(parser.parts)).strip()


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        raise ValueError(f"Invalid spreadsheet cell reference: {reference}")
    number = 0
    for char in letters.group():
        number = number * 26 + ord(char) - 64
    return number - 1


def read_xlsx(path: Path) -> list[tuple[str, list[list[str]]]]:
    """Read all XLSX sheets, shared/inline strings, sparse rows and merged cells."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        shared_strings = []
        if "xl/sharedStrings.xml" in names:
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = ["".join(node.text or "" for node in item.findall(".//m:t", ns))
                              for item in shared_root.findall("m:si", ns)]
        sheets = []
        for sheet in workbook.findall("m:sheets/m:sheet", ns):
            relation_id = sheet.attrib.get(f"{{{ns['r']}}}id")
            target = targets.get(relation_id)
            if not target:
                continue
            sheet_path = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                                             else posixpath.join("xl", target))
            root = ET.fromstring(archive.read(sheet_path))
            rows_by_number: dict[int, list[str]] = {}
            max_column = 0
            for row_element in root.findall(".//m:sheetData/m:row", ns):
                row_number = int(row_element.attrib.get("r", "0"))
                row: list[str] = []
                for cell in row_element.findall("m:c", ns):
                    column = _column_index(cell.attrib["r"])
                    max_column = max(max_column, column + 1)
                    while len(row) <= column:
                        row.append("")
                    value = cell.find("m:v", ns)
                    inline = cell.find("m:is", ns)
                    if cell.attrib.get("t") == "s" and value is not None:
                        row[column] = shared_strings[int(value.text)]
                    elif inline is not None:
                        row[column] = "".join(text.text or "" for text in inline.findall(".//m:t", ns))
                    else:
                        row[column] = value.text if value is not None else ""
                rows_by_number[row_number] = row

            # Expand merged ranges so the value remains available to each affected cell.
            for merged in root.findall(".//m:mergeCell", ns):
                start, end = merged.attrib["ref"].split(":", 1)
                start_match, end_match = re.fullmatch(r"([A-Z]+)(\d+)", start), re.fullmatch(r"([A-Z]+)(\d+)", end)
                if not start_match or not end_match:
                    continue
                start_col, start_row = _column_index(start_match.group(1)), int(start_match.group(2))
                end_col, end_row = _column_index(end_match.group(1)), int(end_match.group(2))
                source = rows_by_number.get(start_row, [])
                value = source[start_col] if start_col < len(source) else ""
                for row_number in range(start_row, end_row + 1):
                    row = rows_by_number.setdefault(row_number, [])
                    while len(row) < max(max_column, end_col + 1):
                        row.append("")
                    for column in range(start_col, end_col + 1):
                        if not row[column]:
                            row[column] = value

            if rows_by_number:
                width = max(max_column, max((len(row) for row in rows_by_number.values()), default=0))
                last_row = max(rows_by_number)
                rows = [rows_by_number.get(number, []) + [""] * max(0, width - len(rows_by_number.get(number, [])))
                        for number in range(1, last_row + 1)]
            else:
                rows = []
            sheets.append((sheet.attrib.get("name", "Sheet"), rows))
    return sheets


def read_docx(path: Path) -> DocumentData:
    """Read paragraphs and tables in document order; table cells stay grouped by row."""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find(".//w:body", ns)
    paragraphs: list[str] = []
    tables: list[tuple[str, list[list[str]]]] = []
    if body is None:
        return DocumentData(warnings=["Word document has no readable body."])
    table_number = 0
    for child in body:
        if child.tag.endswith("}p"):
            text = "".join(node.text or "" for node in child.findall(".//w:t", ns)).strip()
            if text:
                paragraphs.append(text)
        elif child.tag.endswith("}tbl"):
            table_number += 1
            rows = []
            for row_element in child.findall("w:tr", ns):
                cells = []
                for cell in row_element.findall("w:tc", ns):
                    cell_paragraphs = ["".join(node.text or "" for node in para.findall(".//w:t", ns)).strip()
                                       for para in cell.findall("w:p", ns)]
                    cells.append(" ".join(part for part in cell_paragraphs if part))
                rows.append(cells)
            tables.append((f"Table {table_number}", rows))
    return DocumentData(text="\n".join(paragraphs), tables=tables)


def _run_text(command: list[str], timeout: int) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"Command failed: {Path(command[0]).name}")
    return result.stdout


def _tesseract(path: Path, timeout: int, language: str = "eng") -> tuple[str, float | None]:
    executable = shutil.which("tesseract")
    if not executable:
        raise RuntimeError("OCR unavailable: install Tesseract OCR")
    output = _run_text([executable, str(path), "stdout", "-l", language or "eng", "tsv"], timeout=timeout)
    rows = list(csv.DictReader(output.splitlines(), delimiter="\t"))
    words = [row for row in rows if row.get("text", "").strip()]
    text = " ".join(row["text"].strip() for row in words)
    confidences = []
    for row in words:
        try:
            score = float(row.get("conf", "-1"))
            if score >= 0:
                confidences.append(score / 100.0)
        except ValueError:
            pass
    return text, (sum(confidences) / len(confidences) if confidences else None)


def _pdf_text(path: Path, timeout: int, max_pages: int) -> tuple[str, bool]:
    errors = []
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        truncated = len(reader.pages) > max_pages
        return "\n".join(page.extract_text() or "" for page in reader.pages[:max_pages]), truncated
    except Exception as exc:
        errors.append(f"pypdf: {exc}")
    executable = shutil.which("pdftotext")
    if executable:
        try:
            return _run_text([executable, "-layout", "-f", "1", "-l", str(max_pages), str(path), "-"], timeout), False
        except Exception as exc:
            errors.append(f"pdftotext: {exc}")
    raise RuntimeError("PDF text extraction unavailable or failed: " + "; ".join(errors) +
                       ". Install pypdf or Poppler (pdftotext).")


def _ocr_pdf(path: Path, timeout: int, max_pages: int, dpi: int = 250,
             language: str = "eng") -> tuple[str, float | None, bool]:
    renderer = shutil.which("pdftoppm")
    if not renderer:
        raise RuntimeError("Scanned PDF OCR needs Poppler's pdftoppm and Tesseract")
    with tempfile.TemporaryDirectory(prefix="rfq_pdf_ocr_") as directory:
        prefix = str(Path(directory) / "page")
        _run_text([renderer, "-png", "-r", str(dpi), "-l", str(max_pages), str(path), prefix], timeout=timeout)
        pages = sorted(Path(directory).glob("page-*.png"))
        if not pages:
            raise RuntimeError("PDF rasterizer produced no pages")
        results = [_tesseract(page, timeout, language) for page in pages]
    texts = [text for text, _ in results if text]
    scores = [score for _, score in results if score is not None]
    return "\n".join(texts), (sum(scores) / len(scores) if scores else None), len(pages) >= max_pages


def read_pdf(path: Path, minimum_text_chars: int = 30, minimum_text_words: int = 5,
             ocr_threshold: float = 0.65, timeout: int = 180, max_pages: int = 40,
             dpi: int = 250, language: str = "eng") -> DocumentData:
    warnings: list[str] = []
    try:
        text, truncated = _pdf_text(path, timeout, max_pages)
        if truncated:
            warnings.append(f"PDF exceeded the configured {max_pages}-page processing limit.")
    except Exception as exc:
        text = ""
        warnings.append(f"Digital PDF text extraction failed: {exc}")
    useful_chars = len(re.sub(r"\W", "", text, flags=re.UNICODE))
    useful_words = len(re.findall(r"\b[\w'-]{2,}\b", text))
    if useful_chars >= minimum_text_chars and useful_words >= minimum_text_words:
        return DocumentData(text=text)
    warnings.append("PDF text layer was empty or too short; attempted OCR fallback.")
    try:
        text, confidence, truncated = _ocr_pdf(path, timeout, max_pages, dpi, language)
        if truncated:
            warnings.append(f"PDF OCR reached the configured {max_pages}-page processing limit.")
        if not text.strip():
            warnings.append("OCR returned no readable text.")
        if confidence is None:
            warnings.append("OCR could not provide a confidence score.")
        elif confidence < ocr_threshold:
            warnings.append(f"OCR confidence is low ({confidence:.2f}).")
        return DocumentData(text=text, confidence=confidence, warnings=warnings)
    except Exception as exc:
        if text.strip():
            warnings.append(f"OCR fallback unavailable or failed: {exc}; using partial PDF text.")
            return DocumentData(text=text, confidence=0.4, warnings=warnings)
        raise RuntimeError("; ".join(warnings + [f"OCR fallback failed: {exc}"])) from exc


def read_image(path: Path, ocr_threshold: float = 0.65, timeout: int = 90,
               language: str = "eng") -> DocumentData:
    text, confidence = _tesseract(path, timeout, language)
    warnings = []
    if not text.strip():
        warnings.append("OCR returned no readable text.")
    if confidence is None:
        warnings.append("OCR could not provide a confidence score.")
    elif confidence < ocr_threshold:
        warnings.append(f"OCR confidence is low ({confidence:.2f}).")
    return DocumentData(text=text, confidence=confidence, warnings=warnings)


def _email_data(path: Path, max_attachment_bytes: int) -> DocumentData:
    message = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    plain_parts, html_parts, attachments = [], [], []
    warnings = []
    for attachment_number, part in enumerate(message.walk(), 1):
        if part.is_multipart():
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        content_type = part.get_content_type()
        if filename and payload is not None:
            safe_name = Path(filename.replace("\\", "/")).name
            if not Path(safe_name).suffix:
                guessed = mimetypes.guess_extension(content_type)
                if guessed:
                    safe_name += guessed
            if len(payload) > max_attachment_bytes:
                warnings.append(f"Attachment exceeds the configured processing size limit: {safe_name}.")
            else:
                attachments.append(Attachment(safe_name, payload, content_type))
        elif payload is not None and content_type not in {"text/plain", "text/html"}:
            guessed = mimetypes.guess_extension(content_type)
            safe_name = f"attachment-{attachment_number}{guessed or ''}"
            if len(payload) > max_attachment_bytes:
                warnings.append(f"Attachment exceeds the configured processing size limit: {safe_name}.")
            else:
                attachments.append(Attachment(safe_name, payload, content_type))
        elif content_type == "text/plain":
            try:
                plain_parts.append(part.get_content())
            except Exception:
                plain_parts.append((payload or b"").decode("utf-8", errors="replace"))
        elif content_type == "text/html":
            try:
                html_parts.append(part.get_content())
            except Exception:
                html_parts.append((payload or b"").decode("utf-8", errors="replace"))
    plain = "\n".join(plain_parts).strip()
    rich = "\n".join(html_to_text(part) for part in html_parts).strip()
    body = plain or rich
    other = []
    alternate = []
    if plain and rich and re.sub(r"\W", "", plain).lower() != re.sub(r"\W", "", rich).lower():
        alternate.append(rich)
    other.extend([f"Email sender: {message.get('From', '')}", f"Email subject: {message.get('Subject', '')}"])
    return DocumentData(text=body, attachments=attachments, warnings=warnings,
                        other_content=other, alternate_texts=alternate)


def _legacy_office(path: Path, timeout: int) -> DocumentData:
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        raise RuntimeError("Legacy Office file reader unavailable; install LibreOffice or add an adapter")
    output_format = "xlsx" if path.suffix.lower() == ".xls" else "txt:Text"
    with tempfile.TemporaryDirectory(prefix="rfq_office_") as directory:
        _run_text([executable, "--headless", "--convert-to", output_format,
                   "--outdir", directory, str(path)], timeout=timeout)
        converted = Path(directory) / (path.stem + (".xlsx" if output_format == "xlsx" else ".txt"))
        if not converted.exists():
            raise RuntimeError("Office converter did not produce an output file")
        if output_format == "xlsx":
            return DocumentData(tables=read_xlsx(converted), warnings=["Legacy workbook converted through LibreOffice."])
        return DocumentData(text=converted.read_text(encoding="utf-8", errors="replace"))


def _attachment_from_path(path: Path) -> Attachment:
    return Attachment(path.name, path.read_bytes())


def _read_source(path: str | Path, config: dict | None = None) -> DocumentData:
    """Read a source or raise a descriptive error; adapters are independently callable."""
    path = Path(path)
    suffix = path.suffix.lower()
    config = config or {}
    limits = config.get("processing_limits", {})
    ocr = config.get("ocr", {})
    if suffix in {".txt", ".md", ".log"}:
        return DocumentData(text=path.read_text(encoding="utf-8-sig", errors="replace"))
    if suffix == ".eml":
        return _email_data(path, int(limits.get("max_attachment_bytes", 20 * 1024 * 1024)))
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as stream:
            return DocumentData(tables=[(path.stem, list(csv.reader(stream, delimiter=delimiter)))])
    if suffix == ".xlsx":
        return DocumentData(tables=read_xlsx(path))
    if suffix == ".xls":
        return _legacy_office(path, int(limits.get("office_timeout_seconds", 120)))
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, list) and all(isinstance(row, dict) for row in data):
            headers = list(dict.fromkeys(key for row in data for key in row))
            table = [headers] + [[row.get(key, "") for key in headers] for row in data]
            return DocumentData(tables=[(path.stem, table)])
        return DocumentData(text=json.dumps(data, ensure_ascii=False, indent=2))
    if suffix == ".docx":
        return read_docx(path)
    if suffix in {".doc", ".rtf"}:
        return _legacy_office(path, int(limits.get("office_timeout_seconds", 120)))
    if suffix == ".pdf":
        return read_pdf(path, int(ocr.get("minimum_pdf_text_chars", 30)),
                        int(ocr.get("minimum_pdf_text_words", 5)),
                        float(ocr.get("review_confidence_threshold", 0.65)),
                        int(limits.get("pdf_timeout_seconds", 180)),
                        int(limits.get("max_pdf_pages", 40)),
                        int(ocr.get("pdf_dpi", 250)), str(ocr.get("language", "eng")))
    if suffix in {".html", ".htm"}:
        return DocumentData(text=html_to_text(path.read_text(encoding="utf-8-sig", errors="replace")))
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}:
        return read_image(path, float(ocr.get("review_confidence_threshold", 0.65)),
                          int(ocr.get("timeout_seconds", 90)), str(ocr.get("language", "eng")))
    if suffix == ".msg":
        try:
            import extract_msg
        except ImportError as exc:
            raise RuntimeError("Outlook MSG reader unavailable; install extract-msg") from exc
        message = extract_msg.Message(str(path))
        text = message.body or html_to_text(message.htmlBody or "")
        attachments = []
        warnings = []
        for attachment in message.attachments:
            name = Path(attachment.longFilename or attachment.shortFilename or "attachment").name
            data = getattr(attachment, "data", None)
            if data:
                if len(data) > int(limits.get("max_attachment_bytes", 20 * 1024 * 1024)):
                    warnings.append(f"Attachment exceeds the configured processing size limit: {name}.")
                else:
                    attachments.append(Attachment(name, data))
        return DocumentData(text=text, attachments=attachments, warnings=warnings)
    raise RuntimeError(f"Unsupported file format {suffix or '(no extension)'}; add a reader adapter")


def read_source(path: str | Path, config: dict | None = None) -> DocumentData:
    """Public reader boundary with normalized, source-specific errors."""
    source = Path(path)
    try:
        return _read_source(source, config)
    except DocumentReadError:
        raise
    except (OSError, ValueError, KeyError, IndexError, zipfile.BadZipFile,
            ET.ParseError, csv.Error, subprocess.SubprocessError, ImportError) as exc:
        raise DocumentReadError(f"{source.name}: {type(exc).__name__}: {exc}") from exc
    except RuntimeError as exc:
        raise DocumentReadError(f"{source.name}: {exc}") from exc
