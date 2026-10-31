"""Convert structured rows and free text into normalized line item records."""
import math
import re
from app_config import CONFIG
from common import norm

def parse_number(value):
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return int(number) if number.is_integer() else number

def parse_quantity_cell(value, unit_value, catalog_units, unit_aliases):
    """Read numeric cells and common quantity-plus-unit cells without guessing."""
    quantity = parse_number(value)
    embedded_unit = None
    if quantity is None:
        unit_lookup = {norm(unit): str(unit).strip().upper() for unit in catalog_units if str(unit).strip()}
        alias_lookup = {norm(alias): unit_lookup.get(norm(target), str(target).upper())
                        for alias, target in unit_aliases.items()}
        unit_words = set(unit_lookup) | set(alias_lookup)
        unit_pattern = "(?:" + "|".join(re.escape(unit) for unit in sorted(unit_words, key=len, reverse=True)) + ")" if unit_words else r"[A-Za-z]+"
        match = re.fullmatch(r"\s*(?:(?:qty|quantity)\s*[:=]?\s*)?(\d+(?:\.\d+)?)\s*(" + unit_pattern + r")?\s*", str(value), re.I)
        if match:
            quantity = parse_number(match.group(1))
            if match.group(2):
                word = norm(match.group(2))
                embedded_unit = alias_lookup.get(word, unit_lookup.get(word, match.group(2).upper()))
    raw_unit = str(unit_value or "").strip()
    lookup = {norm(unit): str(unit).strip().upper() for unit in catalog_units if str(unit).strip()}
    aliases = {norm(alias): lookup.get(norm(target), str(target).upper()) for alias, target in unit_aliases.items()}
    column_unit = aliases.get(norm(raw_unit), lookup.get(norm(raw_unit), raw_unit.upper() or None))
    conflict = bool(embedded_unit and column_unit and embedded_unit != column_unit)
    return quantity, column_unit or embedded_unit, conflict

def rows_to_items(rows, catalog_units, unit_aliases=None, source_name=None):
    unit_aliases = {key.lower(): value.upper() for key, value in (unit_aliases or {}).items()}
    if not rows:
        return [], []
    width = max(map(len, rows))
    rows = [list(map(str, row)) + [""] * (width - len(row)) for row in rows]
    # Find a likely header row by its field labels instead of assuming column positions.
    header_index, header = None, None
    for idx, row in enumerate(rows[:10]):
        labels = [norm(value) for value in row]
        has_qty = any(value in {"qty", "quantity", "order quantity", "requested quantity", "quantity requested", "qty requested", "requested qty"} for value in labels)
        has_desc = any(value in {"description", "desc", "item", "product", "details", "material",
                                 "item description", "product description", "material description"} for value in labels)
        if has_qty and has_desc:
            header_index, header = idx, row
            break
    if header is None:
        return [], [f"{source_name + ': ' if source_name else ''}Could not identify quantity and item columns; table contents: " +
                    " || ".join(" | ".join(cell for cell in row if cell.strip()) for row in rows if any(cell.strip() for cell in row))]
    headers = [norm(value) for value in header]
    field_aliases = {
        "description": {"description", "desc", "item", "product", "details", "material", "item description", "product description", "material description", "item details"},
        "quantity": {"quantity", "qty", "order quantity", "requested quantity", "quantity requested", "qty requested", "requested qty"},
        "unit": {"unit", "uom", "unit of measure", "measure"},
        "part": {"part", "part number", "part no", "part #", "sku", "item number", "item #", "item id", "product code", "supplier part number", "supplier part"},
        "notes": {"note", "notes", "comments", "comment", "remarks", "additional notes", "customer note"},
    }
    positions = {field: [i for i, value in enumerate(headers) if value in aliases]
                 for field, aliases in field_aliases.items()}

    def cell_value(row, field):
        vals = [row[i].strip() for i in positions[field] if i < len(row) and row[i].strip()]
        return (vals[0] if vals else ""), (len(set(vals)) > 1)

    items, other = [], []
    for preamble in rows[:header_index + 1]:
        if any(cell.strip() for cell in preamble):
            other.append(f"{source_name + ': ' if source_name else ''}" + " | ".join(cell for cell in preamble if cell.strip()))
    for row in rows[header_index + 1:]:
        if not any(cell.strip() for cell in row):
            continue
        desc, desc_conflict = cell_value(row, "description")
        part, part_conflict = cell_value(row, "part")
        qty_text, qty_conflict = cell_value(row, "quantity")
        unit_text, unit_conflict = cell_value(row, "unit")
        qty, inferred_unit, unit_conflict_from_quantity = parse_quantity_cell(
            qty_text, unit_text, catalog_units, unit_aliases)
        raw_unit = unit_text.strip()
        unit_lookup = {norm(unit): str(unit).strip().upper() for unit in catalog_units if str(unit).strip()}
        alias_lookup = {norm(alias): unit_lookup.get(norm(target), str(target).upper())
                        for alias, target in unit_aliases.items()}
        normalized_unit = inferred_unit
        note_text, note_conflict = cell_value(row, "notes")
        consumed = set(index for field in ("description", "part", "quantity", "unit", "notes")
                       for index in positions[field])
        extra_fields = [(str(header[i]).strip(), row[i].strip()) for i in range(min(len(header), len(row)))
                        if i not in consumed and str(header[i]).strip() and row[i].strip()]
        specs = "; ".join(f"{name}: {value}" for name, value in extra_fields)
        if specs:
            desc = "; ".join(value for value in (desc, specs) if value)
        if qty is None and part and items and part == items[-1].get("customer_part_number") and desc:
            previous = items[-1]
            previous["description"] = (previous.get("description", "") + "\n" + desc).strip()
            previous["as_written"] = (previous.get("as_written", "") + "\n" + desc).strip()
            previous.setdefault("flags", []).append("multirow_description")
            continue
        if not desc and not part:
            other.append(f"{source_name + ': ' if source_name else ''}" + " | ".join(cell for cell in row if cell.strip()))
            continue
        if qty is None and not part:
            words = len(norm(desc).split())
            max_continuation = int(CONFIG.get("processing_limits", {}).get("continuation_max_tokens", 8))
            if note_text and not desc:
                other.append(f"{source_name + ': ' if source_name else ''}" + " | ".join(cell for cell in row if cell.strip()))
                continue
            if desc and items and words <= max_continuation:
                items[-1]["description"] = (items[-1].get("description", "") + "\n" + desc).strip()
                items[-1]["as_written"] = (items[-1].get("as_written", "") + "\n" + desc).strip()
                items[-1].setdefault("flags", []).append("multirow_description")
                if note_text:
                    items[-1]["note"] = "; ".join(value for value in (items[-1].get("note"), note_text) if value)
                continue
            if not desc or words > max_continuation:
                other.append(f"{source_name + ': ' if source_name else ''}" + " | ".join(cell for cell in row if cell.strip()))
                continue
        written = " ".join(value for value in (part, desc) if value).strip()
        flags = [] if qty is not None else ["quantity_unparsed"]
        if any((desc_conflict, part_conflict, qty_conflict, unit_conflict, note_conflict, unit_conflict_from_quantity)):
            flags.append("conflicting_duplicate_columns")
        if raw_unit and normalized_unit is None:
            flags.append("unit_not_in_catalog")
        items.append({"as_written": written, "description": desc or part, "quantity": qty,
                      "unit": normalized_unit or (raw_unit.upper() or None),
                      "customer_part_number": part or None,
                      "flags": flags,
                      "note": "; ".join(value for value in (
                          f"Worksheet/table: {source_name}" if source_name else None,
                          f"Customer note: {note_text}" if note_text else None) if value) or None})
    return items, other

def text_to_items(text, catalog_units, unit_aliases):
    # Drop standard email transport headers so dates, phone numbers, and addresses
    # are not mistaken for requested products.
    lines = text.splitlines()
    first_block = []
    for raw in lines:
        if not raw.strip():
            break
        first_block.append(raw)
    header_pattern = re.compile(r"^\s*(from|to|date|sent|subject|cc|reply-to)\s*:", re.I)
    header_lines = sum(bool(header_pattern.match(line)) for line in first_block)
    other = list(first_block) if header_lines >= 2 else []
    if header_lines >= 2:
        text = "\n".join(lines[len(first_block) + 1:])
    items = []
    thread_headers = sum(bool(re.match(r"^\s*(from|to|date|sent|subject)\s*:", line, re.I))
                         for line in text.splitlines())
    thread_context = bool(re.search(r"forwarded message|original message|^-{2,}\s*(?:forwarded|original)", text, re.I | re.M)) or thread_headers >= 2
    alias_lookup = {key.lower(): value.upper() for key, value in unit_aliases.items()}
    unit_alternatives = sorted({re.escape(str(unit)) for unit in catalog_units if unit}
                               | {re.escape(str(unit)) for unit in alias_lookup}, key=len, reverse=True)
    unit_re = "(?:" + "|".join(unit_alternatives) + ")" if unit_alternatives else r"[A-Za-z]{1,8}"
    signature = False
    for raw in text.splitlines():
        if signature:
            if raw.strip():
                other.append(raw.strip())
            continue
        if raw.strip() == "--":
            signature = True
            other.append(raw.strip())
            continue
        quoted = raw.lstrip().startswith(">")
        indented = bool(raw[:1].isspace())
        unquoted = raw.lstrip()[1:].strip() if quoted else raw
        source_line = re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", unquoted).strip()
        if not source_line:
            continue
        if len(source_line) < 3:
            other.append(source_line)
            continue
        if header_pattern.match(source_line):
            other.append(source_line)
            continue
        if re.fullmatch(r"[\s+().-]*\d[\d\s+().-]{5,}", source_line):
            other.append(source_line)
            continue
        if re.match(r"^\s*-{2,}.*(?:forwarded|original).*?-{2,}\s*$", source_line, re.I):
            other.append(source_line)
            continue
        qty, unit, quantity_span = None, None, None
        # Prefer explicit quantity labels, then number + known catalog unit, then a leading count.
        match = re.search(r"\b(?:qty|quantity)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(" + unit_re + r")?\b", source_line, re.I)
        if not match:
            match = re.search(r"\b(\d+(?:\.\d+)?)\s*(" + unit_re + r")\b", source_line, re.I)
        if match:
            qty = parse_number(match.group(1))
            unit = alias_lookup.get(match.group(2).lower(), match.group(2).upper()) if match.group(2) else None
            quantity_span = match.span()
        else:
            leading = re.match(r"^(\d+)\s+(.+)$", source_line)
            trailing = re.search(r"\b(?:x|×)\s*(\d+)\s*$", source_line, re.I)
            if leading:
                qty = parse_number(leading.group(1))
                quantity_span = (0, len(leading.group(1)))
            elif trailing:
                qty = parse_number(trailing.group(1))
                quantity_span = trailing.span()
        # A line without a quantity can still be a requested item; keep it for review.
        looks_like_item = bool(re.match(r"^\s*[-*•]+", raw) or qty is not None or
                               (re.search(r"\d", source_line) and len(norm(source_line).split()) <= 8))
        note_match = re.match(r"^(?:additional\s+)?(?:notes?|specifications?)\s*:\s*(.+)$", source_line, re.I)
        if note_match and items:
            items[-1]["note"] = "; ".join(value for value in (items[-1].get("note"), note_match.group(1).strip()) if value)
            items[-1].setdefault("flags", []).append("additional_notes_attached")
            continue
        if qty is None and not re.match(r"^\s*[-*•]+", raw) and indented and items and len(norm(source_line).split()) <= int(CONFIG.get("processing_limits", {}).get("continuation_max_tokens", 8)):
            items[-1]["description"] = (items[-1].get("description", "") + "\n" + source_line).strip()
            items[-1]["as_written"] = (items[-1].get("as_written", "") + "\n" + source_line).strip()
            items[-1].setdefault("flags", []).append("multi_line_description")
            continue
        if qty is not None or looks_like_item:
            flags = [] if qty is not None else ["quantity_unparsed"]
            if quoted or thread_context:
                flags.append("quoted_email_context_needs_review")
            description = source_line
            if quantity_span:
                description = (source_line[:quantity_span[0]] + " " + source_line[quantity_span[1]:]).strip()
                description = re.sub(r"^[\s,;:=-]+|[\s,;:=-]+$", "", description)
                if description.endswith(","):
                    description = description[:-1].strip()
            items.append({"as_written": source_line, "description": description or source_line,
                          "quantity": qty, "unit": unit, "flags": flags})
        else:
            other.append(source_line)
    return items, other

