"""Run with a private run directory; all evidence writes are local to that run."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import public_sources
from core.artifacts import merge_snapshot
from core.retrieval import prepare_runtime, retrieve
from core.sources import read_source as read_document
from core.store import Store

ROOT = Path(__file__).resolve().parent
directory = Path(
    os.environ.get("RESEARCH_RUN_DIR", str(ROOT / "runs" / "manual"))
).resolve()
if not directory.is_relative_to((ROOT / "runs").resolve()):
    raise ValueError("MCP run state must stay inside this project's runs directory.")
store = Store(directory)
mcp = FastMCP("research-library")


@mcp.tool(meta={"research": {"capability": "register_sources"}})
def catalog_candidates(sources: list[dict]) -> dict:
    """Host handoff: register discovered candidates before any document can be read."""
    if len(sources) > 30 or any(
        not r.get("title") or not r.get("url") for r in sources
    ):
        raise ValueError("Expected at most 30 titled, addressable candidates")
    return {
        "results": store.add_sources(sources),
        "source_catalog": store.data["sources"],
    }


@mcp.tool(meta={"research": {"capability": "import_branch"}})
def import_research_artifacts(snapshot: dict) -> dict:
    """Host-only handoff; re-register exact quotes before accepting branch artifacts."""
    return merge_snapshot(store, snapshot)


@mcp.tool(meta={"research": {"capability": "search_passages"}})
async def retrieve_document_passages(
    source_id: str, query: str, limit: int = 6, mode: str | None = None
) -> dict:
    """Search the saved full body with BM25/dense/RRF/reranking, retaining exact passage IDs."""
    document = store.data["documents"].get(source_id)
    if document is None or document["access"] != "full_text":
        raise ValueError("Read accessible full text before passage retrieval")
    return await asyncio.to_thread(retrieve, document, query, limit, mode)


@mcp.tool(meta={"research": {"capability": "read_source"}})
async def read_source(source_id: str, focus: str = "") -> dict:
    """Read a previously discovered source; return stable passages, access level, content hash and page/section locators."""
    source = store.data["sources"].get(source_id, {})
    if source.get("kind") == "dataset":
        saved = store.data["documents"].get(source_id)
        if saved and saved["access"] == "dataset_sample":
            return saved
        data = await asyncio.to_thread(
            public_sources.inspect_dataset, source["dataset"], 3
        )
        text = json.dumps(data, ensure_ascii=False)
        # Keep field values near their names instead of burying them after long reviews.
        metadata = {key: value for key, value in data.items() if key != "rows"}
        metadata["sample_rows_inspected"] = len(data.get("rows", []))
        paragraphs = [
            {
                "text": json.dumps(metadata, ensure_ascii=False),
                "locator": "Dataset Viewer metadata; bounded sample only",
            }
        ]
        for index, row in enumerate(data.get("rows", [])):
            values = row.get("row", {})
            short = {
                key: value
                for key, value in values.items()
                if not isinstance(value, (str, list, dict))
                or len(json.dumps(value, ensure_ascii=False)) <= 250
            }
            record = {
                "dataset": source["dataset"],
                "config": data.get("selected_config"),
                "split": data.get("selected_split"),
                "row_idx": row.get("row_idx", index),
                "row": short,
            }
            paragraphs.append(
                {
                    "text": json.dumps(record, ensure_ascii=False),
                    "locator": f"Dataset Viewer sample row {index + 1}; long values omitted",
                }
            )
        return store.document(
            source_id,
            paragraphs,
            "dataset_sample",
            source["url"],
            hashlib.sha256(text.encode()).hexdigest(),
        )
    if source.get("kind") == "encyclopedia":
        text = source["abstract"]
        return store.document(
            source_id,
            [{"text": text, "locator": "Encyclopedia introduction excerpt"}],
            "encyclopedic_summary",
            source["url"],
            hashlib.sha256(text.encode()).hexdigest(),
        )
    return await read_document(store, source_id, focus)


@mcp.tool(meta={"research": {"capability": "get_passages"}})
def get_passages(
    source_id: str,
    passage_ids: list[str],
    document_sha256: str | None = None,
    neighbor_window: int = 0,
) -> dict:
    """Retrieve exact passages from this run's saved document, without another network fetch."""
    return store.passages(source_id, passage_ids, document_sha256, neighbor_window)


@mcp.tool(meta={"research": {"capability": "register_evidence"}})
def register_evidence(
    source_id: str, passage_id: str, quote: str, question_id: str
) -> dict:
    """Register an exact quote and character offsets. Invalid or invented quotes/IDs are rejected."""
    return store.register(source_id, passage_id, quote, question_id)


@mcp.tool(meta={"research": {"capability": "inspect_dataset"}})
def inspect_dataset(dataset: str, sample_rows: int = 5) -> dict:
    """Optional bounded sample inspection, never population-level evidence."""
    return public_sources.inspect_dataset(dataset, sample_rows)


@mcp.resource(
    "research://documents/{source_id}/{version}", mime_type="application/json"
)
def document_resource(source_id: str, version: str) -> str:
    """Immutable document snapshot from this run; no URL fetch or arbitrary file access."""
    document = store.data["document_versions"].get(source_id + ":" + version)
    if document is None:
        raise ValueError("Unknown document version")
    return json.dumps(document, ensure_ascii=False)


if __name__ == "__main__":
    prepare_runtime()
    mcp.run(transport="stdio")
