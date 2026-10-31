"""Evaluation-only code; this module is the only code that reads answer_key/."""

import json
from pathlib import Path

from app_config import CONFIG, ROOT, THRESHOLD
from evaluation.metrics import evaluate_records

def main():
    gold_documents = [json.loads(path.read_text(encoding="utf-8"))
                      for path in sorted((ROOT / "answer_key").glob("*.json"))]
    predicted_documents = {}
    for gold in gold_documents:
        output_path = ROOT / "outputs" / f"{gold['rfq_id']}.json"
        if output_path.exists():
            predicted_documents[gold["rfq_id"]] = json.loads(output_path.read_text(encoding="utf-8"))
        else:
            predicted_documents[gold["rfq_id"]] = {"lines": []}
    sweep = CONFIG.get("evaluation", {}).get("thresholds", [0.5, 0.65, 0.8, 0.9, 0.95])
    result = evaluate_records(gold_documents, predicted_documents, sweep)
    (ROOT / "evaluation").mkdir(exist_ok=True)
    (ROOT / "evaluation/results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
