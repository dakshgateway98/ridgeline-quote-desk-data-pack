"""Command-line entry point for the format-agnostic RFQ pipeline."""

import csv
import hashlib
import json
import logging
import re
from pathlib import Path

from app_config import CONFIG, ROOT, THRESHOLD
from extractors.line_items import parse_number, parse_quantity_cell, rows_to_items, text_to_items
from matching.engine import catalog_indexes, match_item
from models.output import OUTPUT_FIELDS, OutputValidationError, validate_output
from services.customers import customer_for_source
from services.rfq import (collect_document, make_line, make_review_stub, process_file)

logger = logging.getLogger("rfq_pipeline")

def load_csv(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))

from readers import DocumentReadError, read_source
from app_config import CONFIG, ROOT, THRESHOLD
from common import norm
from extractors.line_items import parse_number, parse_quantity_cell, rows_to_items, text_to_items
from matching.engine import catalog_indexes, match_item, normalize_part
from models.output import OUTPUT_FIELDS, OutputValidationError, validate_output
from services.customers import customer_for_source
from services.rfq import make_line, make_review_stub, collect_document, process_file

logger = logging.getLogger("rfq_pipeline")

def safe_output_names(files):
    """Make deterministic basename outputs and disambiguate collisions safely."""
    result, used = {}, set()
    for path in files:
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._") or "rfq"
        name = stem
        if name.casefold() in used:
            suffix = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:8]
            name = f"{stem}_{suffix}"
        used.add(name.casefold())
        result[path] = name + ".json"
    return result

def run():
    products = load_csv(ROOT / CONFIG["products_file"])
    crossrefs = load_csv(ROOT / CONFIG["cross_reference_file"])
    customers = load_csv(ROOT / CONFIG["customers_file"])
    by_id, refs, ref_notes, ambiguous_ids, description_tokens, inverse_frequency = catalog_indexes(products, crossrefs)
    units = ({p.get("unit", "").strip().upper() for p in products if p.get("unit")} |
             {str(unit).strip().upper() for unit in CONFIG.get("supported_units", []) if str(unit).strip()})
    input_dir, output_dir = ROOT / CONFIG["input_directory"], ROOT / CONFIG["output_directory"]
    output_dir.mkdir(exist_ok=True)
    files = sorted(path for path in input_dir.iterdir() if path.is_file())
    names = safe_output_names(files)
    expected = set(names.values())
    for stale in output_dir.glob("*.json"):
        if stale.name not in expected:
            stale.unlink()
            logger.info("source=%s stage=cleanup removed_stale_output=true", stale.name)
    processed = failed = review_required = 0
    for path in files:
        result = process_file(path, products, by_id, refs, ref_notes, ambiguous_ids,
                              description_tokens, inverse_frequency, customers, units,
                              CONFIG.get("unit_aliases", {}))
        target = output_dir / names[path]
        try:
            temp = target.with_suffix(target.suffix + ".tmp")
            temp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            temp.replace(target)
            validate_output(result)
            processed += 1
            failed += int(any("stub" in line.get("flags", []) for line in result["lines"]))
            review_required += int(any(line.get("needs_review") for line in result["lines"]))
        except (OSError, OutputValidationError) as exc:
            logger.exception("source=%s stage=write error_type=%s", path.name, type(exc).__name__)
            failed += 1
    logger.info("stage=batch complete total=%d processed=%d failed=%d review_required=%d threshold=%.2f",
                len(files), processed, failed, review_required, THRESHOLD)
    print(f"Wrote {processed}/{len(files)} RFQ drafts to outputs/; failed={failed}, review_required={review_required}.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
