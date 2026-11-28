"""Data-driven multi-type chart renderer via AntV Chart MCP with inline SVG fallback payloads."""
import asyncio
import os
import re

from bootstrap import ROOT
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SUPPORTED_TYPES = ("line", "column", "bar", "pie", "mind_map")


def _antv_entrypoint():
    installed = ROOT / "node_modules/@antv/mcp-server-chart/build/index.js"
    return os.getenv("ANTV_MCP_ENTRYPOINT") or (str(installed) if installed.is_file() else None)


def _extract_url(result):
    if result.isError:
        return None
    for block in result.content:
        match = re.search(r'https://[^\s"<>\)]+', getattr(block, "text", ""))
        if match:
            return match.group()
    return None


def _sanitize_mind_map(node, seen, depth=1):
    """Ensure max depth <= 3 and strictly unique node names required by AntV mind_map validator."""
    if not isinstance(node, dict) or not isinstance(node.get("name"), str) or not node["name"].strip():
        return None
    base = node["name"].strip()[:60]
    name = base
    suffix = 2
    while name.lower() in seen:
        name = f"{base} ({suffix})"
        suffix += 1
    seen.add(name.lower())
    out = {"name": name}
    if depth < 3 and isinstance(node.get("children"), list):
        kids = []
        for child in node["children"][:6]:
            cleaned = _sanitize_mind_map(child, seen, depth + 1)
            if cleaned:
                kids.append(cleaned)
        if kids:
            out["children"] = kids
    return out


def normalize_chart_specs(raw_specs, valid_ids):
    """Validate and normalize up to 2 model-proposed charts across 5 universal chart types."""
    if not isinstance(raw_specs, list):
        return []
    charts = []
    for spec in raw_specs:
        if not isinstance(spec, dict):
            continue
        ctype = spec.get("chart_type")
        if ctype not in SUPPORTED_TYPES:
            continue
        eid = spec.get("evidence_id")
        if not isinstance(eid, str) or eid not in valid_ids:
            continue
        if not eid:
            continue
        title = (spec.get("title") or "").strip() if isinstance(spec.get("title"), str) else ""
        if not title:
            continue
        source_note = (spec.get("source_note") or "").strip() if isinstance(spec.get("source_note"), str) else f"Grounded in {eid}"
        raw_data = spec.get("data")

        if ctype == "mind_map":
            tree = _sanitize_mind_map(raw_data, set(), depth=1)
            if not tree or not tree.get("children"):
                continue
            charts.append(
                {
                    "chart_type": "mind_map",
                    "title": title,
                    "evidence_id": eid,
                    "source_note": source_note,
                    "data": tree,
                    "image_url": None,
                    "renderer": "inline_svg",
                }
            )
        elif ctype == "line":
            if not isinstance(raw_data, list):
                continue
            points = []
            for row in raw_data[:30]:
                if not isinstance(row, dict) or row.get("time") is None:
                    continue
                try:
                    val = float(row["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                pt = {"time": str(row["time"])[:32], "value": float(f"{val:.6g}")}
                if isinstance(row.get("group"), str) and row["group"].strip():
                    pt["group"] = row["group"].strip()[:36]
                points.append(pt)
            if len(points) < 2:
                continue
            charts.append(
                {
                    "chart_type": "line",
                    "title": title,
                    "evidence_id": eid,
                    "source_note": source_note,
                    "data": points,
                    "image_url": None,
                    "renderer": "inline_svg",
                }
            )
        else:  # column, bar, pie
            if not isinstance(raw_data, list):
                continue
            points = []
            for row in raw_data[:12]:
                if not isinstance(row, dict) or not isinstance(row.get("category"), str) or not row["category"].strip():
                    continue
                try:
                    val = float(row["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                if ctype == "pie" and val <= 0:
                    continue
                pt = {"category": row["category"].strip()[:40], "value": float(f"{val:.6g}")}
                if ctype != "pie" and isinstance(row.get("group"), str) and row["group"].strip():
                    pt["group"] = row["group"].strip()[:32]
                points.append(pt)
            if len(points) < 2:
                continue
            charts.append(
                {
                    "chart_type": ctype,
                    "title": title,
                    "evidence_id": eid,
                    "source_note": source_note,
                    "data": points,
                    "image_url": None,
                    "renderer": "inline_svg",
                }
            )
        if len(charts) >= 2:
            break
    return charts


async def _call_antv_tool(client, chart):
    ctype = chart["chart_type"]
    if ctype == "line":
        result = await client.call_tool("generate_line_chart", {"title": chart["title"], "data": chart["data"]})
    elif ctype == "column":
        has_group = any("group" in row for row in chart["data"])
        payload = {"title": chart["title"], "data": chart["data"], "group": has_group}
        result = await client.call_tool("generate_column_chart", payload)
    elif ctype == "bar":
        has_group = any("group" in row for row in chart["data"])
        payload = {"title": chart["title"], "data": chart["data"], "group": has_group, "stack": False}
        result = await client.call_tool("generate_bar_chart", payload)
    elif ctype == "pie":
        payload = {"title": chart["title"], "data": chart["data"], "innerRadius": 0.52}
        result = await client.call_tool("generate_pie_chart", payload)
    elif ctype == "mind_map":
        result = await client.call_tool("generate_mind_map", {"data": chart["data"]})
    else:
        return None
    return _extract_url(result)


async def render_charts(charts):
    """Render validated multi-type charts via AntV Chart MCP; keep structured payloads for SVG fallback."""
    entrypoint = _antv_entrypoint()
    if not charts or not entrypoint:
        return charts
    params = StdioServerParameters(command="node", args=[entrypoint])
    try:
        async with asyncio.timeout(30), stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
            await client.initialize()
            for chart in charts:
                try:
                    async with asyncio.timeout(15):
                        url = await _call_antv_tool(client, chart)
                    if url:
                        chart["image_url"] = url
                        chart["renderer"] = "antv_mcp"
                except (OSError, TimeoutError, ValueError):
                    continue
    except (OSError, TimeoutError, ValueError, ExceptionGroup):
        pass
    return charts
