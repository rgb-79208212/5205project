"""Bounded research DAG: isolated workers, dependency synthesis and explicit resume."""

import asyncio
import json
import time
from copy import deepcopy

from research import setting

from .agent import BudgetExceeded, Research
from .mcp_host import content
from .verification import claim_checks


class ResearchGraph:
    def __init__(self, question, session, model, emit, directory, parallelism=None):
        self.research = Research(question, session, model, emit, directory)
        self.session, self.model = session, model
        self.parallelism = max(
            1, min(2, int(parallelism or setting("RESEARCH_PARALLELISM", "2")))
        )
        self.step_ids = {}

    def event(self, kind, data):
        self.research.event(kind, data)

    def branch_event(self, qid, kind, data):
        if kind == "live_result":
            return
        if kind in {"tool_start", "tool_end"}:
            key = (qid, data["step_index"])
            if kind == "tool_start":
                self.research.state["tool_calls"] += 1
                self.step_ids[key] = self.research.state["tool_calls"]
            data = {**data, "step_index": self.step_ids.get(key, 0)}
        self.event(kind, {**data, "task_id": qid})

    def update_node(self, node, status, **details):
        elapsed = round((time.monotonic() - self.research.started) * 1000)
        if status == "running":
            details["started_ms"] = elapsed
        elif status not in {"pending"}:
            details["finished_ms"] = elapsed
        node.update(status=status, **details)
        self.event("task_status", {"task_id": node["id"], "status": status, **details})

    async def worker(self, node, allowance):
        research = self.research
        qid = node["id"]
        attempt = len(node["attempts"]) + 1
        directory = research.directory / "branches" / qid / f"attempt-{attempt}"
        node["attempts"].append(
            {
                "directory": str(directory.relative_to(research.directory)),
                "status": "running",
            }
        )
        self.update_node(node, "running")
        branch = None
        try:
            async with self.session.fork(directory) as client:
                branch = Research(
                    research.question,
                    client,
                    self.model,
                    lambda kind, data: self.branch_event(qid, kind, data),
                    directory,
                    max_calls=allowance,
                )
                branch.deadline = research.deadline
                task = deepcopy(node["task"])
                task["depends_on"] = []
                plan = {
                    "subquestions": [task],
                    "searches": node["searches"],
                    "read_requests": node.get("read_requests", []),
                }
                result = await branch.run(prepared_plan=plan)
            node["attempts"][-1]["status"] = result["status"]
            return branch
        except Exception as exc:
            node["attempts"][-1].update(status="failed", error_type=type(exc).__name__)
            self.update_node(node, "failed", error_type=type(exc).__name__)
            research.state["errors"].append(
                {"stage": "worker", "task_id": qid, "type": type(exc).__name__}
            )
            return branch

    async def merge(self, node, branch):
        if branch is None:
            return
        state = branch.state
        research = self.research
        # Source/quote IDs are validated and, where necessary, remapped by the library.
        path = branch.directory / "tool-state.json"
        snapshot = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {"sources": {}, "document_versions": {}, "evidence": {}}
        )
        transferred = await self.session.call_tool(
            "import_branch", {"snapshot": snapshot}
        )
        if transferred.isError:
            raise ValueError("Library rejected branch artifacts")
        maps = content(transferred)
        research.state["sources"].update(maps["sources"])
        research.state["evidence"].update(maps["evidence"])
        claim_ids = {}
        for item in state["claims"]:
            claim = deepcopy(item)
            claim["refs"] = [maps["evidence_map"][r] for r in claim["refs"]]
            claim["source_ids"] = [
                maps["source_map"][sid]
                for sid in claim.get("source_ids", [claim["source_id"]])
            ]
            claim["source_id"] = maps["source_map"][claim["source_id"]]
            for fact in claim.get("numeric_facts", []):
                if fact.get("evidence_id"):
                    fact["evidence_id"] = maps["evidence_map"][fact["evidence_id"]]
            existing = next(
                (
                    c
                    for c in research.state["claims"]
                    if c["question_id"] == claim["question_id"]
                    and c["text"] == claim["text"]
                    and c["refs"] == claim["refs"]
                ),
                None,
            )
            identifier = (
                existing["id"]
                if existing
                else "C" + str(len(research.state["claims"]) + 1)
            )
            claim_ids[item["id"]] = identifier
            claim.update(id=identifier, worker_id=node["id"])
            if existing:
                existing.update(claim)
            else:
                research.state["claims"].append(claim)
        for key, review in state["criteria_reviews"].items():
            research.state["criteria_reviews"][key] = {
                **review,
                "claim_ids": [
                    claim_ids[c] for c in review["claim_ids"] if c in claim_ids
                ],
            }
        for sid, document in state["documents"].items():
            research.state["documents"][maps["source_map"][sid]] = document
        for key in (
            "searches",
            "research_actions",
            "audits",
            "errors",
            "activations",
            "model_timings",
            "contract_errors",
        ):
            research.state[key].extend(state[key])
        research.state["model_calls"] += state["model_calls"]
        research.state.setdefault("worker_protocol_requests", []).extend(
            state.get("protocol_requests", [])
        )
        research.state["round"] = max(research.state["round"], state["round"])
        research.activated = list(
            dict.fromkeys([*research.activated, *branch.activated])
        )
        usable = sum(
            claim_checks(c, state["evidence"])["publishable"] for c in state["claims"]
        )
        status = state.get("result", {}).get("status", "failed")
        if node["status"] == "failed":
            status = "partial" if usable else "failed"
        self.update_node(node, status, usable_claims=usable)
        node["stop_reason"] = state["stop_reason"]

    async def synthesize_node(self, node):
        research = self.research
        self.update_node(node, "running")
        for attempt in range(min(2, research.max_rounds)):
            node["synthesis_attempts"] = attempt + 1
            added = await research.synthesize([node["id"]])
            if added:
                await research.audit()
            missing = [g for g in research.coverage() if g["question_id"] == node["id"]]
            if not missing or not added:
                break
            self.event(
                "graph_replanned",
                {
                    "task_id": node["id"],
                    "reason": "Revise synthesis against specific audit gaps",
                    "gaps": missing,
                },
            )
        self.update_node(
            node,
            "partial" if missing else "complete",
            usable_claims=sum(
                c["question_id"] == node["id"]
                and claim_checks(c, research.state["evidence"])["publishable"]
                for c in research.state["claims"]
            ),
        )

    async def run(self, resume=False):
        research = self.research
        try:
            if resume:
                path = research.directory / "research-state.json"
                previous = json.loads(path.read_text(encoding="utf-8"))
                if (
                    previous["question"] != research.question
                    or "task_graph" not in previous
                ):
                    raise ValueError("Resume requires this graph's original question")
                history = research.directory / "graph-history"
                history.mkdir(exist_ok=True)
                (history / f"before-resume-{time.time_ns()}.json").write_text(
                    json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                research.state = previous
                research.max_calls += previous["tool_calls"]
                research.activated = list(
                    previous.get("result", {}).get("skills", {}).get("activated", [])
                )
                for node in previous["task_graph"]["nodes"]:
                    if node["status"] != "complete":
                        node["status"] = "pending"
                self.event(
                    "graph_resumed",
                    {
                        "retained_tasks": [
                            n["id"]
                            for n in previous["task_graph"]["nodes"]
                            if n["status"] == "complete"
                        ]
                    },
                )
            else:
                proposal = await research.initialize()
                nodes = []
                for task in research.state["subquestions"]:
                    nodes.append(
                        {
                            "id": task["id"],
                            "task": deepcopy(task),
                            "depends_on": task["depends_on"],
                            "status": "pending",
                            "attempts": [],
                            "searches": [
                                s
                                for s in proposal["searches"]
                                if s["question_id"] == task["id"]
                            ],
                            "read_requests": [
                                r
                                for r in proposal.get("read_requests", [])
                                if r["question_id"] == task["id"]
                            ],
                        }
                    )
                research.state["task_graph"] = {
                    "version": 1,
                    "parallelism": self.parallelism,
                    "nodes": nodes,
                }
            graph = research.state["task_graph"]
            graph["parallelism"] = self.parallelism
            self.event(
                "graph_planned",
                {"nodes": graph["nodes"], "parallelism": self.parallelism},
            )
            while any(n["status"] == "pending" for n in graph["nodes"]):
                if time.monotonic() >= research.deadline:
                    raise TimeoutError("Graph time budget exhausted")
                by_id = {n["id"]: n for n in graph["nodes"]}
                ready = [
                    n
                    for n in graph["nodes"]
                    if n["status"] == "pending"
                    and all(
                        by_id[d]["status"] in {"complete", "partial"}
                        and by_id[d].get("usable_claims", 0)
                        for d in n["depends_on"]
                    )
                ]
                if not ready:
                    for node in graph["nodes"]:
                        if node["status"] == "pending":
                            self.update_node(
                                node,
                                "blocked",
                                reason="Prerequisite produced no approved evidence",
                            )
                    break
                direct = [n for n in ready if not n["task"]["synthesis_required"]][
                    : self.parallelism
                ]
                if direct:
                    remaining = research.max_calls - research.state["tool_calls"]
                    unfinished = sum(
                        n["status"] == "pending" and not n["task"]["synthesis_required"]
                        for n in graph["nodes"]
                    )
                    allowance = max(0, (remaining - 6) // max(1, unfinished))
                    if allowance < 3:
                        for node in direct:
                            self.update_node(
                                node,
                                "budget_limited",
                                reason="Insufficient remaining tool budget",
                            )
                        continue
                    branches = await asyncio.gather(
                        *(self.worker(n, allowance) for n in direct)
                    )
                    for node, branch in zip(direct, branches):
                        try:
                            await self.merge(node, branch)
                        except Exception as exc:
                            self.update_node(
                                node, "failed", error_type=type(exc).__name__
                            )
                            research.state["errors"].append(
                                {
                                    "stage": "merge",
                                    "task_id": node["id"],
                                    "type": type(exc).__name__,
                                }
                            )
                else:
                    for node in ready:
                        await self.synthesize_node(node)
                research.coverage()
            if len(graph["nodes"]) > 1 and research.state["claims"]:
                await research.audit()
            gaps = research.coverage()
            for node in graph["nodes"]:
                if node["status"] in {"complete", "partial"}:
                    node["status"] = (
                        "partial"
                        if any(g["question_id"] == node["id"] for g in gaps)
                        else "complete"
                    )
            research.state["stop_reason"] = (
                "coverage_satisfied" if not gaps else "graph_partial"
            )
            if gaps and any(
                n["status"] == "budget_limited"
                or n.get("stop_reason") == "tool_budget_exhausted"
                for n in graph["nodes"]
            ):
                research.state["stop_reason"] = "tool_budget_exhausted"
        except Exception as exc:
            research.state["stop_reason"] = (
                "time_budget_exhausted"
                if isinstance(exc, TimeoutError)
                else "tool_budget_exhausted"
                if isinstance(exc, BudgetExceeded)
                else "stage_error"
            )
            research.state["errors"].append(
                {"stage": "graph", "type": type(exc).__name__, "reason": str(exc)[:180]}
            )
        research.coverage()
        return await research.compose()
