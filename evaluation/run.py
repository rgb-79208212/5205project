"""Reproducible synthetic diagnostics and separate human-review exports.

Run from project root: python evaluation/run.py [--model] [--feedback]
"""

import argparse
import asyncio
import csv
import hashlib
import json
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.numeric import numeric_gate, relation_gate
from core.skills import Skills
from core.verification import claim_checks
from evaluation.cases import cases
from research import model_json

OUTPUT = ROOT / ".planning" / "qa-upgrade" / "evaluations"


def metrics(rows, key):
    tp = sum(r["expected_supported"] and r[key] for r in rows)
    fp = sum(not r["expected_supported"] and r[key] for r in rows)
    fn = sum(r["expected_supported"] and not r[key] for r in rows)
    tn = sum(not r["expected_supported"] and not r[key] for r in rows)
    return {
        "true_approved": tp,
        "false_approved": fp,
        "true_withheld": tn,
        "false_withheld": fn,
        "approved_support_rate": tp / (tp + fp) if tp + fp else None,
        "false_release_rate": fp / (fp + tn) if fp + tn else None,
        "false_withhold_rate": fn / (fn + tp) if fn + tp else None,
    }


async def diagnose(use_model=False, limit=None):
    skills, rows = Skills(), []
    instructions, _ = skills.activate(
        "auditor", "Synthetic evaluation against authored expectations"
    )
    for case in cases()[:limit]:
        start = time.monotonic()
        quote = case["quote"]
        content = case["access"] != "metadata_only" and (
            not case["requires_full_text"] or case["access"] == "full_text"
        )
        candidates = []
        batch = []
        for candidate in case["candidates"]:
            tokens = numeric_gate(candidate["text"], quote)
            relations = relation_gate(
                candidate["text"], quote, candidate["numeric_facts"]
            )
            row = {
                **candidate,
                "question_id": case["id"],
                "category": case["category"],
                "token_rules": content and tokens["ok"],
                "relation_rules": content and relations["ok"],
                "rule_details": relations,
            }
            batch.append(row)
            if row["relation_rules"]:
                candidates.append(
                    {
                        "id": candidate["id"],
                        "question_id": "Q1",
                        "text": candidate["text"],
                        "refs": ["E1"],
                        "kind": "direct",
                        "access": case["access"],
                        "numeric_facts": candidate["numeric_facts"],
                        "numeric_gate": relations,
                    }
                )
        if use_model:
            decisions, error = {}, None
            if candidates:
                evidence = {
                    "id": "E1",
                    "source_id": "SIM-S1",
                    "passage_id": "P0001",
                    "quote": quote,
                    "access": case["access"],
                    "document_sha256": hashlib.sha256(quote.encode()).hexdigest(),
                    "locator": "Synthetic test passage",
                }
                payload = {
                    "question": case["question"],
                    "subquestions": [
                        {
                            "id": "Q1",
                            "question": case["question"],
                            "requires_full_text": case["requires_full_text"],
                            "criteria": [
                                {
                                    "id": "Q1.C1",
                                    "description": "Answer the stated question within the supplied evidence scope",
                                }
                            ],
                        }
                    ],
                    "claims": candidates,
                    "evidence": [evidence],
                    "contexts": [
                        {
                            "source_id": "SIM-S1",
                            "passages": [{"id": "P0001", "text": quote}],
                            "access": case["access"],
                        }
                    ],
                    "previous_searches": [],
                }
                try:
                    audit = skills.validate(
                        "auditor", await model_json(instructions, payload)
                    )
                    ids = [c["claim_id"] for c in audit["checks"]]
                    if set(ids) != {c["id"] for c in candidates} or len(ids) != len(
                        set(ids)
                    ):
                        raise ValueError("Incomplete audit")
                    decisions = {c["claim_id"]: c for c in audit["checks"]}
                except Exception as exc:
                    error = type(exc).__name__
            for row in batch:
                verdict = decisions.get(row["id"], {})
                row.update(
                    rules_and_model=row["relation_rules"]
                    and verdict.get("status") == "supported",
                    model_status=verdict.get("status", "not_assessed"),
                    model_reason=verdict.get(
                        "reason", "Rule gate blocked or model unavailable"
                    ),
                    model_error=error,
                )
        elapsed = round(time.monotonic() - start, 3)
        for row in batch:
            row["question_seconds"] = elapsed
        rows.extend(batch)
        print(f"{case['id']} {case['category']}: {elapsed}s", flush=True)
    keys = ["token_rules", "relation_rules"] + (
        ["rules_and_model"] if use_model else []
    )
    return {
        "kind": "synthetic_model_diagnostic"
        if use_model
        else "synthetic_rule_diagnostic",
        "synthetic": True,
        "label_origin": "Authored expectations, not independent human annotations of real papers",
        "scope": "Candidate-level ablation on fixed supplied passages; not an end-to-end retrieval benchmark",
        "metrics": {key: metrics(rows, key) for key in keys},
        "rows": rows,
    }


async def feedback_cases():
    sys.path.insert(0, str(ROOT / "tests"))
    from test_research import FixtureModel, FixtureSession

    from core.agent import Research

    output = []
    for case in ("normal", "gap", "conflict", "false_number"):
        for rounds in (1, 2):
            model = FixtureModel(
                first_audit="partial"
                if case == "gap"
                else "conflict"
                if case == "conflict"
                else "supported",
                number="999.9%" if case == "false_number" else "80%",
                repair=case != "conflict",
            )
            with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
                result = await Research(
                    "SIMULATED feedback evaluation: " + case,
                    FixtureSession(directory),
                    model,
                    lambda *a: None,
                    directory,
                    max_rounds=rounds,
                ).run()
            criteria = result["research"]["criteria"]
            output.append(
                {
                    "scenario": case,
                    "feedback": rounds > 1,
                    "simulation": True,
                    "status": result["status"],
                    "rounds": result["research"]["rounds"],
                    "criterion_coverage": sum(c["status"] == "met" for c in criteria)
                    / len(criteria)
                    if criteria
                    else None,
                    "model_calls": len(model.inputs),
                    "tool_calls": result["stats"]["steps"],
                    "elapsed_ms": result["stats"]["elapsed_ms"],
                }
            )
    return output


def export_review(directory):
    path = (ROOT / directory).resolve()
    if not path.is_relative_to((ROOT / "runs").resolve()):
        raise ValueError("Export a run inside this project's runs directory")
    state = json.loads((path / "research-state.json").read_text(encoding="utf-8"))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / (path.name + "-human-review.csv")
    if target.exists():
        raise FileExistsError(
            "Review sheet already exists; annotations will not be overwritten"
        )
    fields = [
        "run_id",
        "claim_id",
        "question",
        "claim",
        "system_status",
        "system_published",
        "quotes",
        "locators",
        "human_support",
        "human_coverage",
        "reviewer",
        "notes",
    ]
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for claim in state["claims"]:
            records = [state["evidence"][r] for r in claim["refs"]]
            writer.writerow(
                {
                    "run_id": path.name,
                    "claim_id": claim["id"],
                    "question": state["question"],
                    "claim": claim["text"],
                    "system_status": claim["status"],
                    "system_published": str(
                        claim_checks(claim, state["evidence"])["publishable"]
                    ).lower(),
                    "quotes": "\n\n".join(r["quote"] for r in records),
                    "locators": "\n".join(
                        r["url"] + " " + r["locator"] for r in records
                    ),
                }
            )
    print("Human review sheet:", target.relative_to(ROOT), flush=True)


def score_review(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        source = list(csv.DictReader(stream))
    rows = []
    for row in source:
        label = row["human_support"].strip().lower()
        if label not in ("supported", "unsupported"):
            continue
        rows.append(
            {
                "expected_supported": label == "supported",
                "published": row["system_published"] == "true",
            }
        )
    return {
        "kind": "human_review",
        "annotated": len(rows),
        "pending_or_ambiguous": len(source) - len(rows),
        "metrics": metrics(rows, "published") if rows else None,
        "note": "Partial/uncertain labels are excluded and counted as pending; coverage requires question-level human review.",
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        action="store_true",
        help="Call configured model on synthetic passages",
    )
    parser.add_argument(
        "--feedback",
        action="store_true",
        help="Include simulated feedback-on/off trajectories",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--export-run")
    parser.add_argument("--score-review")
    args = parser.parse_args()
    if args.export_run:
        export_review(args.export_run)
        return
    report = (
        score_review(args.score_review)
        if args.score_review
        else await diagnose(args.model, args.limit)
    )
    if args.feedback:
        report["simulated_feedback_ablation"] = await feedback_cases()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S-%f")
    target = OUTPUT / (report["kind"] + "-" + stamp + ".json")
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"file": str(target.relative_to(ROOT)), "metrics": report["metrics"]},
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
