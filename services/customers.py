"""Customer identification from exact contact and name evidence."""
import re
from app_config import CONFIG
from common import norm

def customer_for_source(text, customers):
    """Return a unique customer using exact contact, identifier, then name evidence."""
    addresses = {value.lower() for value in re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)}
    domains = {address.rsplit("@", 1)[1] for address in addresses}

    def unique(matches, evidence):
        id_column = CONFIG.get("customer_fields", {}).get("id", "customer_id")
        found = {row.get(id_column): row for row in matches if row.get(id_column)}
        if len(found) == 1:
            return next(iter(found.values())), evidence
        if len(found) > 1:
            return None, "ambiguous"
        return None, None

    email_columns = ("email", "email_address", "contact_email", "primary_email")
    domain_columns = ("email_domain", "domain", "customer_domain")
    exact_matches = []
    for row in customers:
        known = {str(row.get(column, "")).strip().lower()
                 for column in CONFIG.get("customer_fields", {}).get("email", email_columns) if row.get(column)}
        if addresses.intersection(known):
            exact_matches.append(row)
    match, evidence = unique(exact_matches, "email")
    if match or evidence == "ambiguous":
        return match, evidence

    domain_matches = []
    for row in customers:
        known_domains = {str(row.get(column, "")).strip().lower().lstrip("@")
                         for column in CONFIG.get("customer_fields", {}).get("domain", domain_columns) if row.get(column)}
        known_domains.update(str(row.get(column, "")).strip().lower().rsplit("@", 1)[-1]
                             for column in CONFIG.get("customer_fields", {}).get("email", email_columns)
                             if row.get(column) and "@" in str(row[column]))
        if domains.intersection(known_domains):
            domain_matches.append(row)
    match, evidence = unique(domain_matches, "email_domain")
    if match or evidence == "ambiguous":
        return match, evidence

    identifiers = []
    for row in customers:
        identifier = str(row.get(CONFIG.get("customer_fields", {}).get("id", "customer_id"), "")).strip()
        if identifier and re.search(r"(?<![A-Za-z0-9])" + re.escape(identifier) + r"(?![A-Za-z0-9])", text, re.I):
            identifiers.append(row)
    match, evidence = unique(identifiers, "customer_id")
    if match or evidence == "ambiguous":
        return match, evidence

    body = " " + norm(text) + " "
    name_columns = CONFIG.get("customer_fields", {}).get("name", ("name", "customer_name"))
    name_matches = [row for row in customers if any(norm(row.get(column)) and
                    (" " + norm(row[column]) + " ") in body for column in name_columns)]
    match, evidence = unique(name_matches, "customer_name")
    return (match, evidence) if match else (None, evidence or "unresolved")

