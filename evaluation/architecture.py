"""Retrieval ablation over fixed, versioned original-paper passages. No cloud LLM."""

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.retrieval import retrieve


def load_corpus(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    from core.sources import read_source
    from core.store import Store

    async def download():
        manifest = json.loads(
            (Path(__file__).parent / "retrieval_sources.json").read_text(
                encoding="utf-8"
            )
        )
        store = Store(ROOT / "runs/retrieval-corpus")
        corpus = {}
        for key, expected in manifest.items():
            source = store.add_sources(
                [
                    {
                        "title": expected["title"],
                        "url": expected["url"],
                        "reading_urls": [expected["url"]],
                    }
                ]
            )[0]
            await read_source(store, source["id"])
            document = store.data["documents"][source["id"]]
            if document["sha256"] != expected["sha256"]:
                raise ValueError("Benchmark document version changed: " + key)
            corpus[key] = {"source": source, "document": document}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return corpus

    return asyncio.run(download())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        default=str(ROOT / ".planning/architecture-upgrade/retrieval-corpus.json"),
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / ".planning/architecture-upgrade/retrieval-ablation.json"),
    )
    args = parser.parse_args()
    corpus = load_corpus(Path(args.corpus))
    cases_path = Path(__file__).parent / "retrieval_cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    rows = []
    for case in cases:
        document = corpus[case["corpus"]]["document"]
        gold = set(case["gold_passages"])
        assert gold <= {p["id"] for p in document["passages"]}
        for mode in ("lexical", "bm25", "dense", "hybrid-no-rerank", "hybrid"):
            result = retrieve(document, case["query"], 6, mode)
            ids = [p["id"] for p in result["passages"]]
            ranks = [index + 1 for index, pid in enumerate(ids) if pid in gold]
            rows.append(
                {
                    "case": case["id"],
                    "mode": mode,
                    "document_sha256": document["sha256"],
                    "ids": ids,
                    "hit_at_1": bool(gold & set(ids[:1])),
                    "hit_at_3": bool(gold & set(ids[:3])),
                    "hit_at_6": bool(gold & set(ids)),
                    "reciprocal_rank_at_6": 1 / min(ranks) if ranks else 0,
                    "retrieval": result["retrieval"],
                }
            )
            print(case["id"], mode, "target_found", bool(ranks), flush=True)
    summaries = {}
    for mode in sorted({r["mode"] for r in rows}):
        selected = [r for r in rows if r["mode"] == mode]
        summaries[mode] = {
            key: statistics.mean(r[key] for r in selected)
            for key in ("hit_at_1", "hit_at_3", "hit_at_6", "reciprocal_rank_at_6")
        }
        summaries[mode]["mean_ms_including_first_load"] = statistics.mean(
            r["retrieval"]["elapsed_ms"] for r in selected
        )
        summaries[mode]["degraded_queries"] = sum(
            bool(r["retrieval"]["warnings"]) for r in selected
        )
    report = {
        "kind": "fixed_target_passage_diagnostic",
        "expert_blind_labels": False,
        "note": "12 manually selected queries over six real saved papers; target IDs are limited annotations, not exhaustive relevance judgments. Does not measure answer accuracy. First-load and indexing costs are included; later modes may reuse indexes.",
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "summaries": summaries,
        "rows": rows,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summaries, indent=2), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
