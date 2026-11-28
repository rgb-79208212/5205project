"""QA regression cases: publication rules, source identity and research contracts."""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_research import QUOTE, ROOT, FixtureModel, FixtureSession

from core.agent import Research
from core.coverage import coverage
from core.numeric import calculate, relation_gate
from core.store import Store
from core.synthesis import comparison_checks
from core.verification import claim_checks


class MultiSession(FixtureSession):
    """Clearly simulated primary studies with two traceable measurements."""

    async def call_tool(self, name, args):
        if name == "search_papers":
            self.calls.append((name, args))
            sources = self.store.add_sources(
                [
                    {
                        "title": "Study " + label,
                        "url": "https://example.org/study-" + label,
                        "model_label": label,
                    }
                    for label in ("A", "B")
                ]
            )
            return SimpleNamespace(
                isError=False,
                structuredContent={
                    "results": sources,
                    "source_catalog": self.store.data["sources"],
                },
            )
        if name == "read_source":
            self.calls.append((name, args))
            label = self.store.data["sources"][args["source_id"]]["model_label"]
            score = "90" if label == "A" else "85"
            quote = f"For QA on TestSet using fixed budget, Model {label} achieved accuracy of {score}%."
            data = self.store.document(
                args["source_id"],
                [{"text": quote, "locator": "Results"}],
                "full_text",
                "https://example.org/" + label,
                label.lower() * 64,
            )
            data["title"] = "Study " + label
            return SimpleNamespace(isError=False, structuredContent=data)
        return await super().call_tool(name, args)


class MultiModel(FixtureModel):
    def __init__(self, kind="synthesis", forged=False):
        super().__init__()
        self.kind, self.forged = kind, forged

    async def __call__(self, instructions, payload):
        if payload.get("mode") == "synthesis":
            parents = payload["direct_claims"]
            result = {
                "question_id": "Q1",
                "kind": self.kind,
                "text": "Across these studies, accuracy depends on the evaluated model under the stated conditions.",
                "refs": [c["refs"][0] for c in parents[:2]],
                "numeric_facts": [],
            }
            if self.forged:
                result["refs"][1] = "E_FAKE"
            if self.kind == "derived":
                result["calculation"] = {
                    "operation": "difference",
                    "input_claim_ids": [c["id"] for c in parents[:2]],
                    "input_fact_indices": [0, 0],
                }
            return {"claims": [result]}
        if "document" in payload:
            quote = payload["document"]["passages"][0]["text"]
            label = "A" if "Model A" in quote else "B"
            value = "90" if label == "A" else "85"
            span = f"Model {label} achieved accuracy of {value}%"
            return {
                "claims": [
                    {
                        "question_id": "Q1",
                        "text": quote,
                        "passage_id": "P0001",
                        "quote": quote,
                        "numeric_facts": [
                            {
                                "subject": "Model " + label,
                                "metric": "accuracy",
                                "value": value,
                                "unit": "%",
                                "condition": "TestSet",
                                "source_span": span,
                                "claim_span": span,
                            }
                        ],
                    }
                ]
            }
        result = await super().__call__(instructions, payload)
        if "subquestions and searches" in instructions:
            result["subquestions"][0].update(min_sources=2, synthesis_required=True)
        return result


class SourceRules(unittest.TestCase):
    def test_versions_share_study_identity_and_cannot_fill_two_study_requirement(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            sources = store.add_sources(
                [
                    {
                        "title": "Original preprint",
                        "url": "https://arxiv.org/abs/2201.12345v1",
                    },
                    {
                        "title": "Revised publication",
                        "url": "https://doi.org/10.1234/p",
                        "doi": "10.1234/p",
                        "reading_urls": ["https://arxiv.org/pdf/2201.12345v2.pdf"],
                    },
                ]
            )
            self.assertNotEqual(sources[0]["id"], sources[1]["id"])
            self.assertEqual(sources[0]["study_id"], sources[1]["study_id"])
            state = {
                "subquestions": [
                    {"id": "Q1", "min_sources": 2, "requires_full_text": True}
                ],
                "sources": store.data["sources"],
                "claims": [],
                "evidence": {},
            }
            for index, source in enumerate(sources):
                ref = "E" + str(index)
                state["evidence"][ref] = {"quote": QUOTE, "access": "full_text"}
                state["claims"].append(
                    {
                        "id": "C" + str(index),
                        "question_id": "Q1",
                        "source_id": source["id"],
                        "refs": [ref],
                        "status": "supported",
                        "access": "full_text",
                        "numeric_gate": {"ok": True},
                    }
                )
            self.assertTrue(coverage(state))

    def test_neighbor_passages_keep_the_requested_document_version(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            source = store.add_sources(
                [{"title": "Paper", "url": "https://example.org/p"}]
            )[0]
            store.document(
                source["id"],
                [
                    {"text": "Condition before"},
                    {"text": QUOTE},
                    {"text": "Limitation after"},
                ],
                "full_text",
                source["url"],
                "a" * 64,
            )
            store.document(
                source["id"],
                [{"text": "A newer version"}],
                "full_text",
                source["url"],
                "b" * 64,
            )
            result = store.passages(source["id"], ["P0002"], "a" * 64, 1)
            self.assertEqual(len(result["passages"]), 3)
            self.assertEqual(result["requested_passage_ids"], ["P0002"])

    def test_no_doi_original_content_can_be_registered(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            source = store.add_sources(
                [
                    {
                        "title": "Official technical report",
                        "url": "https://example.org/report",
                    }
                ]
            )[0]
            self.assertIsNone(source["identifiers"]["doi"])
            store.document(
                source["id"], [{"text": QUOTE}], "full_text", source["url"], "a" * 64
            )
            evidence = store.register(source["id"], "P0001", QUOTE, "Q1")
            self.assertEqual(evidence["access"], "full_text")

    def test_doi_metadata_is_not_substantive_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            source = store.add_sources(
                [
                    {
                        "title": "Metadata record",
                        "doi": "10.1234/test",
                        "url": "https://doi.org/10.1234/test",
                    }
                ]
            )[0]
            store.document(
                source["id"],
                [{"text": QUOTE}],
                "metadata_only",
                source["url"],
                "a" * 64,
            )
            with self.assertRaises(ValueError):
                store.register(source["id"], "P0001", QUOTE, "Q1")

    def test_pending_claim_does_not_pass_publication(self):
        claim = {
            "id": "C1",
            "status": "pending",
            "refs": ["E1"],
            "numeric_gate": {"ok": True},
        }
        checks = claim_checks(claim, {"E1": {"quote": QUOTE, "access": "full_text"}})
        self.assertFalse(checks["publishable"])
        self.assertEqual(checks["semantic"], "pending")


class NumericRelations(unittest.TestCase):
    def test_swapped_subjects_are_rejected_despite_matching_number_set(self):
        quote = "Model A achieved accuracy of 90%; Model B achieved accuracy of 85% on TestSet."
        claim = "Model A achieved accuracy of 85% on TestSet."
        fact = {
            "subject": "Model A",
            "metric": "accuracy",
            "value": "85",
            "unit": "%",
            "condition": "TestSet",
            "source_span": quote.rstrip("."),
            "claim_span": claim.rstrip("."),
        }
        result = relation_gate(claim, quote, [fact])
        self.assertFalse(result["ok"])
        self.assertFalse(result["missing"])
        self.assertTrue(result["relation_errors"])

    def test_local_numeric_binding_passes_and_missing_binding_fails(self):
        quote = "Model A achieved accuracy of 90% on TestSet."
        fact = {
            "subject": "Model A",
            "metric": "accuracy",
            "value": "90",
            "unit": "%",
            "condition": "TestSet",
            "source_span": quote.rstrip("."),
            "claim_span": quote.rstrip("."),
        }
        self.assertTrue(relation_gate(quote, quote, [fact])["ok"])
        self.assertFalse(relation_gate(quote, quote, [])["ok"])
        changed = {**fact, "condition": "OtherSet"}
        self.assertFalse(relation_gate(quote, quote, [changed])["ok"])

    def test_closer_subject_cannot_be_hidden_in_a_long_span(self):
        quote = "Model A scored 90% and Model B scored 85% on TestSet"
        claim = "Model A scored 85% on TestSet"
        fact = {
            "subject": "Model A",
            "metric": "scored",
            "value": "85",
            "unit": "%",
            "condition": "TestSet",
            "source_span": quote,
            "claim_span": claim,
        }
        self.assertFalse(relation_gate(claim, quote, [fact])["ok"])

    def test_calculation_operations_and_zero_baseline(self):
        self.assertEqual(calculate("difference", ["90", "85"]), 5)
        self.assertEqual(calculate("percent_change", ["120", "100"]), 20)
        with self.assertRaises(ValueError):
            calculate("ratio", ["1", "0"])
        with self.assertRaises(ValueError):
            calculate("eval", ["1", "2"])


class StatusRules(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, model, **options):
        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            research = Research(
                "Compare evidence",
                FixtureSession(directory),
                model,
                lambda *a: None,
                directory,
                **options,
            )
            result = await research.run()
            return result, research.state

    async def test_initial_planning_failure_is_recorded_without_publishing(self):
        async def failed_planner(instructions, payload):
            raise ValueError("Simulated planner failure")

        result, state = await self.run_case(failed_planner)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(result["status"], "no_evidence")
        self.assertFalse(result["claims"])
        self.assertEqual(state["errors"][0]["stage"], "planner")

    async def test_cyclic_task_dependencies_fail_without_publishing(self):
        class CyclicPlanner(FixtureModel):
            async def __call__(self, instructions, payload):
                result = await super().__call__(instructions, payload)
                if "subquestions and searches" in instructions:
                    result["subquestions"][0]["depends_on"] = ["Q2"]
                    result["subquestions"].append(
                        {
                            **result["subquestions"][0],
                            "id": "Q2",
                            "depends_on": ["Q1"],
                            "criteria": [
                                {
                                    "id": "Q2.C1",
                                    "description": "Another requested aspect",
                                }
                            ],
                        }
                    )
                return result

        result, state = await self.run_case(CyclicPlanner(), max_rounds=1)
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertFalse(result["claims"])
        self.assertIn("cycle", state["errors"][0]["reason"])

    async def test_schema_retry_includes_the_contract_and_records_error_path(self):
        class RepairedPlanner(FixtureModel):
            async def __call__(self, instructions, payload):
                result = await super().__call__(instructions, payload)
                if (
                    "subquestions and searches" in instructions
                    and "previous_output" not in payload
                ):
                    del result["subquestions"][0]["criteria"]
                return result

        result, state = await self.run_case(RepairedPlanner(), max_rounds=1)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(state["contract_errors"][0]["path"], ["subquestions", 0])
        self.assertEqual(state["contract_errors"][0]["skill"], "planner")
        self.assertEqual(result["stats"]["model_calls"], 5)

    async def test_diagnostic_chart_is_not_substantive_evidence(self):
        class EmptyReader(FixtureModel):
            async def __call__(self, instructions, payload):
                result = await super().__call__(instructions, payload)
                if "subquestions and searches" in instructions:
                    result["subquestions"].append(
                        {
                            **result["subquestions"][0],
                            "id": "Q2",
                            "criteria": [
                                {
                                    "id": "Q2.C1",
                                    "description": "Another required aspect",
                                }
                            ],
                        }
                    )
                if "document" in payload:
                    return {"claims": []}
                return result

        result, _ = await self.run_case(EmptyReader(), max_rounds=1)
        self.assertTrue(result["charts"])
        self.assertEqual(result["status"], "no_evidence")

    async def test_same_quote_can_support_distinct_facts_without_duplicate_registration(
        self,
    ):
        class TwoFacts(FixtureModel):
            async def __call__(self, instructions, payload):
                result = await super().__call__(instructions, payload)
                if "document" in payload:
                    first = {
                        **result["claims"][0],
                        "text": "The experiment reports a measured accuracy on this specific benchmark.",
                        "numeric_facts": [],
                    }
                    second = {
                        **first,
                        "text": "The experiment was evaluated under the stated conditions.",
                    }
                    result["claims"] = [first, second]
                return result

        result, state = await self.run_case(TwoFacts(), max_rounds=1)
        self.assertEqual(len(result["claims"]), 2)
        self.assertEqual(len(state["evidence"]), 1)
        self.assertEqual(
            sum(
                e.get("tool") == "register_evidence"
                for e in state["trace"]
                if e["event"] == "tool_start"
            ),
            1,
        )

    async def test_source_count_cannot_replace_unmet_criterion(self):
        class MissingLimitations(FixtureModel):
            async def __call__(self, instructions, payload):
                result = await super().__call__(instructions, payload)
                if "fresh-context evidence auditor" in instructions:
                    result["coverage"][0].update(
                        status="missing",
                        reason="The limitations requested by the criterion are absent",
                    )
                return result

        result, state = await self.run_case(MissingLimitations(), max_rounds=1)
        self.assertTrue(result["verification"]["ok"])
        self.assertEqual(result["status"], "partial")
        self.assertTrue(any(g["kind"] == "criterion" for g in state["gaps"]))

    async def test_incomplete_audit_never_publishes_new_claims(self):
        class MissingCoverage(FixtureModel):
            async def __call__(self, instructions, payload):
                result = await super().__call__(instructions, payload)
                if "fresh-context evidence auditor" in instructions:
                    result["coverage"] = []
                return result

        result, state = await self.run_case(MissingCoverage(), max_rounds=1)
        self.assertFalse(result["claims"])
        self.assertEqual(result["execution"]["status"], "failed")
        self.assertEqual(state["claims"][0]["status"], "pending")

    async def test_replanning_preserves_original_criteria_and_links_actions(self):
        result, state = await self.run_case(
            FixtureModel(first_audit="partial"), max_rounds=2
        )
        self.assertEqual(result["status"], "complete")
        followup = [a for a in state["research_actions"] if a["round"] == 2]
        self.assertTrue(followup)
        self.assertIn("Q1.C1", followup[0]["gap_ids"])
        self.assertEqual(state["subquestions"][0]["criteria"][0]["id"], "Q1.C1")

    async def test_approved_partial_answer_is_not_an_execution_failure(self):
        class TwoQuestions(FixtureModel):
            async def __call__(self, instructions, payload):
                result = await super().__call__(instructions, payload)
                if "subquestions and searches" in instructions:
                    result["subquestions"].append(
                        {
                            "id": "Q2",
                            "question": "What remains unknown?",
                            "requires_full_text": True,
                            "min_sources": 1,
                            "synthesis_required": False,
                            "criteria": [
                                {
                                    "id": "Q2.C1",
                                    "description": "Identify unanswered limitations",
                                }
                            ],
                        }
                    )
                return result

        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            result = await Research(
                "Compare evidence",
                FixtureSession(directory),
                TwoQuestions(),
                lambda *a: None,
                directory,
                max_rounds=1,
            ).run()
        self.assertEqual(result["completion"]["status"], "partial")
        self.assertTrue(result["verification"]["ok"])
        self.assertEqual(result["execution"]["status"], "budget_limited")
        self.assertTrue(result["verification"]["checks"][0]["publishable"])


class MultiEvidence(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, model):
        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            research = Research(
                "Compare the studies",
                MultiSession(directory),
                model,
                lambda *a: None,
                directory,
                max_rounds=1,
            )
            result = await research.run()
            return result, research.state

    async def test_new_synthesis_is_audited_after_direct_facts(self):
        result, state = await self.run_case(MultiModel())
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(state["audits"]), 2)
        synthesis = [c for c in result["claim_registry"] if c["kind"] == "synthesis"]
        self.assertEqual(len(synthesis), 1)
        self.assertEqual(len(synthesis[0]["refs"]), 2)
        self.assertTrue(synthesis[0]["checks"]["publishable"])
        registry_row = next(
            row
            for row in result["comparison_table"]["rows"]
            if row[1] == synthesis[0]["id"]
        )
        self.assertTrue(all(ref in registry_row[2] for ref in synthesis[0]["refs"]))

    async def test_synthesis_reuses_approved_facts_from_dependency_questions(self):
        class CrossQuestionModel(MultiModel):
            async def __call__(self, instructions, payload):
                result = await super().__call__(instructions, payload)
                if "subquestions and searches" in instructions:
                    result["subquestions"] = [
                        {
                            "id": qid,
                            "question": description,
                            "requires_full_text": True,
                            "min_sources": 2 if qid == "Q3" else 1,
                            "synthesis_required": qid == "Q3",
                            "depends_on": ["Q1", "Q2"] if qid == "Q3" else [],
                            "criteria": [
                                {"id": qid + ".C1", "description": description}
                            ],
                        }
                        for qid, description in [
                            ("Q1", "Describe the first study"),
                            ("Q2", "Describe the second study"),
                            ("Q3", "Synthesize both primary studies"),
                        ]
                    ]
                    result["searches"] = [
                        {"question_id": "Q1", "query": "study A"},
                        {"question_id": "Q2", "query": "study B"},
                    ]
                elif "document" in payload:
                    for claim in result["claims"]:
                        claim["question_id"] = payload["target_question_id"]
                elif payload.get("mode") == "synthesis":
                    for claim in result["claims"]:
                        claim["question_id"] = payload["target_question_id"]
                return result

        class RankedSession(MultiSession):
            async def call_tool(self, name, args):
                result = await super().call_tool(name, args)
                if name == "search_papers" and "study B" in args["query"]:
                    result.structuredContent["results"].reverse()
                return result

        with tempfile.TemporaryDirectory(dir=ROOT / "runs") as directory:
            research = Research(
                "Compare both studies",
                RankedSession(directory),
                CrossQuestionModel(),
                lambda *a: None,
                directory,
                max_rounds=1,
            )
            result = await research.run()
        self.assertEqual(result["status"], "complete")
        combined = next(
            c for c in research.state["claims"] if c.get("kind") == "synthesis"
        )
        self.assertEqual(combined["question_id"], "Q3")
        parents = {c["id"]: c for c in research.state["claims"]}
        self.assertTrue(
            any(
                parents[cid]["question_id"] != "Q3"
                for cid in combined["parent_claim_ids"]
            )
        )
        self.assertEqual(len(combined["source_ids"]), 2)

    async def test_synthesis_cannot_invent_evidence(self):
        result, state = await self.run_case(MultiModel(forged=True))
        self.assertEqual(result["status"], "partial")
        self.assertFalse(any(c.get("kind") == "synthesis" for c in state["claims"]))
        self.assertTrue(any(e["stage"] == "synthesis" for e in state["errors"]))

    async def test_calculated_difference_uses_approved_inputs_and_is_reaudited(self):
        result, state = await self.run_case(MultiModel(kind="derived"))
        self.assertEqual(result["status"], "complete")
        derived = next(c for c in result["claim_registry"] if c["kind"] == "derived")
        self.assertEqual(derived["calculation"]["value"], "5")
        self.assertEqual(derived["calculation"]["unit"], "percentage points")
        self.assertIn("5 percentage points", derived["text"])
        self.assertEqual(len(state["audits"]), 2)

    def test_different_conditions_cannot_be_validated_as_comparable_conflict(self):
        claims = [{"id": "C1", "refs": ["E1"]}, {"id": "C2", "refs": ["E2"]}]
        evidence = {
            "E1": {"quote": "QA TestA accuracy fixed budget"},
            "E2": {"quote": "QA TestB accuracy fixed budget"},
        }
        comparison = {
            "claim_ids": ["C1", "C2"],
            "relationship": "comparable_conflict",
            "conditions": [
                {
                    "task": "QA",
                    "dataset": "TestA",
                    "metric": "accuracy",
                    "protocol": "fixed budget",
                },
                {
                    "task": "QA",
                    "dataset": "TestB",
                    "metric": "accuracy",
                    "protocol": "fixed budget",
                },
            ],
            "reason": "The reported numbers differ",
        }
        checked = comparison_checks([comparison], claims, evidence)[0]
        self.assertEqual(checked["relationship"], "insufficient_context")
        comparison["relationship"] = "different_conditions"
        self.assertEqual(
            comparison_checks([comparison], claims, evidence)[0]["relationship"],
            "different_conditions",
        )


class DiscoveryFallback(unittest.IsolatedAsyncioTestCase):
    async def test_arxiv_fallback_discovers_readable_source_without_doi(self):
        import httpx

        from core.sources import search

        atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2201.12345v2</id><title>Example original study</title><published>2022-01-01T00:00:00Z</published><summary>Original method and its evaluated scope.</summary></entry></feed>"""
        response = httpx.Response(
            429, request=httpx.Request("GET", "https://api.openalex.org/works")
        )
        fetch = AsyncMock(
            side_effect=[
                httpx.HTTPStatusError(
                    "Rate limited", request=response.request, response=response
                ),
                (
                    b'{"message":{"items":[]}}',
                    "application/json",
                    "https://api.crossref.org/works",
                ),
                (atom, "application/atom+xml", "https://export.arxiv.org/api/query"),
            ]
        )
        with (
            patch("core.sources.fetch", fetch),
            patch("core.sources._openalex_backoff_until", 0),
            patch("core.sources._last_arxiv_request", 0),
        ):
            rows, errors = await search("Example original study", 2015, 2026)
        self.assertEqual(rows[0]["index"], "arXiv")
        self.assertIsNone(rows[0]["doi"])
        self.assertEqual(
            rows[0]["reading_urls"][0], "https://arxiv.org/pdf/2201.12345v2"
        )
        self.assertIn("OpenAlex: 429", errors)
