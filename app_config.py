"""Load and validate application configuration."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "pipeline_config.json").read_text(encoding="utf-8"))
THRESHOLD = float(CONFIG["review_threshold"])

def validate_config(config):
    """Fail early for unsafe or contradictory processing settings."""
    threshold = float(config.get("review_threshold", 0.8))
    ocr_threshold = float(config.get("ocr", {}).get("review_confidence_threshold", 0.65))
    if not 0 <= threshold <= 1 or not 0 <= ocr_threshold <= 1:
        raise ValueError("review and OCR confidence thresholds must be between 0 and 1")
    limits = config.get("processing_limits", {})
    for name, value in limits.items():
        if int(value) <= 0:
            raise ValueError(f"processing limit {name} must be greater than zero")
    matching = config.get("matching", {})
    for name in ("token_coverage_weight", "token_precision_weight", "sequence_weight"):
        if name in matching and float(matching[name]) < 0:
            raise ValueError(f"matching.{name} cannot be negative")
    if float(matching.get("minimum_description_score", threshold)) > 1:
        raise ValueError("matching.minimum_description_score cannot exceed 1")
    llm = matching.get("llm", {})
    if llm.get("enabled") and (not llm.get("endpoint_url") or not llm.get("model")):
        raise ValueError("matching.llm.endpoint_url and matching.llm.model are required when LLM matching is enabled")
    if not 0 <= float(llm.get("minimum_confidence", 0.75)) <= 1:
        raise ValueError("matching.llm.minimum_confidence must be between 0 and 1")
    if int(llm.get("candidate_limit", 8)) < 1:
        raise ValueError("matching.llm.candidate_limit must be at least 1")

validate_config(CONFIG)
