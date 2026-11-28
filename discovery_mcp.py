"""Replaceable scholarly discovery; no access to a branch's evidence Store."""

import asyncio

from mcp.server.fastmcp import FastMCP

import public_sources
from core.sources import search

mcp = FastMCP("research-discovery")


@mcp.tool(meta={"research": {"capability": "search_papers"}})
async def find_publications(
    query: str, start_year: int | None = None, end_year: int | None = None
) -> dict:
    """Discover scholarly candidates and public reading URLs, not verified findings."""
    rows, errors = await search(query, start_year, end_year)
    return {"source": "Scholarly discovery", "results": rows, "errors": errors}


@mcp.tool(meta={"research": {"capability": "search_datasets"}})
async def find_datasets(query: str) -> dict:
    """Discover public datasets; samples do not establish population properties."""
    result = await asyncio.to_thread(public_sources.search_datasets, query)
    result["results"] = [
        {**r, "dataset": r["id"], "kind": "dataset", "reading_urls": []}
        for r in result["results"]
    ]
    return result


@mcp.tool(meta={"research": {"capability": "search_encyclopedia"}})
async def find_background(query: str) -> dict:
    """Discover encyclopedia background, explicitly distinct from primary research."""
    result = await asyncio.to_thread(public_sources.search_wikipedia, query)
    result["results"] = [
        {**r, "abstract": r["summary"], "kind": "encyclopedia", "reading_urls": []}
        for r in result["results"]
    ]
    return result


if __name__ == "__main__":
    mcp.run()
