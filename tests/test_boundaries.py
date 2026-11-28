"""Credential routing, run isolation and failure budgets use real interface boundaries."""

import asyncio
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from test_research import QUOTE, ROOT, FixtureModel, FixtureSession

from core.agent import Research
from core.numeric import (
    bind_json_facts,
    json_numeric_facts,
    numbers,
    numeric_gate,
    relation_gate,
)
from core.sources import fetch, search
from core.store import Store, source_identifiers
from research import connect


class Boundaries(unittest.IsolatedAsyncioTestCase):
    async def test_requested_dataset_cannot_be_replaced_by_a_fork(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            session = FixtureSession(directory)
            research = Research(
                "Inspect the namespace/dataset dataset",
                session,
                FixtureModel(),
                lambda *a: None,
                directory,
            )
            source = session.store.add_sources(
                [
                    {
                        "title": "fork/dataset",
                        "dataset": "fork/dataset",
                        "kind": "dataset",
                        "url": "https://huggingface.co/datasets/fork/dataset",
                    }
                ]
            )[0]
            research.state["sources"][source["id"]] = source
            await research.read([(source["id"], "Q1")])
            self.assertFalse(session.calls)
            self.assertFalse(research.state["claims"])
            self.assertEqual(len(research.state["source_exclusions"]), 1)

    async def test_default_search_window_includes_pre_2015_originals(self):
        fake = AsyncMock(
            return_value=(
                b'{"results":[{"id":"https://openalex.org/W1","title":"Adam: A Method for Stochastic Optimization","publication_year":2014}]}',
                "application/json",
                "https://api.openalex.org/works",
            )
        )
        with (
            patch("core.sources.fetch", fake),
            patch("core.sources._openalex_backoff_until", 0),
            patch("core.sources.search_arxiv", AsyncMock(return_value=[])),
        ):
            rows, _ = await search("Adam A Method for Stochastic Optimization")
        self.assertTrue(
            fake.call_args_list[0]
            .args[1]["filter"]
            .startswith("publication_year:1900-")
        )
        self.assertEqual(rows[0]["year"], 2014)

    async def test_named_original_title_controls_query_and_excludes_secondary(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            session = FixtureSession(directory)
            research = Research(
                "Read the original Primary Study Title",
                session,
                FixtureModel(),
                lambda *a: None,
                directory,
            )
            research.state["subquestions"] = [
                {
                    "id": "Q1",
                    "question": "What does the original establish?",
                    "requires_full_text": True,
                    "min_sources": 1,
                }
            ]
            queue = await research.discover(
                [
                    {
                        "question_id": "Q1",
                        "query": "overloaded mechanism and expected answer 999",
                        "original_title": "Primary Study Title",
                    }
                ]
            )
            self.assertEqual(session.calls[0][1]["query"], "Primary Study Title")
            secondary = session.store.add_sources(
                [
                    {
                        "title": "Unrelated secondary paper",
                        "url": "https://example.org/secondary",
                    }
                ]
            )[0]
            research.state["sources"][secondary["id"]] = secondary
            await research.read([(secondary["id"], "Q1")])
            self.assertFalse(research.state["claims"])
            self.assertEqual(len(research.state["source_exclusions"]), 1)
            self.assertTrue(queue)

    async def test_original_title_cannot_be_invented_by_planner(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            research = Research(
                "Read a specific original paper",
                FixtureSession(directory),
                FixtureModel(),
                lambda *a: None,
                directory,
            )
            research.state["subquestions"] = [{"id": "Q1"}]
            with self.assertRaises(ValueError):
                await research.discover(
                    [
                        {
                            "question_id": "Q1",
                            "query": "a paper",
                            "original_title": "An Invented Original Title",
                        }
                    ]
                )

    async def test_openalex_credential_is_not_put_in_url_or_forwarded_to_redirect(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.host == "api.openalex.org":
                return httpx.Response(
                    302,
                    headers={"location": "https://example.org/public"},
                    request=request,
                )
            return httpx.Response(200, content=b"{}", request=request)

        real_client = httpx.AsyncClient

        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        with (
            patch("core.sources.public_url", AsyncMock()),
            patch("core.sources.setting", return_value="unit-test-key"),
            patch("core.sources.httpx.AsyncClient", side_effect=client),
        ):
            await fetch("https://api.openalex.org/works", {"search": "paper"})
        self.assertEqual(
            requests[0].headers.get("authorization"), "Bearer unit-test-key"
        )
        self.assertNotIn("authorization", requests[1].headers)
        self.assertTrue(all("unit-test-key" not in str(r.url) for r in requests))

    async def test_real_mcp_sessions_cannot_read_each_others_sources(self):
        with (
            tempfile.TemporaryDirectory(dir=ROOT / "runs") as first,
            tempfile.TemporaryDirectory(dir=ROOT / "runs") as second,
        ):
            sources = []
            for directory, label in ((first, "A"), (second, "B")):
                store = Store(directory)
                source = store.add_sources(
                    [
                        {
                            "title": "Private run " + label,
                            "url": "https://example.org/" + label,
                        }
                    ]
                )[0]
                store.document(
                    source["id"],
                    [{"text": QUOTE}],
                    "full_text",
                    source["url"],
                    label.lower() * 64,
                )
                sources.append(source)
            async with connect(first) as one, connect(second) as two:
                good, rejected = await asyncio.gather(
                    one.call_tool(
                        "get_passages",
                        {"source_id": sources[0]["id"], "passage_ids": ["P0001"]},
                    ),
                    two.call_tool(
                        "get_passages",
                        {"source_id": sources[0]["id"], "passage_ids": ["P0001"]},
                    ),
                )
                self.assertFalse(good.isError)
                self.assertTrue(rejected.isError)

    async def test_expired_overall_deadline_makes_no_model_request(self):
        model = FixtureModel()
        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            research = Research(
                "Check budget",
                FixtureSession(directory),
                model,
                lambda *a: None,
                directory,
            )
            research.deadline = time.monotonic() - 1
            result = await research.run()
        self.assertEqual(result["execution"]["status"], "budget_limited")
        self.assertEqual(result["stats"]["model_calls"], 0)
        self.assertFalse(result["claims"])


class StateDirectory(unittest.TestCase):
    def test_literal_json_binding_replaces_redundant_model_format_guess(self):
        quote = 'A train split sample has {"label": 0, "row_idx": 1}.'
        claim = "In the train split sample, the label field has value 0."
        proposed = [
            {
                "subject": "label",
                "metric": "label",
                "value": "0",
                "unit": "",
                "condition": '"split":"train"',
                "source_span": '"label": 0',
                "claim_span": "label field has value 0",
            }
        ]
        facts = bind_json_facts(claim, quote, proposed)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["origin"], "deterministic_json_field_binding")
        self.assertTrue(relation_gate(claim, quote, facts)["ok"])
        wrong = claim.replace("value 0", "value 1")
        self.assertFalse(
            relation_gate(wrong, quote, bind_json_facts(wrong, quote, []))["ok"]
        )

    def test_exact_context_binds_model_size_without_borrowing_an_outcome(self):
        quote = "Compared to GPT-3 175B fine-tuned with Adam, LoRA reduces trainable parameters by 10,000 times."
        fact = {
            "subject": "LoRA",
            "metric": "trainable parameters",
            "value": "10000",
            "unit": "times",
            "condition": "Compared to GPT-3 175B fine-tuned with Adam",
            "source_span": "LoRA reduces trainable parameters by 10,000 times",
            "claim_span": "LoRA reduces trainable parameters by 10,000 times",
        }
        self.assertTrue(relation_gate(quote, quote, [fact])["ok"])
        self.assertFalse(
            relation_gate(quote.replace("175B", "170B"), quote, [fact])["ok"]
        )

    def test_json_binding_preserves_field_identity(self):
        quote = 'The bounded sample contains {"label": 0, "row_idx": 1}.'
        claim = "The bounded sample label field has value 0."
        self.assertTrue(
            relation_gate(claim, quote, json_numeric_facts(claim, quote))["ok"]
        )
        wrong = "The bounded sample label field has value 1."
        self.assertFalse(
            relation_gate(wrong, quote, json_numeric_facts(wrong, quote))["ok"]
        )

    def test_model_identifiers_and_ordinals_are_checked_without_numeric_bindings(self):
        quote = "GPT-3 uses estimates of the 1st and 2nd moments."
        self.assertFalse(numbers(quote))
        self.assertTrue(
            relation_gate(
                "GPT-3 uses estimates of the 1st and 2nd moments.", quote, []
            )["ok"]
        )
        self.assertFalse(
            numeric_gate("GPT-4 uses estimates of the 1st moment.", quote)["ok"]
        )

    def test_unicode_ligature_comparison_does_not_rewrite_source_spans(self):
        quote = "LoRA reduces trainable parameters by 10,000 times when ﬁne-tuned."
        claim = "LoRA reduces trainable parameters by 10,000 times when fine-tuned."
        fact = {
            "subject": "LoRA",
            "metric": "trainable parameters",
            "value": "10000",
            "unit": "times",
            "condition": "fine-tuned",
            "source_span": quote.rstrip("."),
            "claim_span": claim.rstrip("."),
        }
        self.assertTrue(relation_gate(claim, quote, [fact])["ok"])
        self.assertFalse(
            relation_gate(claim, quote, [{**fact, "source_span": claim.rstrip(".")}])[
                "ok"
            ]
        )

    def test_arxiv_doi_and_openreview_links_supply_readable_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            source = store.add_sources(
                [
                    {
                        "title": "Named original paper",
                        "doi": "https://doi.org/10.48550/arXiv.2106.09685",
                        "url": "https://doi.org/10.48550/arXiv.2106.09685",
                        "reading_urls": ["https://openreview.net/forum?id=nZeVKeeFYf9"],
                    }
                ]
            )[0]
        self.assertEqual(source_identifiers(source)["arxiv"], "2106.09685")
        self.assertEqual(source["reading_urls"][0], "https://arxiv.org/pdf/2106.09685")
        self.assertIn(
            "https://openreview.net/pdf?id=nZeVKeeFYf9", source["reading_urls"]
        )

    def test_arxiv_reading_preserves_an_explicit_version(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Store(directory).add_sources(
                [
                    {
                        "title": "Versioned original",
                        "url": "https://arxiv.org/abs/2201.12345v1",
                    }
                ]
            )[0]
        self.assertEqual(source["identifiers"]["arxiv"], "2201.12345")
        self.assertEqual(
            source["reading_urls"][0], "https://arxiv.org/pdf/2201.12345v1"
        )

    def test_dataset_namespace_identifier_has_direct_lookup_fallback(self):
        import public_sources

        with patch(
            "public_sources.read",
            side_effect=[
                [],
                {"id": "namespace/dataset", "private": False, "gated": False},
            ],
        ) as api:
            result = public_sources.search_datasets("namespace/dataset")
        self.assertEqual(result["results"][0]["id"], "namespace/dataset")
        self.assertTrue(api.call_args_list[1].args[0].endswith("/namespace/dataset"))

    def test_mcp_rejects_state_directory_outside_runs_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "should-not-be-created"
            process = subprocess.run(
                [sys.executable, str(ROOT / "data_mcp.py")],
                cwd=ROOT,
                env={**os.environ, "RESEARCH_RUN_DIR": str(destination)},
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("MCP run state must stay inside", process.stderr)
            self.assertFalse(destination.exists())
