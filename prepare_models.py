"""Download and smoke-test the public CPU retrieval models. No API key is needed."""

import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from fastembed import TextEmbedding
from flashrank import Ranker, RerankRequest

ROOT = Path(__file__).resolve().parent


def main():
    cache = ROOT / ".models"
    embedding = TextEmbedding(
        "BAAI/bge-small-en-v1.5",
        cache_dir=str(cache / "embedding"),
        threads=2,
        providers=["CPUExecutionProvider"],
    )
    vector = next(
        iter(embedding.query_embed(["Where do retrieved passages come from?"]))
    )
    ranker = Ranker(cache_dir=str(cache / "reranker"), max_length=384)
    ranked = ranker.rerank(
        RerankRequest(
            query="Where does retrieved information come from?",
            passages=[
                {
                    "id": "source",
                    "text": "An external collection supplies retrieved passages.",
                },
                {"id": "other", "text": "The cat sleeps on a sofa."},
            ],
        )
    )
    if len(vector) != 384 or ranked[0]["id"] != "source":
        raise ValueError("Retrieval model smoke test failed")
    files = {
        str(p.relative_to(cache)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in cache.rglob("*.onnx")
    }
    report = {
        "embedding": "BAAI/bge-small-en-v1.5",
        "dimension": 384,
        "reranker": "ms-marco-TinyBERT-L-2-v2",
        "provider": "CPU",
        "model_files": files,
    }
    (cache / "models.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps({k: v for k, v in report.items() if k != "model_files"}), flush=True
    )


if __name__ == "__main__":
    main()
