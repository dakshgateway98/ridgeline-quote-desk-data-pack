"""Optional OpenAI-compatible chat-completions fallback for product matching."""
import json
import os
from urllib.error import URLError
from urllib.request import Request, urlopen
from common import norm


def llm_candidate(item, products, config):
    """Return (part, confidence, note) or raise an actionable provider error.

    The provider receives only the current RFQ line and a bounded candidate list.
    Its confidence is an evidence score and every LLM match is review-required.
    """
    endpoint = (config.get("endpoint_url") or "").rstrip("/")
    model = config.get("model")
    key_name = config.get("api_key_env", "LLM_API_KEY")
    api_key = os.environ.get(key_name)
    if not endpoint or not model:
        raise ValueError("matching.llm.endpoint_url and matching.llm.model must be configured")
    if not api_key:
        raise ValueError(f"LLM fallback is enabled but environment variable {key_name} is not set")

    limit = max(1, int(config.get("candidate_limit", 8)))
    query_tokens = set(norm(item.get("description") or item.get("as_written", "")).split())
    active = [p for p in products if (p.get("status") or "").strip().lower() in {"", "active"}]
    ranked = sorted(active, key=lambda p: (
        len(query_tokens.intersection(norm(p.get("description", "")).split())),
        str(p.get("part_number", ""))), reverse=True)
    candidates = [{"part_number": p.get("part_number"), "description": p.get("description"),
                   "unit": p.get("unit"), "status": p.get("status")}
                  for p in ranked[:limit]]
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": (
                "Match the requested item to one candidate only when the evidence supports it. "
                "Never invent part numbers. Return JSON with part_number (candidate value or null), "
                "confidence (number from 0 to 1), and reason (short string).")},
            {"role": "user", "content": json.dumps({
                "requested_item": item.get("as_written", ""),
                "description": item.get("description", ""),
                "candidates": candidates,
            }, ensure_ascii=False)},
        ],
    }
    request = Request(endpoint + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=float(config.get("timeout_seconds", 20))) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"LLM fallback request failed: {exc}") from exc
    try:
        content = body["choices"][0]["message"]["content"]
        result = json.loads(content)
        part = result.get("part_number")
        confidence = float(result.get("confidence", 0))
        reason = str(result.get("reason", "LLM candidate suggestion"))[:400]
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("LLM fallback returned an invalid response schema") from exc
    allowed = {str(candidate["part_number"]) for candidate in candidates if candidate.get("part_number")}
    if part is not None and part not in allowed:
        raise RuntimeError("LLM fallback returned a part number outside the supplied candidate list")
    if not 0 <= confidence <= 1:
        raise RuntimeError("LLM fallback confidence must be between 0 and 1")
    return part, confidence, reason
