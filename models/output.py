"""Dependency-free validation for the public RFQ JSON contract."""
import math
from app_config import THRESHOLD

OUTPUT_FIELDS = (
    "line", "as_written", "quantity", "unit", "ridgeline_part_number",
    "match_method", "confidence", "needs_review", "flags",
)

class OutputValidationError(ValueError):
    pass


def validate_output(result):
    """Validate the assignment's JSON contract without silently filling missing data."""
    errors = []
    required_top = {"rfq_id", "customer_id", "lines"}
    if not isinstance(result, dict):
        raise OutputValidationError("RFQ output must be a JSON object")
    missing = required_top - set(result)
    if missing:
        errors.append("missing top-level fields: " + ", ".join(sorted(missing)))
    if not isinstance(result.get("rfq_id"), str) or not result.get("rfq_id"):
        errors.append("rfq_id must be a non-empty string")
    if result.get("customer_id") is not None and not isinstance(result.get("customer_id"), str):
        errors.append("customer_id must be a string or null")
    if result.get("customer_name") is not None and not isinstance(result.get("customer_name"), str):
        errors.append("customer_name must be a string or null")
    if "non_item_rows" in result and (not isinstance(result["non_item_rows"], list) or
                                       not all(isinstance(row, str) for row in result["non_item_rows"])):
        errors.append("non_item_rows must be an array of strings")
    lines = result.get("lines")
    if not isinstance(lines, list) or not lines:
        errors.append("lines must be a non-empty array")
        lines = []
    required_line = set(OUTPUT_FIELDS)
    methods = {"ridgeline_part_number", "cross_reference", "description", "llm", None}
    for position, line in enumerate(lines, 1):
        prefix = f"lines[{position}]"
        if not isinstance(line, dict):
            errors.append(f"{prefix} must be an object")
            continue
        absent = required_line - set(line)
        if absent:
            errors.append(f"{prefix} missing fields: {', '.join(sorted(absent))}")
        if not isinstance(line.get("line"), int) or isinstance(line.get("line"), bool) or line.get("line") < 1:
            errors.append(f"{prefix}.line must be a positive integer")
        if not isinstance(line.get("as_written"), str) or not line.get("as_written").strip():
            errors.append(f"{prefix}.as_written must be a non-empty string")
        quantity = line.get("quantity")
        if quantity is not None and (isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or
                                     not math.isfinite(quantity) or quantity <= 0):
            errors.append(f"{prefix}.quantity must be a positive number or null")
        if line.get("unit") is not None and not isinstance(line.get("unit"), str):
            errors.append(f"{prefix}.unit must be a string or null")
        part = line.get("ridgeline_part_number")
        if part is not None and not isinstance(part, str):
            errors.append(f"{prefix}.ridgeline_part_number must be a string or null")
        if line.get("match_method") not in methods:
            errors.append(f"{prefix}.match_method is not supported")
        if line.get("ridgeline_part_number") is not None and line.get("match_method") is None:
            errors.append(f"{prefix} has a matched part but no match method")
        confidence = line.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            errors.append(f"{prefix}.confidence must be between 0 and 1")
        if not isinstance(line.get("needs_review"), bool):
            errors.append(f"{prefix}.needs_review must be boolean")
        if not isinstance(line.get("flags"), list) or not all(isinstance(flag, str) for flag in line.get("flags", [])):
            errors.append(f"{prefix}.flags must be an array of strings")
        if line.get("ridgeline_part_number") is None and line.get("needs_review") is False:
            errors.append(f"{prefix} has no matched product but is not marked for review")
        if line.get("flags") and line.get("needs_review") is False:
            errors.append(f"{prefix} has review flags but is not marked for review")
        if isinstance(confidence, (int, float)) and confidence < THRESHOLD and line.get("needs_review") is False:
            errors.append(f"{prefix} is below review threshold but is not marked for review")
    if errors:
        raise OutputValidationError("; ".join(errors))
    return result

