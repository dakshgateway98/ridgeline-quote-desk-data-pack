"""Small shared helpers with no pipeline-specific behavior."""
import re

def norm(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()

