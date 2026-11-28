"""Bounded live access to public open-data and encyclopedia APIs (Hugging Face & Wikipedia)."""

import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HF_API = "https://huggingface.co/api/datasets"
HF_VIEWER = "https://datasets-server.huggingface.co/"
WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "Deep-Research-Studio/3.0 (DSAI5205 Academic Research Project)"


def read(url, *, timeout=15, **params):
    query = urlencode(params)
    full_url = f"{url}?{query}" if query else url
    last_error = None
    for attempt in range(2):
        request = Request(full_url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read(16_000_001)
            if len(payload) > 16_000_000:
                raise ValueError("Source response exceeds the size limit.")
            return json.loads(payload)
        except OSError as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.35)
                continue
            raise
    raise last_error


def dataset_id(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?", value)
        or any(piece in {".", ".."} for piece in value.split("/"))
    ):
        raise ValueError("Invalid dataset identifier.")
    return value


def search_datasets(query):
    clean = query.strip()[:120]
    if not clean:
        raise ValueError("Provide a non-empty dataset search query.")
    items = read(HF_API, search=clean, limit=8)
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", clean) and not any(
        item.get("id") == clean for item in items
    ):
        try:
            exact = read(HF_API + "/" + dataset_id(clean))
            if exact.get("id"):
                items.insert(0, exact)
        except HTTPError as exc:
            if exc.code not in (401, 403, 404):
                raise
    results = []
    for item in items:
        if item.get("private") or item.get("gated"):
            continue
        if any(row["id"] == item["id"] for row in results):
            continue
        results.append(
            {
                "id": item["id"],
                "title": item["id"],
                "downloads": item.get("downloads"),
                "likes": item.get("likes"),
                "url": f"https://huggingface.co/datasets/{item['id']}",
            }
        )
    return {
        "source": "Hugging Face Hub",
        "results": results,
    }


def inspect_dataset(dataset, sample_rows=5):
    ds = dataset_id(dataset)
    if not 1 <= sample_rows <= 15:
        raise ValueError("Request between 1 and 15 sample rows.")
    splits_payload = read(HF_VIEWER + "splits", dataset=ds)
    splits = splits_payload.get("splits", [])[:20]
    url = f"https://huggingface.co/datasets/{ds}"
    if not splits:
        return {
            "source": "Hugging Face Dataset Viewer",
            "dataset": ds,
            "url": url,
            "splits": [],
            "sample": True,
            "rows": [],
            "pending": bool(splits_payload.get("pending")),
            "failed": bool(splits_payload.get("failed")),
        }

    chosen = splits[0]
    config, split = chosen["config"], chosen["split"]
    rows_payload = {}
    row_note = ""
    try:
        rows_payload = read(
            HF_VIEWER + "rows",
            dataset=ds,
            config=config,
            split=split,
            offset=0,
            length=sample_rows,
        )
    except HTTPError as exc:
        try:
            rows_payload = read(
                HF_VIEWER + "first-rows", dataset=ds, config=config, split=split
            )
            rows_payload["rows"] = rows_payload.get("rows", [])[:sample_rows]
        except (HTTPError, OSError):
            row_note = f"Row preview unavailable via Dataset Viewer (HTTP {exc.code}); splits metadata retrieved."

    features = [
        f.get("name")
        for f in rows_payload.get("features", [])
        if isinstance(f, dict) and f.get("name")
    ]
    out = {
        "source": "Hugging Face Dataset Viewer",
        "dataset": ds,
        "url": url,
        "splits": splits,
        "selected_config": config,
        "selected_split": split,
        "total_rows": rows_payload.get("num_rows_total"),
        "features": features,
        "sample": True,
        "rows": rows_payload.get("rows", [])[:sample_rows],
    }
    if row_note:
        out["note"] = row_note
    return out


def search_wikipedia(query):
    clean = query.strip()[:180]
    if not clean:
        raise ValueError("Provide a non-empty encyclopedia search query.")
    body = read(
        WIKI_API,
        timeout=10,
        action="query",
        generator="search",
        gsrsearch=clean,
        gsrlimit=5,
        prop="extracts|info",
        exintro=1,
        explaintext=1,
        exchars=950,
        inprop="url",
        format="json",
        redirects=1,
    )
    pages = (body.get("query") or {}).get("pages") or {}
    ordered = sorted(pages.values(), key=lambda p: p.get("index", 999))
    results = []
    for page in ordered:
        title = page.get("title")
        extract = (page.get("extract") or "").strip()
        url = page.get("fullurl")
        if not title or not extract or not url:
            continue
        results.append({"title": title, "url": url, "summary": extract[:950]})
    return {
        "source": "Wikipedia",
        "results": results,
        "scope": "Encyclopedic background summaries. Pair with primary scholarly or dataset evidence for empirical claims.",
    }
