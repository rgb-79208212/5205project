"""Discover and route semantic capabilities across replaceable MCP servers."""

import json
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from jsonschema import validate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def content(result):
    return result.structuredContent or json.loads(result.content[0].text)


class MCPHost:
    def __init__(self):
        self.routes = {}
        self.sessions = {}
        self.catalog = []
        self.definitions = None
        self.protocol_trace = []

    async def attach(self, label, session):
        tools = (await session.list_tools()).tools
        additions = {}
        for tool in tools:
            capability = (tool.meta or {}).get("research", {}).get("capability")
            if not capability:
                continue
            if capability in self.routes or capability in additions:
                raise ValueError(f"Duplicate MCP capability: {capability}")
            additions[capability] = (session, tool.name, tool.inputSchema)
        self.routes.update(additions)
        self.sessions[label] = session
        self.catalog.extend(
            {"capability": cap, "server": label, "tool": name, "input_schema": schema}
            for cap, (_, name, schema) in additions.items()
        )

    async def list_tools(self):
        from mcp.types import Tool

        return SimpleNamespace(
            tools=[
                Tool(name=cap, inputSchema=schema)
                for cap, (_, _, schema) in self.routes.items()
            ]
        )

    async def call_tool(self, capability, arguments):
        if capability not in self.routes:
            raise ValueError(f"Unavailable MCP capability: {capability}")
        session, actual_name, schema = self.routes[capability]
        validate(arguments, schema)
        if capability == "get_passages" and arguments.get("document_sha256"):
            return await self._versioned_context(arguments)
        self.protocol_trace.append(
            {"method": "tools/call", "tool": actual_name, "capability": capability}
        )
        result = await session.call_tool(actual_name, arguments)
        if result.isError:
            return result
        # Discovery is stateless with respect to the library. Only the host transfers
        # registered candidates; the two servers never share a writable Store.
        if capability in {"search_papers", "search_datasets", "search_encyclopedia"}:
            data = content(result)
            if data.get("results"):
                imported = await self.call_tool(
                    "register_sources", {"sources": data["results"]}
                )
                if imported.isError:
                    return imported
                registered = content(imported)
                data.update(
                    results=registered["results"],
                    source_catalog=registered["source_catalog"],
                )
                result.structuredContent = data
        return result

    async def _versioned_context(self, arguments):
        from mcp.types import CallToolResult, TextContent

        uri = f"research://documents/{arguments['source_id']}/{arguments['document_sha256']}"
        result = await self.read_resource(uri)
        document = json.loads(result.contents[0].text)
        wanted = set(arguments["passage_ids"])
        positions = [i for i, p in enumerate(document["passages"]) if p["id"] in wanted]
        if len(positions) != len(wanted):
            raise ValueError("Unknown passage in the requested document version")
        window = max(0, min(2, arguments.get("neighbor_window", 0)))
        indices = {
            j
            for i in positions
            for j in range(
                max(0, i - window), min(len(document["passages"]), i + window + 1)
            )
        }
        data = {
            **document,
            "passages": [p for i, p in enumerate(document["passages"]) if i in indices],
            "resource_uri": uri,
            "mcp_method": "resources/read",
        }
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(data, ensure_ascii=False))
            ],
            structuredContent=data,
        )

    async def read_resource(self, uri):
        session, _, _ = self.routes["get_passages"]
        self.protocol_trace.append({"method": "resources/read", "uri": uri})
        return await session.read_resource(uri)

    def fork(self, directory):
        # Return a context manager: each branch owns fresh clients and server state.
        return connect_servers(directory, self.definitions)


@asynccontextmanager
async def connect_servers(directory, servers=None):
    directory = Path(directory).resolve()
    if not directory.is_relative_to((ROOT / "runs").resolve()):
        raise ValueError("MCP run state must stay inside this project's runs directory")
    directory.mkdir(parents=True, exist_ok=True)
    definitions = (
        servers
        if servers is not None
        else [
            (entry["name"], ROOT / entry["script"])
            for entry in json.loads(
                (ROOT / "mcp_servers.json").read_text(encoding="utf-8")
            )
        ]
    )
    host = MCPHost()
    host.definitions = definitions
    async with AsyncExitStack() as stack:
        for label, script in definitions:
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(script)],
                cwd=str(ROOT),
                env={
                    "RESEARCH_RUN_DIR": str(directory),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONIOENCODING": "utf-8",
                    "OPENBLAS_NUM_THREADS": "2",
                    "OMP_NUM_THREADS": "2",
                    **{
                        key: os.environ[key]
                        for key in ("RETRIEVAL_MODE", "OPENALEX_API_KEY")
                        if key in os.environ
                    },
                },
            )
            reader, writer = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(reader, writer))
            await session.initialize()
            await host.attach(label, session)
        yield host
