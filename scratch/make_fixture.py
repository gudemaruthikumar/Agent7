import csv
import json
import os
import hashlib

def parse_threshold(threshold_str, metric_name):
    if "0" in threshold_str and "100%" not in threshold_str and "≥" not in threshold_str:
        return {"value": 0.0, "comparison": "lte"}
    elif "100%" in threshold_str or "1" in threshold_str:
        return {"value": 1.0, "comparison": "gte"}
    elif "≥" in threshold_str:
        val = float(threshold_str.split("≥")[1].split(",")[0].strip())
        return {"value": val, "comparison": "gte"}
    elif "≤" in threshold_str:
        # LAT-01
        return {"value": 2.0, "comparison": "lte"}
    else:
        return {"value": 0.95, "comparison": "gte"}

metrics = {}
risks = {}
impacts = {
    "TD-06": {"risk_ids": [], "metric_ids": [], "test_ids": ["REW-S1"]},
    "TD-11": {"risk_ids": [], "metric_ids": [], "test_ids": ["REW-S1"]}
}
baseline_scores = {}
pass_values = {}
fail_values = {}

with open('scratch/03_RMTE_Mapping.csv', 'r', encoding='utf-8') as f:
    next(f)
    next(f)
    reader = csv.DictReader(f)
    for row in reader:
        metric_id = row['Metric ID']
        if not metric_id: continue
        risk_ids = [r.strip() for r in row['Risk ID'].split(',')]
        
        for rid in risk_ids:
            if rid not in risks:
                risks[rid] = {
                    "name": row['Risk Statement'],
                    "category": row['Trust Attribute'],
                    "trust_attributes": [row['Trust Attribute']]
                }
            if rid not in impacts["TD-06"]["risk_ids"]:
                impacts["TD-06"]["risk_ids"].append(rid)
        
        thresh = parse_threshold(row['Threshold'], row['Metric'])
        
        metrics[metric_id] = {
            "name": row['Metric'],
            "category": row['Trust Attribute'],
            "threshold": thresh,
            "approved_test_ids": ["REW-S1"],
            "risk_ids": risk_ids
        }
        
        impacts["TD-06"]["metric_ids"].append(metric_id)
        
        # evaluation values
        if thresh["comparison"] in ["gte", "gt"]:
            baseline_scores[metric_id] = thresh["value"] + 0.01 if thresh["value"] < 1.0 else 1.0
            pass_values[metric_id] = thresh["value"]
            fail_values[metric_id] = max(0.0, thresh["value"] - 0.2)
        else:
            baseline_scores[metric_id] = 0.0
            pass_values[metric_id] = 0.0
            fail_values[metric_id] = thresh["value"] + 0.1 if thresh["value"] == 0.0 else thresh["value"] * 2

product_fixture = {
  "product": {
    "id": "askmd-rewards",
    "name": "AskMD In-Chat Rewards",
    "archetype": "Tool-Calling Agent",
    "criticality": "high",
    "active_version": "approved-v1",
    "product_version": "prod-v1",
    "prompt_version": "prompt-v1",
    "corpus_version": "corpus-v1",
    "dataset": {
      "id": "prod-golden",
      "version": "dataset-v1",
      "hash": "sha256:d1b2a3f4"
    },
    "memory_snapshot_hash": "sha256:8f9e0d1c",
    "baseline_pack_id": "pack_prod_baseline_v1",
    "baseline_pack_hash": "sha256:5a6b7c8d",
    "baseline_score_ref": "q6://score/prod-policy-v1"
  },
  "registry": {
    "prompt": { "from": "prompt-v1", "to": "prompt-v2", "summary": "Prompt updated." },
    "corpus": { "from": "corpus-v1", "to": "corpus-v2", "summary": "Corpus updated." }
  },
  "risks": risks,
  "metrics": metrics,
  "tests": {
    "REW-S1": {
      "name": "Rewards Test Suite",
      "metric_ids": list(metrics.keys()),
      "version": "1.0.0",
      "hash": "sha256:test-rew-s1",
      "g2_approved": True,
      "estimated_cost": 2.5,
      "estimated_duration_seconds": 120
    }
  },
  "impacts": impacts,
  "baseline_scores": baseline_scores,
  "evaluation": {
    "pass_values": pass_values,
    "fail_values": fail_values
  },
  "seed_changes": [
    {
      "name": "prompt.changed",
      "resource_version": "prompt-v2",
      "product_version": "prod-v1",
      "simulation": "quality_fail",
      "detail": "Updated the prompt to use new tool signatures.",
      "product_id": "askmd-rewards",
      "criticality": "high"
    }
  ]
}

with open('fixtures/askmd_rewards.json', 'w', encoding='utf-8') as f:
    json.dump(product_fixture, f, indent=2)

print("Created fixtures/askmd_rewards.json")
