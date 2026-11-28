"""CPU BM25/dense retrieval, RRF fusion and cross-encoder reranking.

Exact passage IDs survive indexing; model failures are explicit BM25 fallbacks.
"""

import hashlib
import json
import logging
import math
import os
import re
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

from research import setting

ROOT = Path(__file__).resolve().parents[1]
EMBEDDING = "BAAI/bge-small-en-v1.5"
RERANKER = "ms-marco-TinyBERT-L-2-v2"
MODES = {"lexical", "bm25", "dense", "hybrid-no-rerank", "hybrid"}


def prepare_runtime():
    """Initialize native DLL imports on the server main thread before worker threads."""
    try:
        import fastembed
        import flashrank
        import numpy

        return bool(numpy and fastembed and flashrank)
    except ImportError:
        return False


@lru_cache(maxsize=1)
def model_signature():
    manifest = ROOT / ".models/models.json"
    return (
        hashlib.sha256(manifest.read_bytes()).hexdigest()
        if manifest.exists()
        else "unprepared"
    )


def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def bm25(query, texts):
    documents = [Counter(tokens(text)) for text in texts]
    n = len(documents)
    if not n:
        return []
    lengths = [sum(d.values()) for d in documents]
    average = max(1, sum(lengths) / n)
    scores = [0.0] * n
    for token in set(tokens(query)):
        frequency = sum(token in d for d in documents)
        idf = math.log(1 + (n - frequency + 0.5) / (frequency + 0.5))
        for i, doc in enumerate(documents):
            tf = doc[token]
            scores[i] += (
                idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * lengths[i] / average))
            )
    return scores


def fuse(rankings, k=60):
    scores = Counter()
    for order in rankings:
        for rank, index in enumerate(order, 1):
            scores[index] += 1 / (k + rank)
    return sorted(scores, key=lambda i: (-scores[i], i)), scores


@lru_cache(maxsize=1)
def encoder():
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from fastembed import TextEmbedding

    return TextEmbedding(
        EMBEDDING,
        cache_dir=str(ROOT / ".models/embedding"),
        threads=2,
        providers=["CPUExecutionProvider"],
        local_files_only=True,
    )


@lru_cache(maxsize=1)
def reranker():
    from flashrank import Ranker

    return Ranker(
        model_name=RERANKER, cache_dir=str(ROOT / ".models/reranker"), max_length=384
    )


def dense_scores(query, document):
    import uuid

    import numpy as np

    texts = [p["text"] for p in document["passages"]]
    identity = hashlib.sha256(
        json.dumps(
            [EMBEDDING, model_signature(), document["sha256"], texts],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    directory = ROOT / ".models/indices"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (identity + ".npy")
    cached = path.exists()
    if cached:
        matrix = np.load(path, allow_pickle=False)
    else:
        matrix = np.array(list(encoder().passage_embed(texts)), dtype=np.float32)
        matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
        temporary = path.with_name(identity + "." + uuid.uuid4().hex + ".tmp")
        with temporary.open("wb") as stream:
            np.save(stream, matrix, allow_pickle=False)
        temporary.replace(path)
    if matrix.shape != (len(texts), 384) or not np.isfinite(matrix).all():
        raise ValueError("Invalid versioned dense index")
    vector = np.array(next(iter(encoder().query_embed([query]))), dtype=np.float32)
    vector /= max(float(np.linalg.norm(vector)), 1e-12)
    return (matrix @ vector).tolist(), cached


def retrieve(document, query, limit=6, mode=None):
    started = time.monotonic()
    mode = mode or setting("RETRIEVAL_MODE", "hybrid-no-rerank")
    if mode not in MODES or not 1 <= limit <= 12:
        raise ValueError("Unknown retrieval mode or invalid result limit")
    passages = document["passages"]
    texts = [p["text"] for p in passages]
    metadata = {
        "requested_mode": mode,
        "effective_mode": mode,
        "embedding_model": None,
        "model_signature": model_signature(),
        "reranker": None,
        "document_sha256": document["sha256"],
        "warnings": [],
        "dense_cache_hit": False,
    }
    if not passages:
        return {**document, "passages": [], "retrieval": metadata}
    lexical = bm25(query, texts)
    order = sorted(range(len(texts)), key=lambda i: (-lexical[i], i))
    scores = lexical
    if mode == "lexical":
        words = set(re.findall(r"[a-z]{3,}", query.lower()))
        scores = [
            len(words & set(re.findall(r"[a-z]{3,}", text.lower()))) for text in texts
        ]
        order = sorted(range(len(texts)), key=lambda i: (-scores[i], i))
    elif mode in {"dense", "hybrid-no-rerank", "hybrid"}:
        try:
            dense, cached = dense_scores(query, document)
            metadata.update(embedding_model=EMBEDDING, dense_cache_hit=cached)
            semantic = sorted(range(len(texts)), key=lambda i: (-dense[i], i))
            if mode == "dense":
                order, scores = semantic, dense
            else:
                order, fused = fuse([order[:24], semantic[:24]])
                scores = [fused.get(i, 0) for i in range(len(texts))]
        except Exception as exc:
            logging.exception("Local dense retrieval failed")
            metadata["warnings"].append(
                "Dense retrieval unavailable: " + type(exc).__name__
            )
            metadata["effective_mode"] = "bm25"
    if mode == "hybrid" and metadata["embedding_model"]:
        try:
            from flashrank import RerankRequest

            candidates = [{"id": i, "text": texts[i]} for i in order[:24]]
            ranked = reranker().rerank(RerankRequest(query=query, passages=candidates))
            order = [int(r["id"]) for r in ranked]
            scores = {int(r["id"]): float(r["score"]) for r in ranked}
            metadata["reranker"] = RERANKER
        except Exception as exc:
            logging.exception("Local reranking failed")
            metadata["warnings"].append("Reranking unavailable: " + type(exc).__name__)
            metadata["effective_mode"] = "hybrid-no-rerank"
    selected = order[:limit]
    neighbors = []
    for i in selected:
        for j in (i - 1, i + 1):
            if 0 <= j < len(passages) and j not in selected and j not in neighbors:
                neighbors.append(j)
    neighbors = neighbors[:limit]
    metadata.update(
        candidate_count=len(order),
        elapsed_ms=round((time.monotonic() - started) * 1000),
        ranking=[
            {"passage_id": passages[i]["id"], "score": float(scores[i])}
            for i in selected
        ],
    )
    return {
        **document,
        "passages": [passages[i] for i in selected],
        "context_passages": [passages[i] for i in neighbors],
        "retrieval": metadata,
    }
