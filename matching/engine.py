"""Exact, cross-reference, and weighted description matching."""
from difflib import SequenceMatcher
import math
import re
from app_config import CONFIG, THRESHOLD
from common import norm
from matching.llm import llm_candidate

def normalize_part(value):
    """Normalize separators and case without performing substring matching."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

def product_tokens(value, ignored):
    words = norm(value).split()
    return {word[:-1] if len(word) > 4 and word.endswith("s") else word
            for word in words if word not in ignored}

def catalog_indexes(products, crossrefs):
    matching = CONFIG.get("matching", {})
    ignored = set(matching.get("common_tokens", []))
    by_id, refs, ref_notes, ambiguous_ids = {}, {}, {}, set()
    description_tokens, document_frequency = {}, {}
    for product in products:
        raw_part = (product.get("part_number") or "").strip()
        part = normalize_part(raw_part)
        if part:
            if part in by_id and by_id[part].get("part_number") != raw_part:
                by_id.pop(part)
                ambiguous_ids.add(part)
            elif part not in ambiguous_ids:
                by_id[part] = product
            tokens = product_tokens(product.get("description", ""), ignored)
            description_tokens[part] = tokens
            for token in tokens:
                document_frequency[token] = document_frequency.get(token, 0) + 1
    for row in crossrefs:
        source = normalize_part(row.get("competitor_part_number"))
        target = normalize_part(row.get("ridgeline_part_number"))
        if source and target:
            refs.setdefault(source, set()).add(target)
            note = (row.get("notes") or "").strip()
            if note:
                ref_notes.setdefault(source, set()).add(note)
    count = max(len(products), 1)
    inverse_frequency = {token: math.log((count + 1) / (frequency + 1)) + 1
                         for token, frequency in document_frequency.items()}
    attributes = set(matching.get("attributes", []))
    multiplier = float(matching.get("attribute_weight_multiplier", 1.0))
    for token in attributes.intersection(inverse_frequency):
        inverse_frequency[token] *= multiplier
    return by_id, refs, ref_notes, ambiguous_ids, description_tokens, inverse_frequency

def match_item(item, products, by_id, refs, ref_notes, ambiguous_ids,
               description_tokens, inverse_frequency):
    config = CONFIG.get("matching", {})
    llm_config = config.get("llm", {})
    evidence = config.get("evidence_scores", {})
    text = item.get("as_written", "")
    tokens = {normalize_part(token) for token in re.findall(r"[A-Z0-9][A-Z0-9._/-]*", text.upper())}
    tokens.discard("")
    known = tokens.intersection(by_id)
    flags, note = [], None
    candidate, method, score = None, None, 0.0

    # 1. Exact Ridgeline part number.
    if tokens.intersection(ambiguous_ids):
        flags.append("ambiguous_catalog_part_number")
        note = "Part number normalization maps to multiple catalogue entries."
    elif len(known) == 1:
        candidate = next(iter(known))
        method, score = "ridgeline_part_number", float(evidence.get("exact_part", 1.0))
        note = "Exact catalogue part-number evidence. The score is an evidence rating, not a calibrated probability."
    elif len(known) > 1:
        flags.append("multiple_ridgeline_parts_in_line")
        note = "The line contains more than one catalogue part number."

    # 2. Exact customer/competitor part number from the cross-reference data.
    if candidate is None and not flags:
        targets = set().union(*(refs.get(token, set()) for token in tokens)) if tokens else set()
        if len(targets) == 1:
            target = next(iter(targets))
            if target not in by_id:
                flags.append("invalid_cross_reference")
                note = "The cross-reference points to a part missing from the product catalogue."
            else:
                candidate, method = target, "cross_reference"
                score = float(evidence.get("cross_reference", 0.98))
                source_notes = set().union(*(ref_notes.get(token, set()) for token in tokens))
                if source_notes:
                    flags.append("cross_reference_note")
                    note = "Cross-reference note: " + "; ".join(sorted(source_notes))
                else:
                    note = "Exact cross-reference evidence. The score is an evidence rating, not a calibrated probability."
        elif len(targets) > 1:
            flags.append("ambiguous_cross_reference")
            note = "The supplied part number maps to multiple catalogue products."

    # 3. Exact normalized description, then 4. weighted token/fuzzy scoring.
    if candidate is None and not flags:
        query_text = item.get("description") or text
        query_tokens = product_tokens(query_text, set(config.get("common_tokens", [])))
        exact = [part for part, tokens_for_product in description_tokens.items()
                 if query_tokens and query_tokens == tokens_for_product and part in by_id and
                 (by_id[part].get("status") or "").strip().lower() in {"", "active"}]
        if len(exact) == 1:
            candidate, method = exact[0], "description"
            score = float(evidence.get("exact_description", 0.96))
            note = "Normalized product-description evidence. The score is an evidence rating, not a calibrated probability."
        elif len(exact) > 1:
            flags.append("ambiguous_description_match")
            note = "Several products have the same normalized description."
        else:
            ranked = []
            for part, product in by_id.items():
                if (product.get("status") or "").strip().lower() not in {"", "active"}:
                    continue
                product_set = description_tokens.get(part, set())
                if not query_tokens or not product_set:
                    continue
                shared = query_tokens & product_set
                weights = inverse_frequency
                query_weight = sum(weights.get(token, 1.0) for token in query_tokens)
                product_weight = sum(weights.get(token, 1.0) for token in product_set)
                shared_weight = sum(weights.get(token, 1.0) for token in shared)
                coverage = shared_weight / query_weight if query_weight else 0.0
                precision = shared_weight / product_weight if product_weight else 0.0
                token_score = (float(config.get("token_coverage_weight", 0.65)) * coverage +
                               float(config.get("token_precision_weight", 0.35)) * precision)
                fuzzy_score = SequenceMatcher(None, " ".join(sorted(query_tokens)),
                                              " ".join(sorted(product_set))).ratio()
                sequence_weight = float(config.get("sequence_weight", 0.1))
                combined = (1 - sequence_weight) * token_score + sequence_weight * fuzzy_score
                ranked.append((combined, part))
            ranked.sort(reverse=True)
            if ranked:
                top_score, top_part = ranked[0]
                second_score = ranked[1][0] if len(ranked) > 1 else 0.0
                minimum = float(config.get("minimum_description_score", THRESHOLD))
                margin = float(config.get("ambiguous_score_margin", 0.06))
                score = max(0.0, min(1.0, top_score))
                if top_score >= minimum and second_score >= minimum and top_score - second_score < margin:
                    flags.append("ambiguous_description_match")
                    note = f"Top description candidates {top_part} and {ranked[1][1]} have similar evidence scores."
                elif top_score >= minimum:
                    candidate, method = top_part, "description"
                    note = f"Weighted token/fuzzy evidence score: {score:.2f}; this is not a calibrated probability."
                elif top_score > 0:
                    flags.append("low_match_confidence")
                    note = f"Best description evidence score {score:.2f} is below {minimum:.2f}."

    # 5. Optional model fallback, only after deterministic matching abstains.
    # Ambiguous or invalid part-number evidence remains a human-review case.
    should_try_llm = (candidate is None and llm_config.get("enabled") and
                      not (set(flags) - {"low_match_confidence"}))
    if should_try_llm:
        try:
            suggested, llm_score, reason = llm_candidate(item, products, llm_config)
            minimum_llm = float(llm_config.get("minimum_confidence", 0.75))
            if suggested and llm_score >= minimum_llm:
                candidate, method, score = normalize_part(suggested), "llm", llm_score
                flags = [flag for flag in flags if flag != "low_match_confidence"]
                flags.append("llm_match_needs_review")
                note = f"LLM suggested {suggested}: {reason}. Score is uncalibrated and requires human review."
            else:
                flags.append("llm_no_confident_match")
                note = f"LLM fallback did not return a candidate above {minimum_llm:.2f}."
        except Exception as exc:
            flags.append("llm_fallback_failed")
            note = str(exc)

    if candidate:
        product = by_id.get(candidate)
        status = (product.get("status") or "").strip().lower() if product else ""
        if status not in {"", "active"}:
            replacement = normalize_part(product.get("replaced_by"))
            if replacement in by_id:
                candidate = replacement
                flags.append("discontinued_replaced")
                note = "Original product is discontinued; its listed replacement is suggested for human review."
            else:
                flags.append("discontinued")
                note = f"Product status is {status or 'not specified as active'}; human review is required."
    output_candidate = by_id[candidate].get("part_number") if candidate in by_id else None
    return output_candidate, method, score, flags, note

