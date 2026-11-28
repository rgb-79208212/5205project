"""Observable capability replacement, package extension, retrieval and DAG recovery."""

import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jsonschema import ValidationError
from mcp.types import Tool
from test_research import QUOTE, ROOT, FixtureModel, FixtureSession

from core.artifacts import merge_snapshot
from core.mcp_host import MCPHost, content
from core.retrieval import bm25, retrieve
from core.skills import Skills
from core.store import Store
from core.task_graph import ResearchGraph
from research import connect


class Packages(unittest.TestCase):
    def test_new_method_loads_its_own_contract_and_resources_without_runtime_changes(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "new-method"
            package.mkdir()
            (package / "SKILL.md").write_text(
                "---\nname: new-method\ndescription: Check a supplied experimental setting.\n---\nUse the supplied setting.\n",
                encoding="utf-8",
            )
            (package / "runtime.json").write_text(
                json.dumps(
                    {
                        "kind": "method",
                        "output_schema": "output.schema.json",
                        "capabilities": ["get_passages"],
                        "references": ["conditions.md"],
                    }
                )
            )
            (package / "output.schema.json").write_text(
                json.dumps({"type": "object", "required": ["condition"]})
            )
            (package / "conditions.md").write_text(
                "Do not equate different test splits."
            )
            runtime = Skills(directory)
            self.assertEqual(runtime.loaded_resources, [])
            body, activation = runtime.activate("new-method", "test extension")
            self.assertIn("different test splits", body)
            runtime.validate("new-method", {"condition": "held-out split"})
            with self.assertRaises(ValidationError):
                runtime.validate("new-method", {})
            runtime.permit("get_passages")
            with self.assertRaises(PermissionError):
                runtime.permit("register_evidence")
            with self.assertRaises(ValueError):
                runtime.resource("new-method", "../outside.md")
            self.assertEqual(activation["kind"], "method")

    def test_failed_branch_import_is_atomic_and_never_accepts_a_forged_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            destination, source = (
                Store(Path(directory) / "destination"),
                Store(Path(directory) / "source"),
            )
            row = source.add_sources(
                [{"title": "Study", "url": "https://example.org/a"}]
            )[0]
            source.document(
                row["id"], [{"text": QUOTE}], "full_text", row["url"], "a" * 64
            )
            evidence = source.register(row["id"], "P0001", QUOTE, "Q1")
            snapshot = deepcopy(source.data)
            snapshot["evidence"][evidence["id"]]["quote"] = (
                "This is fabricated content that does not appear in the saved document."
            )
            before = deepcopy(destination.data)
            with self.assertRaises(ValueError):
                merge_snapshot(destination, snapshot)
            self.assertEqual(before, destination.data)
            maps = merge_snapshot(destination, source.data)
            self.assertIn(evidence["id"], maps["evidence_map"])

    def test_bm25_and_fallback_preserve_exact_text_and_explain_degradation(self):
        paragraphs = [
            {
                "id": "P0001",
                "text": "A bounded document reports performance in a different task.",
            },
            {
                "id": "P0002",
                "text": "The adapter freezes pretrained weights and trains low rank matrices.",
            },
        ]
        document = {
            "source_id": "S1",
            "sha256": "a" * 64,
            "access": "full_text",
            "passages": paragraphs,
        }
        self.assertGreater(
            bm25("freezes weights", [p["text"] for p in paragraphs])[1], 0
        )
        with patch("core.retrieval.dense_scores", side_effect=RuntimeError):
            result = retrieve(document, "freezes weights", 1, "hybrid")
        self.assertEqual(result["passages"], [paragraphs[1]])
        self.assertEqual(result["retrieval"]["effective_mode"], "bm25")
        self.assertTrue(result["retrieval"]["warnings"])


class Capabilities(unittest.IsolatedAsyncioTestCase):
    async def test_capability_routes_to_a_differently_named_tool_and_checks_its_schema(
        self,
    ):
        calls = []

        class Replacement:
            async def list_tools(self):
                return SimpleNamespace(
                    tools=[
                        Tool(
                            name="vendor_specific_reader",
                            inputSchema={"type": "object", "required": ["source_id"]},
                            _meta={"research": {"capability": "read_source"}},
                        )
                    ]
                )

            async def call_tool(self, name, arguments):
                calls.append(name)
                return SimpleNamespace(
                    isError=False,
                    structuredContent={"source_id": arguments["source_id"]},
                )

        host = MCPHost()
        await host.attach("replacement", Replacement())
        self.assertEqual(
            content(await host.call_tool("read_source", {"source_id": "S1"}))[
                "source_id"
            ],
            "S1",
        )
        self.assertEqual(calls, ["vendor_specific_reader"])
        with self.assertRaises(ValidationError):
            await host.call_tool("read_source", {})
        with self.assertRaises(ValueError):
            await host.attach("ambiguous", Replacement())

    async def test_two_real_servers_and_resource_read_use_the_saved_version(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            store = Store(directory)
            source = store.add_sources(
                [{"title": "Scoped result", "url": "https://example.org/scoped"}]
            )[0]
            store.document(
                source["id"], [{"text": QUOTE}], "full_text", source["url"], "a" * 64
            )
            async with connect(directory) as host:
                self.assertEqual(set(host.sessions), {"discovery", "library"})
                self.assertIn("search_passages", host.routes)
                data = content(
                    await host.call_tool(
                        "get_passages",
                        {
                            "source_id": source["id"],
                            "passage_ids": ["P0001"],
                            "document_sha256": "a" * 64,
                        },
                    )
                )
                self.assertEqual(data["mcp_method"], "resources/read")
                self.assertEqual(data["passages"][0]["text"], QUOTE)
                result = await host.call_tool(
                    "register_sources",
                    {
                        "sources": [
                            {"title": "Another", "url": "https://example.org/another"}
                        ]
                    },
                )
                self.assertFalse(result.isError)


class GraphSession(FixtureSession):
    catalog = []

    @asynccontextmanager
    async def fork(self, directory):
        yield GraphSession(directory)

    async def call_tool(self, name, arguments):
        if name == "import_branch":
            return SimpleNamespace(
                isError=False,
                structuredContent=merge_snapshot(self.store, arguments["snapshot"]),
            )
        return await super().call_tool(name, arguments)


class GraphModel(FixtureModel):
    def __init__(self, fail_second=False):
        super().__init__()
        self.fail_second = fail_second
        self.running = self.peak = 0
        self.reads = []

    async def __call__(self, instructions, payload):
        if "subquestions and searches" in instructions:
            tasks = payload.get("subquestions") or [
                {
                    "id": qid,
                    "question": "What does the controlled " + qid + " study show?",
                    "requires_full_text": True,
                    "min_sources": 1,
                    "synthesis_required": False,
                    "criteria": [
                        {"id": qid + ".C1", "description": "Report its scoped result"}
                    ],
                    "skill": "original-paper",
                }
                for qid in ("Q1", "Q2")
            ]
            return {
                "subquestions": tasks,
                "searches": [
                    {
                        "question_id": t["id"],
                        "query": "controlled "
                        + t["id"]
                        + (" followup" if "gaps" in payload else " evidence"),
                    }
                    for t in tasks
                ],
            }
        if "document" in payload:
            qid = payload["target_question_id"]
            self.reads.append(qid)
            if self.fail_second and qid == "Q2":
                raise ValueError("Simulated isolated worker failure")
            self.running += 1
            self.peak = max(self.peak, self.running)
            try:
                await asyncio.sleep(0.06)
                output = await super().__call__(instructions, payload)
                for claim in output["claims"]:
                    claim["question_id"] = qid
                return output
            finally:
                self.running -= 1
        return await super().__call__(instructions, payload)


class GraphExecution(unittest.IsolatedAsyncioTestCase):
    async def test_insufficient_graph_budget_is_visible_as_budget_limited_execution(
        self,
    ):
        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            graph = ResearchGraph(
                "Compare two studies",
                GraphSession(directory),
                GraphModel(),
                lambda *a: None,
                directory,
            )
            graph.research.max_calls = 2
            result = await graph.run()
            self.assertEqual(result["execution"]["status"], "budget_limited")
            self.assertEqual(result["status"], "no_evidence")
            self.assertFalse(result["claims"])

    async def test_synthesis_waits_for_both_dependencies_and_uses_their_registered_evidence(
        self,
    ):
        class SynthesisModel(GraphModel):
            async def __call__(self, instructions, payload):
                if payload.get("mode") == "synthesis":
                    return {
                        "claims": [
                            {
                                "question_id": "Q3",
                                "text": "The two controlled studies report scoped benchmark findings under their respective stated conditions.",
                                "kind": "synthesis",
                                "refs": [r["id"] for r in payload["evidence"]],
                                "numeric_facts": [],
                            }
                        ]
                    }
                output = await super().__call__(instructions, payload)
                if (
                    "subquestions and searches" in instructions
                    and "gaps" not in payload
                ):
                    output["subquestions"].append(
                        {
                            "id": "Q3",
                            "question": "Combine the two study findings",
                            "requires_full_text": True,
                            "min_sources": 2,
                            "synthesis_required": True,
                            "depends_on": ["Q1", "Q2"],
                            "skill": "cross-paper-synthesis",
                            "criteria": [
                                {
                                    "id": "Q3.C1",
                                    "description": "Synthesize both original results",
                                }
                            ],
                        }
                    )
                return output

        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            events = []
            graph = ResearchGraph(
                "Read two studies then synthesize",
                GraphSession(directory),
                SynthesisModel(),
                lambda kind, data: events.append((kind, data)),
                directory,
            )
            result = await graph.run()
            self.assertEqual(result["status"], "complete")
            synthesis = [
                c
                for c in graph.research.state["claims"]
                if c.get("kind") == "synthesis"
            ]
            self.assertEqual(len(synthesis), 1)
            self.assertEqual(len(synthesis[0]["refs"]), 2)
            began = next(
                i
                for i, (k, d) in enumerate(events)
                if k == "task_status"
                and d["task_id"] == "Q3"
                and d["status"] == "running"
            )
            for qid in ("Q1", "Q2"):
                finished = next(
                    i
                    for i, (k, d) in enumerate(events)
                    if k == "task_status"
                    and d["task_id"] == qid
                    and d["status"] == "complete"
                )
                self.assertLess(finished, began)

    async def test_parallel_branches_keep_evidence_and_step_ids_separate(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            model = GraphModel()
            events = []
            graph = ResearchGraph(
                "Compare two controlled studies",
                GraphSession(directory),
                model,
                lambda kind, data: events.append((kind, data)),
                directory,
            )
            result = await graph.run()
            self.assertEqual(result["status"], "complete")
            self.assertEqual(model.peak, 2)
            self.assertEqual(
                {c["question_id"] for c in graph.research.state["claims"]}, {"Q1", "Q2"}
            )
            starts = [
                data["step_index"] for kind, data in events if kind == "tool_start"
            ]
            self.assertEqual(len(starts), len(set(starts)))
            self.assertTrue(
                (Path(directory) / "branches/Q1/attempt-1/tool-state.json").exists()
            )
            self.assertTrue(
                (Path(directory) / "branches/Q2/attempt-1/tool-state.json").exists()
            )

    async def test_resume_retains_completed_branch_and_retries_only_failed_work(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            model = GraphModel(fail_second=True)
            first = ResearchGraph(
                "Compare two controlled studies",
                GraphSession(directory),
                model,
                lambda *a: None,
                directory,
            )
            self.assertEqual((await first.run())["status"], "partial")
            self.assertEqual(model.reads.count("Q1"), 1)
            model.fail_second = False
            resumed = ResearchGraph(
                "Compare two controlled studies",
                GraphSession(directory),
                model,
                lambda *a: None,
                directory,
            )
            self.assertEqual((await resumed.run(resume=True))["status"], "complete")
            self.assertEqual(model.reads.count("Q1"), 1)
            self.assertTrue(
                list((Path(directory) / "graph-history").glob("before-resume-*.json"))
            )
