"""RFQ line processing service: orchestrates readers, extractors, and matching."""
import logging
import re
import tempfile
import time
from pathlib import Path

from app_config import CONFIG, THRESHOLD
from common import norm
from extractors.line_items import parse_number, rows_to_items, text_to_items
from matching.engine import match_item
from models.output import OutputValidationError, validate_output
from readers import DocumentReadError, read_source
from services.customers import customer_for_source

logger = logging.getLogger("rfq_pipeline")

def make_line(index, item, products, by_id, refs, ref_notes, ambiguous_ids,
              description_tokens, inverse_frequency, extraction_confidence=None, warnings=None):
    quantity = parse_number(item.get("quantity"))
    unit = (str(item.get("unit") or "").strip().upper() or None)
    flags = list(item.get("flags", []))
    if quantity is None and "quantity_unparsed" not in flags:
        flags.append("quantity_unparsed")
    if unit and unit not in {str(value).strip().upper() for value in CONFIG.get("unit_aliases", {}).values()} and unit not in {p.get("unit", "").strip().upper() for p in products}:
        flags.append("unit_not_in_catalog")
    if re.search(r"\b(?:same as above|as above|same as previous|previous line|same as line|see item|ditto)\b",
                 item.get("as_written", ""), re.I):
        flags.append("relative_reference_needs_context")
    if "relative_reference_needs_context" in flags:
        candidate, method, confidence, match_flags, note = None, None, 0.0, [], None
    else:
        candidate, method, confidence, match_flags, note = match_item(
            item, products, by_id, refs, ref_notes, ambiguous_ids,
            description_tokens, inverse_frequency)
        flags.extend(match_flags)
    if candidate is None:
        flags.append("unmatched")
    product = by_id.get(candidate) if candidate else None
    if product and unit and product.get("unit") and unit != product["unit"].strip().upper():
        flags.append("unit_mismatch")
    if warnings:
        flags.append("document_reader_warning")
    if extraction_confidence is not None:
        if extraction_confidence < float(CONFIG.get("ocr", {}).get("review_confidence_threshold", 0.65)):
            flags.append("low_ocr_confidence")
        confidence = min(confidence, extraction_confidence)
    needs_review = bool(flags) or confidence < THRESHOLD
    line = {"line": index, "as_written": str(item.get("as_written") or "").strip(),
            "quantity": quantity, "unit": unit, "ridgeline_part_number": candidate,
            "match_method": method, "confidence": round(confidence, 2),
            "needs_review": needs_review, "flags": sorted(set(flags))}
    notes = [str(value).strip() for value in (item.get("note"), note) if value and str(value).strip()]
    notes.extend(str(value).strip() for value in (warnings or []) if str(value).strip())
    if extraction_confidence is not None:
        notes.append(f"Document OCR confidence: {extraction_confidence:.2f}")
    if notes:
        line["note"] = "; ".join(dict.fromkeys(notes))
    return line

def make_review_stub(reason):
    return [{"line": 1, "as_written": "entire RFQ", "quantity": None, "unit": None,
             "ridgeline_part_number": None, "match_method": None, "confidence": 0.0,
             "needs_review": True, "flags": ["stub"], "note": reason}]

def collect_document(document, source_name, products, by_id, refs, ref_notes, units,
                     unit_aliases, depth=0):
    """Convert document text, tables and nested attachments into reviewable lines."""
    items, other = [], list(document.other_content)
    all_text = [document.text] if document.text else []
    text_items, text_other = text_to_items(document.text, units, unit_aliases) if document.text else ([], [])
    items.extend(text_items)
    other.extend(text_other)
    for alternate in document.alternate_texts:
        all_text.append(alternate)
        alternate_items, alternate_other = text_to_items(alternate, units, unit_aliases)
        seen = {norm(item.get("as_written")) for item in items}
        for item in alternate_items:
            if norm(item.get("as_written")) not in seen:
                item.setdefault("flags", []).append("alternate_email_body_needs_review")
                item["note"] = "Extracted from the alternate HTML email body."
                items.append(item)
                seen.add(norm(item.get("as_written")))
        other.extend(alternate_other)
    for table_name, rows in document.tables:
        all_text.extend(" ".join(map(str, row)) for row in rows)
        table_items, table_other = rows_to_items(rows, units, unit_aliases,
                                                 source_name=f"{source_name} / {table_name}")
        items.extend(table_items)
        other.extend(table_other)
    other.extend(document.warnings)
    if document.attachments:
        if depth >= int(CONFIG.get("processing_limits", {}).get("max_attachment_depth", 2)):
            other.append(f"{source_name}: attachment nesting limit reached; {len(document.attachments)} attachment(s) need manual review.")
        else:
            for attachment in document.attachments:
                attachment_path = Path(attachment.filename)
                if attachment_path.suffix.lower() not in CONFIG.get("attachment_extensions", []):
                    other.append(f"Unsupported attachment retained for review: {attachment.filename} ({attachment.content_type}).")
                    continue
                try:
                    with tempfile.TemporaryDirectory(prefix="rfq_attachment_") as temp_dir:
                        local_path = Path(temp_dir) / attachment_path.name
                        local_path.write_bytes(attachment.data)
                        attached = read_source(local_path, CONFIG)
                        attached_items, attached_other, attached_text = collect_document(
                            attached, f"Attachment {attachment.filename}", products, by_id,
                            refs, ref_notes, units, unit_aliases, depth + 1)
                    for item in attached_items:
                        source_note = f"Source: attachment {attachment.filename}"
                        item["note"] = "; ".join(value for value in (source_note, item.get("note")) if value)
                    items.extend(attached_items)
                    other.extend(attached_other)
                    all_text.extend(attached_text)
                except Exception as exc:
                    logger.warning("source=%s stage=attachment filename=%s error_type=%s",
                                   source_name, attachment.filename, type(exc).__name__)
                    other.append(f"Attachment could not be processed: {attachment.filename}: {exc}")
    for item in items:
        item.setdefault("_extraction_confidence", document.confidence)
        item.setdefault("_reader_warnings", document.warnings)
    return items, other, all_text

def process_file(path, products, by_id, refs, ref_notes, ambiguous_ids, description_tokens,
                 inverse_frequency, customers, units, unit_aliases):
    started = time.perf_counter()
    logger.info("source=%s stage=read start", path.name)
    try:
        document = read_source(path, CONFIG)
        logger.info("source=%s stage=read complete tables=%d attachments=%d", path.name,
                    len(document.tables), len(document.attachments))
        items, other, all_text = collect_document(document, path.name, products, by_id,
                                                  refs, ref_notes, units, unit_aliases)
        if not items:
            lines = make_review_stub("No line items could be identified from this file; review the source manually.")
        else:
            lines = [make_line(i, item, products, by_id, refs, ref_notes, ambiguous_ids,
                               description_tokens, inverse_frequency,
                               item.get("_extraction_confidence", document.confidence),
                               item.get("_reader_warnings", document.warnings))
                     for i, item in enumerate(items, 1)]
        for line in lines:
            if line.get("ridgeline_part_number") is None:
                logger.warning("source=%s stage=matching line=%s status=unmatched flags=%s confidence=%.2f",
                               path.name, line.get("line"), ",".join(line.get("flags", [])),
                               float(line.get("confidence", 0)))
        source_text = "\n".join(all_text + other)
        customer, customer_evidence = customer_for_source(source_text, customers)
        if customer is None:
            other.append("Customer could not be identified uniquely; manual customer review is required.")
            for line in lines:
                line["flags"] = sorted(set(line["flags"] + ["customer_unresolved"]))
                line["needs_review"] = True
                if customer_evidence == "ambiguous":
                    line["note"] = "; ".join(value for value in (line.get("note"), "Multiple customers matched the available evidence.") if value)
        customer_fields = CONFIG.get("customer_fields", {})
        name_column = next((column for column in customer_fields.get("name", ("name", "customer_name"))
                            if customer and customer.get(column)), None)
        result = {"rfq_id": path.stem,
                  "customer_id": customer.get(customer_fields.get("id", "customer_id")) if customer else None,
                  "customer_name": customer.get(name_column) if customer and name_column else None,
                  "lines": lines}
        if other:
            result["non_item_rows"] = list(dict.fromkeys(other))
        try:
            validate_output(result)
        except OutputValidationError as exc:
            logger.error("source=%s stage=validation error=%s", path.name, exc)
            result = {"rfq_id": path.stem, "customer_id": None, "customer_name": None,
                      "lines": make_review_stub(f"Output validation failed: {exc}")}
            if other:
                result["non_item_rows"] = list(dict.fromkeys(other))
            validate_output(result)
        review_count = sum(bool(line.get("needs_review")) for line in result["lines"])
        logger.info("source=%s stage=complete lines=%d review_lines=%d duration_seconds=%.3f",
                    path.name, len(result["lines"]), review_count, time.perf_counter() - started)
        return result
    except DocumentReadError as exc:
        logger.warning("source=%s stage=read error_type=%s detail=%s", path.name, type(exc).__name__, exc)
        result = {"rfq_id": path.stem, "customer_id": None, "customer_name": None,
                  "lines": make_review_stub(f"Document reader failed: {exc}")}
        validate_output(result)
        logger.info("source=%s stage=complete failed=true duration_seconds=%.3f",
                    path.name, time.perf_counter() - started)
        return result
    except Exception as exc:
        logger.exception("source=%s stage=processing unexpected_error_type=%s", path.name, type(exc).__name__)
        logger.info("source=%s stage=complete failed=true duration_seconds=%.3f",
                    path.name, time.perf_counter() - started)
        return {"rfq_id": path.stem, "customer_id": None, "customer_name": None,
                "lines": make_review_stub("Unexpected processing error; see application log for details.")}

