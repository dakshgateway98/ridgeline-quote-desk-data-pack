"""Compute extraction and matching metrics from supplied records."""
from app_config import CONFIG, THRESHOLD

def evaluate_records(gold_documents, predicted_documents, thresholds):
    totals = {"lines": 0, "extraction_correct": 0, "known_matches": 0,
              "match_correct": 0, "predicted_matches": 0, "false_positives": 0,
              "false_negatives": 0, "customers": 0, "customer_correct": 0,
              "review_lines": 0}
    pairs = []
    for gold in gold_documents:
        pred = predicted_documents.get(gold["rfq_id"], {})
        predicted_by_line = {line.get("line"): line for line in pred.get("lines", [])}
        if gold.get("customer_id") is not None:
            totals["customers"] += 1
            totals["customer_correct"] += int(pred.get("customer_id") == gold["customer_id"])
        for expected in gold.get("lines", []):
            actual = predicted_by_line.get(expected.get("line"), {})
            totals["lines"] += 1
            exact_extract = (actual.get("as_written") == expected.get("as_written") and
                             actual.get("quantity") == expected.get("quantity") and
                             actual.get("unit") == expected.get("unit"))
            totals["extraction_correct"] += int(exact_extract)
            truth = expected.get("expected_part_number")
            guess = actual.get("ridgeline_part_number")
            if guess is not None:
                totals["predicted_matches"] += 1
            if truth is not None:
                totals["known_matches"] += 1
                if guess == truth:
                    totals["match_correct"] += 1
                else:
                    totals["false_negatives"] += 1
                    if guess is not None:
                        totals["false_positives"] += 1
            elif guess is not None:
                totals["false_positives"] += 1
            totals["review_lines"] += int(bool(actual.get("needs_review", True)))
            pairs.append((truth, guess, actual))

    curve = []
    for threshold in thresholds:
        accepted = [(truth, guess) for truth, guess, row in pairs
                    if guess is not None and row.get("confidence", 0) >= threshold and not row.get("flags")]
        correct = sum(truth is not None and guess == truth for truth, guess in accepted)
        precision = correct / len(accepted) if accepted else None
        covered_truth = sum(truth is not None and guess == truth for truth, guess in accepted)
        recall = covered_truth / totals["known_matches"] if totals["known_matches"] else 0.0
        curve.append({"threshold": threshold, "auto_accepted": len(accepted),
                      "precision": round(precision, 4) if precision is not None else None,
                      "recall": round(recall, 4)})
    target = float(CONFIG.get("evaluation", {}).get("target_auto_precision", 0.95))
    viable = [row for row in curve if row["precision"] is not None and row["precision"] >= target]
    recommendation = min(viable, key=lambda row: row["threshold"]) if viable else None
    denominator = totals["lines"] or 1
    result = {
        "threshold": THRESHOLD,
        "extraction_accuracy": round(totals["extraction_correct"] / denominator, 4),
        "match_precision": round(totals["match_correct"] / totals["predicted_matches"], 4)
        if totals["predicted_matches"] else 0.0,
        "match_recall": round(totals["match_correct"] / totals["known_matches"], 4)
        if totals["known_matches"] else 0.0,
        "additional_metrics": {
            "extraction_exact_line_count": f"{totals['extraction_correct']}/{totals['lines']}",
            "correct_matches": f"{totals['match_correct']}/{totals['known_matches']}",
            "customer_identification_rate": round(totals["customer_correct"] / totals["customers"], 4)
            if totals["customers"] else None,
            "customer_identification_count": f"{totals['customer_correct']}/{totals['customers']}",
            "review_rate": round(totals["review_lines"] / denominator, 4),
            "false_positive_matches": totals["false_positives"],
            "false_negative_matches": totals["false_negatives"],
            "threshold_sweep": curve,
            "recommended_threshold": recommendation,
            "target_auto_precision": target,
            "answer_key_rfqs": len(gold_documents),
            "notes": "Metrics use only answer-key RFQs. Confidence values are evidence scores, not calibrated probabilities. Threshold results are exploratory on a small sample; use a larger held-out set before production.",
        },
    }
    return result

