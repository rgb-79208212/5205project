"""Budgeted, gap-driven research. Skills have separate calls and enforceable contracts."""

import asyncio
import inspect
import json
import re
import time
from pathlib import Path
from uuid import uuid4

from jsonschema import ValidationError, validate

from research import ROOT, setting

from .coverage import coverage
from .numeric import bind_json_facts, numbers, relation_gate
from .numeric import numeric_gate as numeric_gate
from .skills import Skills
from .store import normalize
from .synthesis import build_synthesis, comparison_checks
from .verification import claim_checks, execution_status, verification_report
from .visuals import coverage_chart

TOOLS = {
    "search_papers",
    "read_source",
    "get_passages",
    "register_evidence",
    "search_datasets",
    "inspect_dataset",
    "search_encyclopedia",
    "search_passages",
}


class BudgetExceeded(Exception):
    pass


def dataset_targets(question):
    urls = re.findall(r"https://huggingface\.co/datasets/([\w.-]+/[\w.-]+)", question)
    plain = re.sub(r"https?://\S+", " ", question)
    identifiers = re.findall(r"(?<![\w/])([\w.-]+/[\w.-]+)(?![\w/])", plain)
    return list(dict.fromkeys(i.rstrip(".,;:") for i in urls + identifiers))


class Research:
    def __init__(
        self,
        question,
        session,
        model,
        emit,
        directory=None,
        max_calls=None,
        max_rounds=None,
    ):
        self.question, self.session, self.model, self.emit = (
            question,
            session,
            model,
            emit,
        )
        self.skills = Skills()
        self.use_methods = setting("RESEARCH_METHODS", "enabled") != "disabled"
        self.started = time.monotonic()
        self.deadline = self.started + max(
            20, min(600, float(setting("RESEARCH_TIMEOUT", "240")))
        )
        self.directory = Path(directory or ROOT / "runs" / uuid4().hex)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_calls = (
            max_calls if max_calls is not None else int(setting("MAX_TOOL_CALLS", "36"))
        )
        self.max_rounds = (
            max_rounds if max_rounds is not None else int(setting("MAX_ROUNDS", "3"))
        )
        self.state = {
            "question": question,
            "subquestions": [],
            "sources": {},
            "documents": {},
            "evidence": {},
            "claims": [],
            "audits": [],
            "searches": [],
            "activations": [],
            "trace": [],
            "gaps": [],
            "tool_calls": 0,
            "round": 0,
            "stop_reason": "not_started",
            "errors": [],
            "criteria_reviews": {},
            "coverage_report": [],
            "research_actions": [],
            "comparisons": [],
            "contract_errors": [],
            "model_calls": 0,
            "model_timings": [],
            "source_targets": {},
            "source_exclusions": [],
            "dataset_targets": dataset_targets(question),
        }
        self.activated = []

    def save(self):
        path = self.directory / "research-state.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        temporary.replace(path)

    def event(self, kind, data):
        self.state["trace"].append(
            {
                "event": kind,
                "elapsed_ms": round((time.monotonic() - self.started) * 1000),
                **data,
            }
        )
        self.save()
        self.emit(kind, data)

    def activate(self, name, reason):
        instructions, activation = self.skills.activate(name, reason)
        self.state["activations"].append(activation)
        if (
            activation.get("base_role")
            and activation["base_role"] not in self.activated
        ):
            self.activated.append(activation["base_role"])
        if name not in self.activated:
            self.activated.append(name)
        self.event(
            "skills_status",
            {
                "discovered": list(self.skills.catalog),
                "activated": list(self.activated),
            },
        )
        self.event(
            "pipeline_phase",
            {
                "phase": {
                    "planner": "planning",
                    "reader": "fetching",
                    "auditor": "verifying",
                    "writer": "synthesizing",
                }.get(name, "fetching"),
                "skill": name,
                "label": reason,
            },
        )
        return instructions

    async def ask(self, name, reason, payload):
        if name == "reader" and self.use_methods:
            task = next(
                (
                    q
                    for q in self.state["subquestions"]
                    if q["id"] == payload.get("target_question_id")
                ),
                {},
            )
            name = task.get("skill") or (
                "cross-paper-synthesis"
                if payload.get("mode") == "synthesis"
                else "original-paper"
            )
        instructions = self.activate(name, reason)
        contract = self.skills.schema(name)
        if (
            name == "planner"
            and self.use_methods
            and getattr(self.session, "catalog", [])
        ):
            contract["properties"]["subquestions"]["items"]["required"].append("skill")
        payload = {**payload, "output_contract": contract}
        # Each call gets a new context, not a shared writer/verifier conversation.
        for attempt in range(2):
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Overall research budget exhausted")
            self.state["model_calls"] += 1
            model_started = time.monotonic()
            returned = False
            try:
                async with asyncio.timeout(remaining):
                    output = self.model(instructions, payload)
                    output = await output if inspect.isawaitable(output) else output
                returned = True
            finally:
                self.state["model_timings"].append(
                    {
                        "skill": name,
                        "returned": returned,
                        "elapsed_ms": round((time.monotonic() - model_started) * 1000),
                    }
                )
            try:
                validate(output, contract)
                return output
            except ValidationError as exc:
                detail = {
                    "skill": name,
                    "attempt": attempt + 1,
                    "path": list(exc.absolute_path),
                    "message": exc.message[:300],
                }
                self.state["contract_errors"].append(detail)
                self.save()
                if attempt:
                    raise ValueError(f"{name} output violates its contract") from exc
                payload = {
                    **payload,
                    "contract_error": detail,
                    "output_contract": contract,
                    "previous_output": output,
                }

    async def call(self, name, arguments, label):
        self.skills.permit(name)
        if self.state["tool_calls"] >= self.max_calls:
            raise BudgetExceeded("MCP call budget exhausted")
        self.state["tool_calls"] += 1
        step = self.state["tool_calls"]
        self.event(
            "tool_start",
            {
                "step_index": step,
                "step": label,
                "tool": name,
                "tool_label": name,
                "source_group": "Evidence MCP",
                "arguments": arguments,
            },
        )
        start = time.monotonic()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Overall research budget exhausted")
        async with asyncio.timeout(min(48, remaining)):
            result = await self.session.call_tool(name, arguments)
        if result.isError:
            self.event(
                "tool_end",
                {
                    "step_index": step,
                    "tool": name,
                    "ok": False,
                    "summary": "Tool returned an error",
                    "duration_ms": round((time.monotonic() - start) * 1000),
                },
            )
            raise ValueError(f"Tool rejected request: {name}")
        data = result.structuredContent or json.loads(result.content[0].text)
        ok = bool(data.get("results")) if name == "search_papers" else True
        self.event(
            "tool_end",
            {
                "step_index": step,
                "tool": name,
                "ok": ok,
                "source": data.get("source", "Evidence MCP"),
                "summary": label,
                "duration_ms": round((time.monotonic() - start) * 1000),
                "evidence_id": data.get("id") if name == "register_evidence" else None,
                "mcp_method": data.get("mcp_method", "tools/call"),
                "resource_uri": data.get("resource_uri"),
            },
        )
        return data

    async def discover(self, searches, read_requests=None):
        valid_q = {q["id"] for q in self.state["subquestions"]}
        if self.state["round"] > 1:
            valid_q &= {g["question_id"] for g in self.state["gaps"]}
        for action in [*searches, *(read_requests or [])]:
            if action["question_id"] in valid_q:
                self.state["research_actions"].append(
                    {
                        "round": self.state["round"],
                        **action,
                        "gap_ids": [
                            g["id"]
                            for g in self.state["gaps"]
                            if g["question_id"] == action["question_id"]
                        ],
                    }
                )
        queue = [
            (r["source_id"], r["question_id"])
            for r in read_requests or []
            if r["source_id"] in self.state["sources"] and r["question_id"] in valid_q
        ]
        explicit = list(queue)
        self.activate(
            "planner", "Search specifically for unresolved research questions"
        )
        tried = {
            (
                item.get("tool", "search_papers"),
                (
                    item.get("executed_query")
                    or item.get("original_title")
                    or item["query"]
                )
                .strip()
                .casefold(),
                item.get("start_year"),
                item.get("end_year"),
            )
            for item in self.state["searches"]
        }
        for item in searches[:3]:
            if item["question_id"] not in valid_q:
                continue
            tool = item.get("tool", "search_papers")
            arguments = {"query": item["query"]}
            if tool == "search_datasets" and len(self.state["dataset_targets"]) == 1:
                arguments["query"] = self.state["dataset_targets"][0]
            if tool == "search_papers":
                arguments.update(
                    {
                        key: item[key]
                        for key in ("start_year", "end_year")
                        if key in item
                    }
                )
                title = item.get("original_title")
                if title:
                    title_key = re.sub(r"\W+", "", title).casefold()
                    if title_key not in re.sub(r"\W+", "", self.question).casefold():
                        raise ValueError(
                            "Original title must be copied from the user's question"
                        )
                    targets = self.state["source_targets"].setdefault(
                        item["question_id"], []
                    )
                    if title_key not in targets:
                        targets.append(title_key)
                    arguments["query"] = title
            key = (
                tool,
                arguments["query"].strip().casefold(),
                item.get("start_year"),
                item.get("end_year"),
            )
            if key in tried:
                continue
            tried.add(key)
            self.state["searches"].append(
                {**item, "executed_query": arguments["query"]}
            )
            try:
                data = await self.call(
                    tool,
                    arguments,
                    f"Find evidence for {item['question_id']}: {item['query']}",
                )
            except (ValueError, TimeoutError) as exc:
                self.state["errors"].append(
                    {
                        "stage": "planner",
                        "query": item["query"],
                        "type": type(exc).__name__,
                    }
                )
                continue
            results = data.get("results", [])
            targets = self.state["source_targets"].get(item["question_id"], [])
            results.sort(
                key=lambda source: (
                    bool(targets)
                    and re.sub(r"\W+", "", source["title"]).casefold() not in targets
                )
            )
            for source in results:
                self.state["sources"][source["id"]] = source
                queue.append((source["id"], item["question_id"]))
            self.state["sources"].update(data.get("source_catalog", {}))
            for warning in data.get("errors", []):
                self.state["errors"].append(
                    {"stage": "discovery", "query": item["query"], "reason": warning}
                )
        # Prefer unread documents. A failed claim can be narrowed on a later reread.
        remaining = [pair for pair in dict.fromkeys(queue) if pair not in explicit]
        remaining.sort(
            key=lambda pair: (
                pair[0] in self.state["documents"],
                not bool(self.state["sources"].get(pair[0], {}).get("reading_urls")),
            )
        )
        # Give each subquestion a turn instead of reading two sources for Q1 only.
        selected = list(dict.fromkeys(explicit))
        for qid in (q["id"] for q in self.state["subquestions"]):
            choice = next(
                (p for p in remaining if p[1] == qid and p not in selected), None
            )
            if choice:
                selected.append(choice)
        selected.extend(pair for pair in remaining if pair not in selected)
        return selected[:3]

    async def read(self, queue):
        for source_id, question_id in queue:
            source = self.state["sources"][source_id]
            if (
                source.get("kind") == "dataset"
                and self.state["dataset_targets"]
                and source.get("dataset") not in self.state["dataset_targets"]
            ):
                self.state["source_exclusions"].append(
                    {
                        "source_id": source_id,
                        "question_id": question_id,
                        "reason": "Does not match the dataset identifier supplied by the user",
                    }
                )
                continue
            targets = self.state["source_targets"].get(question_id, [])
            source_title = self.state["sources"][source_id].get("title", "")
            if targets and re.sub(r"\W+", "", source_title).casefold() not in targets:
                self.state["source_exclusions"].append(
                    {
                        "source_id": source_id,
                        "question_id": question_id,
                        "reason": "Does not match the explicitly requested original title",
                    }
                )
                continue
            self.activate(
                (
                    next(
                        q for q in self.state["subquestions"] if q["id"] == question_id
                    ).get("skill", "original-paper")
                    if self.use_methods
                    else "reader"
                ),
                f"Read source {source_id} for {question_id}, preserving passage locations",
            )
            try:
                focus = next(
                    q["question"]
                    for q in self.state["subquestions"]
                    if q["id"] == question_id
                )
                missing = [
                    g["reason"]
                    for g in self.state["gaps"]
                    if g["question_id"] == question_id
                ]
                if missing:
                    focus += " Evidence needed: " + " | ".join(missing)
                doc = await self.call(
                    "read_source",
                    {"source_id": source_id, "focus": focus},
                    "Read public body with this subquestion's focus, or explicitly labeled abstract",
                )
                if doc.get("access") == "full_text" and "search_passages" in getattr(
                    self.session, "routes", {}
                ):
                    selected = await self.call(
                        "search_passages",
                        {
                            "source_id": source_id,
                            "query": re.sub(
                                re.escape(source_title), "", focus, flags=re.I
                            ).strip(),
                        },
                        "Retrieve evidence with BM25, semantic search and reranking",
                    )
                    passages = selected["passages"] + selected.get(
                        "context_passages", []
                    )
                    doc.update(
                        passages=sorted(passages, key=lambda p: p["id"]),
                        retrieval=selected["retrieval"],
                    )
                    for warning in selected["retrieval"]["warnings"]:
                        self.state["errors"].append(
                            {
                                "stage": "retrieval",
                                "source_id": source_id,
                                "reason": warning,
                            }
                        )
            except (ValueError, TimeoutError) as exc:
                self.state["errors"].append(
                    {
                        "stage": "reader",
                        "source_id": source_id,
                        "type": type(exc).__name__,
                    }
                )
                continue
            self.state["documents"][source_id] = doc
            if not doc.get("passages"):
                continue
            response = await self.ask(
                "reader",
                f"Extract exact evidence for {question_id} ({doc['access']})",
                {
                    "question": self.question,
                    "subquestions": self.state["subquestions"],
                    "target_question_id": question_id,
                    "document": doc,
                    "previous_audit": self.state["audits"][-1:],
                    "gaps": [
                        g for g in self.state["gaps"] if g["question_id"] == question_id
                    ],
                    "existing_claims": [
                        {
                            "text": c["text"],
                            "status": c["status"],
                            "numeric_gate": c["numeric_gate"],
                            "reason": c["reason"],
                        }
                        for c in self.state["claims"]
                        if c["question_id"] == question_id
                    ],
                },
            )
            for candidate in response["claims"][:2]:
                if candidate["question_id"] != question_id:
                    continue
                if "passage_id" not in candidate:
                    self.state["errors"].append(
                        {
                            "stage": "reader",
                            "reason": "Direct reading cannot bypass quote registration with synthesis output",
                        }
                    )
                    continue
                passage = next(
                    (p for p in doc["passages"] if p["id"] == candidate["passage_id"]),
                    None,
                )
                if not passage:
                    self.state["errors"].append(
                        {"stage": "reader", "reason": "invented passage ID"}
                    )
                    continue
                try:
                    evidence = next(
                        (
                            e
                            for e in self.state["evidence"].values()
                            if e["source_id"] == source_id
                            and e["document_sha256"] == doc["sha256"]
                            and e["passage_id"] == candidate["passage_id"]
                            and e["quote"] == normalize(candidate["quote"])
                        ),
                        None,
                    )
                    evidence = evidence or await self.call(
                        "register_evidence",
                        {
                            "source_id": source_id,
                            "passage_id": candidate["passage_id"],
                            "quote": candidate["quote"],
                            "question_id": candidate["question_id"],
                        },
                        "Check exact quote against saved passage and register offsets",
                    )
                except ValueError:
                    self.state["errors"].append(
                        {"stage": "reader", "reason": "quote registration rejected"}
                    )
                    continue
                self.state["evidence"][evidence["id"]] = evidence
                previous = next(
                    (
                        c
                        for c in self.state["claims"]
                        if c["question_id"] == candidate["question_id"]
                        and (
                            c["text"] == candidate["text"]
                            or (
                                c["refs"] == [evidence["id"]]
                                and c["status"]
                                in ("blocked", "partial", "unsupported", "conflict")
                            )
                        )
                    ),
                    None,
                )
                if previous and (
                    previous["text"] == candidate["text"]
                    or previous["status"] == "supported"
                ):
                    continue
                facts = list(candidate.get("numeric_facts", []))
                if doc["access"] == "dataset_sample":
                    facts = bind_json_facts(candidate["text"], evidence["quote"], facts)
                gate = relation_gate(candidate["text"], evidence["quote"], facts)
                claim = {
                    "id": previous["id"]
                    if previous
                    else f"C{len(self.state['claims']) + 1}",
                    "question_id": candidate["question_id"],
                    "text": candidate["text"],
                    "refs": [evidence["id"]],
                    "source_id": source_id,
                    "source_ids": [source_id],
                    "access": doc["access"],
                    "numeric_gate": gate,
                    "numeric_facts": facts,
                    "model_numeric_facts": candidate.get("numeric_facts", []),
                    "kind": "direct",
                    "status": "pending" if gate["ok"] else "blocked",
                    "reason": "Awaiting separate audit"
                    if gate["ok"]
                    else "Numeric token or subject/metric/condition binding failed",
                }
                if previous:
                    self.state.setdefault("claim_revisions", []).append(dict(previous))
                    previous.update(claim)
                else:
                    self.state["claims"].append(claim)
                self.event(
                    "verify_status",
                    {
                        "ok": gate["ok"],
                        "message": f"{claim['id']}: exact quote recorded; numeric gate {'passed' if gate['ok'] else 'blocked publication'}",
                    },
                )

    def coverage(self):
        return coverage(self.state)

    async def synthesize(self, question_ids=None):
        added = False
        for question in self.state["subquestions"]:
            if question_ids is not None and question["id"] not in question_ids:
                continue
            if not question.get("synthesis_required"):
                continue
            qid = question["id"]
            parent_questions = {qid, *question.get("depends_on", [])}
            parents = [
                c
                for c in self.state["claims"]
                if c["question_id"] in parent_questions
                and c.get("kind") == "direct"
                and claim_checks(c, self.state["evidence"])["publishable"]
            ]
            if len({r for c in parents for r in c["refs"]}) < 2:
                continue
            if not any(g["question_id"] == qid for g in self.state["gaps"]) and any(
                c["question_id"] == qid
                and c.get("kind") != "direct"
                and claim_checks(c, self.state["evidence"])["publishable"]
                for c in self.state["claims"]
            ):
                continue
            response = await self.ask(
                "reader",
                "Synthesize approved evidence; every new inference must be audited again",
                {
                    "mode": "synthesis",
                    "question": self.question,
                    "target_question_id": qid,
                    "subquestions": self.state["subquestions"],
                    "direct_claims": parents,
                    "evidence": [
                        self.state["evidence"][r]
                        for r in dict.fromkeys(r for c in parents for r in c["refs"])
                    ],
                    "previous_audit": self.state["audits"][-1:],
                },
            )
            for candidate in response["claims"][:2]:
                try:
                    if candidate["question_id"] != qid or "refs" not in candidate:
                        raise ValueError("Synthesis must target the requested question")
                    claim = build_synthesis(candidate, self.state)
                    if any(
                        c["text"] == claim["text"] and c["refs"] == claim["refs"]
                        for c in self.state["claims"]
                    ):
                        continue
                    self.state["claims"].append(claim)
                    added = True
                    self.event(
                        "verify_status",
                        {
                            "ok": False,
                            "message": f"{claim['id']}: multi-evidence {claim['kind']} registered, awaiting audit",
                        },
                    )
                except ValueError as exc:
                    self.state["errors"].append(
                        {"stage": "synthesis", "reason": str(exc)[:200]}
                    )
        return added

    async def audit(self):
        eligible = [c for c in self.state["claims"] if c["numeric_gate"]["ok"]]
        if not eligible:
            self.coverage()
            return []
        self.activate(
            "auditor",
            "Review candidate claims against exact source context in a separate call",
        )
        contexts = []
        versions = list(
            dict.fromkeys(
                (e["source_id"], e["document_sha256"])
                for e in (
                    self.state["evidence"][ref] for c in eligible for ref in c["refs"]
                )
            )
        )
        for source_id, version in versions:
            evidence = [
                self.state["evidence"][ref]
                for c in eligible
                for ref in c["refs"]
                if self.state["evidence"][ref]["source_id"] == source_id
                and self.state["evidence"][ref]["document_sha256"] == version
            ]
            data = await self.call(
                "get_passages",
                {
                    "source_id": source_id,
                    "passage_ids": list(
                        dict.fromkeys(e["passage_id"] for e in evidence)
                    ),
                    "document_sha256": version,
                    "neighbor_window": 1,
                },
                "Retrieve the exact saved document version for separate evidence review",
            )
            contexts.append(data)
        review = await self.ask(
            "auditor",
            "Audit support, scope, access and contradictions in a fresh model context",
            {
                "question": self.question,
                "subquestions": self.state["subquestions"],
                "claims": eligible,
                "evidence": [
                    self.state["evidence"][ref]
                    for ref in dict.fromkeys(ref for c in eligible for ref in c["refs"])
                ],
                "contexts": contexts,
                "previous_searches": self.state["searches"],
            },
        )
        expected = {c["id"] for c in eligible}
        got = [c["claim_id"] for c in review["checks"]]
        if set(got) != expected or len(got) != len(expected):
            raise ValueError(
                "Auditor must return exactly one verdict for every candidate claim."
            )
        by_id = {c["claim_id"]: c for c in review["checks"]}
        expected_criteria = {
            a["id"]: q["id"]
            for q in self.state["subquestions"]
            for a in q.get("criteria", [])
        }
        reviews = review.get("coverage", [])
        if {c["criterion_id"] for c in reviews} != set(expected_criteria) or len(
            reviews
        ) != len(expected_criteria):
            raise ValueError(
                "Auditor must assess every original acceptance criterion exactly once"
            )
        for criterion in reviews:
            if criterion["question_id"] != expected_criteria[criterion["criterion_id"]]:
                raise ValueError("Coverage references the wrong subquestion")
        comparisons = comparison_checks(
            review.get("comparisons", []), eligible, self.state["evidence"]
        )
        for claim in eligible:
            check = by_id[claim["id"]]
            claim.update(status=check["status"], reason=check["reason"])
            if claim["status"] == "conflict" and not any(
                c["relationship"] == "comparable_conflict"
                and claim["id"] in c["claim_ids"]
                for c in comparisons
            ):
                claim.update(
                    status="partial",
                    reason="Possible conflict withheld: comparable task, dataset, metric and protocol not established. "
                    + claim["reason"],
                )
            q = next(
                q for q in self.state["subquestions"] if q["id"] == claim["question_id"]
            )
            if (
                q["requires_full_text"]
                and claim["access"] != "full_text"
                and claim["status"] == "supported"
            ):
                claim.update(
                    status="partial",
                    reason="Required full text was not read; abstract support is insufficient for this task.",
                )
        by_claim_id = {c["id"]: c for c in eligible}
        for claim in eligible:
            if claim.get("kind") == "derived" and any(
                by_claim_id.get(cid, {}).get("status") != "supported"
                for cid in claim.get("calculation", {}).get("input_claim_ids", [])
            ):
                claim.update(
                    status="partial",
                    reason="A calculation input is no longer supported by this audit",
                )
        self.state["criteria_reviews"] = {c["criterion_id"]: c for c in reviews}
        self.state["comparisons"] = comparisons
        self.state["audits"].append(review)
        gaps = self.coverage()
        self.event(
            "verify_status",
            {
                "ok": not gaps,
                "message": f"Separate evidence review: {sum(c['status'] == 'supported' for c in eligible)} supported claims; {len(gaps)} unresolved gaps",
            },
        )
        return review["followups"]

    async def initialize(self):
        proposal = await self.ask(
            "planner",
            "Decompose the question into checkable research tasks",
            {
                "question": self.question,
                "skill_catalog": [
                    {k: v for k, v in meta.items() if k != "path"}
                    for meta in self.skills.catalog.values()
                ],
                "research_methods": self.skills.methods() if self.use_methods else [],
                "mcp_capabilities": getattr(self.session, "catalog", []),
            },
        )
        if len({q["id"] for q in proposal["subquestions"]}) != len(
            proposal["subquestions"]
        ):
            raise ValueError("Planner returned duplicate subquestion IDs.")
        self.state["subquestions"] = proposal["subquestions"]
        for task in self.state["subquestions"]:
            method = task.get("skill")
            if method and (
                method not in self.skills.catalog
                or self.skills.catalog[method]["kind"] != "method"
            ):
                raise ValueError("Task selected an unavailable research-method Skill")
        self.state["mcp_capabilities"] = getattr(self.session, "catalog", [])
        criteria = [
            c["id"] for q in proposal["subquestions"] for c in q.get("criteria", [])
        ]
        if len(criteria) != len(set(criteria)):
            raise ValueError("Acceptance criterion IDs must be unique")
        if any(
            not c["id"].startswith(q["id"] + ".")
            for q in proposal["subquestions"]
            for c in q["criteria"]
        ):
            raise ValueError("Criterion ID must belong to its subquestion")
        ids = {q["id"] for q in self.state["subquestions"]}
        for question in self.state["subquestions"]:
            defaults = [
                q["id"]
                for q in self.state["subquestions"]
                if q["id"] != question["id"] and not q.get("synthesis_required")
            ]
            question.setdefault(
                "depends_on", defaults if question.get("synthesis_required") else []
            )
            if any(
                dep not in ids or dep == question["id"]
                for dep in question["depends_on"]
            ):
                raise ValueError(
                    "Task dependencies must reference other original subquestions"
                )
        dependencies = {q["id"]: q["depends_on"] for q in self.state["subquestions"]}

        def visit(qid, ancestors):
            if qid in ancestors:
                raise ValueError("Task dependency cycle")
            for dependency in dependencies[qid]:
                visit(dependency, {*ancestors, qid})

        for qid in ids:
            visit(qid, set())
        return proposal

    async def run(self, prepared_plan=None):
        try:
            proposal = (
                await self.initialize() if prepared_plan is None else prepared_plan
            )
            if prepared_plan is not None:
                self.state["subquestions"] = prepared_plan["subquestions"]
        except Exception as exc:
            self.state["stop_reason"] = (
                "time_budget_exhausted"
                if isinstance(exc, TimeoutError)
                else "stage_error"
            )
            self.state["errors"].append(
                {
                    "stage": "planner",
                    "type": type(exc).__name__,
                    "reason": str(exc)[:200],
                }
            )
            self.coverage()
            return await self.compose()
        searches = proposal["searches"]
        read_requests = proposal.get("read_requests", [])
        timeout = max(20, min(600, float(setting("RESEARCH_TIMEOUT", "240"))))
        try:
            async with asyncio.timeout(timeout):
                for round_number in range(1, min(4, self.max_rounds) + 1):
                    self.state["round"] = round_number
                    queue = await self.discover(searches, read_requests)
                    await self.read(queue)
                    followups = await self.audit()
                    if await self.synthesize():
                        followups = await self.audit()
                    gaps = self.coverage()
                    if not gaps:
                        self.state["stop_reason"] = "coverage_satisfied"
                        break
                    if round_number >= self.max_rounds:
                        self.state["stop_reason"] = "round_budget_exhausted"
                        break
                    next_plan = await self.ask(
                        "planner",
                        "Evidence gaps require a revised research plan, not report padding",
                        {
                            "question": self.question,
                            "gaps": gaps,
                            "auditor_followups": followups,
                            "previous_searches": self.state["searches"],
                            "subquestions": self.state["subquestions"],
                            "available_sources": [
                                {
                                    "id": s["id"],
                                    "title": s["title"],
                                    "access": self.state["documents"]
                                    .get(s["id"], {})
                                    .get("access", "unread"),
                                }
                                for s in self.state["sources"].values()
                            ],
                        },
                    )
                    # Original subquestions are immutable after planning; no goalpost shifting.
                    original = {q["id"]: q for q in self.state["subquestions"]}
                    next_tasks = [
                        {
                            **q,
                            "depends_on": q.get(
                                "depends_on",
                                original.get(q["id"], {}).get("depends_on", []),
                            ),
                        }
                        for q in next_plan["subquestions"]
                    ]
                    if next_tasks != self.state["subquestions"]:
                        self.state["errors"].append(
                            {
                                "stage": "planner",
                                "reason": "Attempted changes to original tasks/criteria ignored; original acceptance targets retained",
                            }
                        )
                    searches = next_plan["searches"]
                    read_requests = next_plan.get("read_requests", [])
                    self.event(
                        "pipeline_phase",
                        {
                            "phase": "planning",
                            "skill": "planner",
                            "label": f"Round {round_number + 1}: follow evidence gaps and revise searches",
                        },
                    )
                else:
                    self.state["stop_reason"] = "round_budget_exhausted"
        except (BudgetExceeded, TimeoutError) as exc:
            self.state["stop_reason"] = (
                "tool_budget_exhausted"
                if isinstance(exc, BudgetExceeded)
                else "time_budget_exhausted"
            )
        except Exception as exc:
            self.state["stop_reason"] = "stage_error"
            self.state["errors"].append(
                {
                    "stage": self.skills.active,
                    "type": type(exc).__name__,
                    "reason": str(exc)[:200],
                }
            )
        self.coverage()
        return await self.compose()

    async def compose(self):
        self.state["protocol_requests"] = [
            *getattr(self.session, "protocol_trace", []),
            *self.state.get("worker_protocol_requests", []),
        ]
        approved = [
            c
            for c in self.state["claims"]
            if claim_checks(c, self.state["evidence"])["publishable"]
        ]
        by_id = {c["id"]: c for c in approved}
        outline = {"sections": [], "summary_claim_ids": []}
        if approved:
            try:
                outline = await self.ask(
                    "writer",
                    "Organize approved claims only; no new factual prose or chart numbers",
                    {
                        "question": self.question,
                        "approved_claims": approved,
                        "gaps": self.state["gaps"],
                    },
                )
                ids = [i for s in outline["sections"] for i in s["claim_ids"]]
                if (
                    set(ids) != set(by_id)
                    or len(ids) != len(set(ids))
                    or any(i not in by_id for i in outline["summary_claim_ids"])
                ):
                    raise ValueError(
                        "Writer attempted to add, omit or duplicate unapproved claims."
                    )
            except Exception:
                outline = {
                    "sections": [
                        {"heading": "Supported evidence", "claim_ids": list(by_id)}
                    ],
                    "summary_claim_ids": list(by_id)[:3],
                }
                self.state["errors"].append(
                    {
                        "stage": "writer",
                        "reason": "Invalid outline; deterministic approved-claim layout used",
                    }
                )
        sections, blocks = [], []
        for section in outline["sections"]:
            # Headings cannot smuggle factual assertions: use the original question label.
            qids = list(
                dict.fromkeys(by_id[i]["question_id"] for i in section["claim_ids"])
            )
            heading = " / ".join(qids) + " · Evidence findings"
            paragraphs = [
                {
                    "text": by_id[i]["text"],
                    "refs": by_id[i]["refs"],
                    "claim_id": i,
                    "kind": by_id[i].get("kind", "direct"),
                }
                for i in section["claim_ids"]
            ]
            sections.append(
                {"heading": heading, "paragraphs": paragraphs, "takeaways": []}
            )
            blocks.extend({"section": heading, **p} for p in paragraphs)
        summary_claims = [by_id[i] for i in outline["summary_claim_ids"]]
        summary = (
            {
                "text": " ".join(c["text"] for c in summary_claims),
                "refs": list(
                    dict.fromkeys(e for c in summary_claims for e in c["refs"])
                ),
            }
            if summary_claims
            else None
        )
        comparison_table = (
            {
                "caption": "Approved claim-to-source registry",
                "columns": [
                    "Question",
                    "Claim",
                    "Evidence / source / passage",
                    "Access",
                ],
                "rows": [
                    [
                        c["question_id"],
                        c["id"],
                        "; ".join(
                            ref
                            + " / "
                            + self.state["evidence"][ref]["source_id"]
                            + " / "
                            + self.state["evidence"][ref]["passage_id"]
                            for ref in c["refs"]
                        ),
                        c["access"],
                    ]
                    for c in approved[:10]
                ],
                "refs": list(
                    dict.fromkeys(e for c in approved[:10] for e in c["refs"])
                ),
            }
            if approved
            else None
        )
        evidence = []
        for item in self.state["evidence"].values():
            evidence.append(
                {
                    "id": item["id"],
                    "tool": "register_evidence",
                    "data": {
                        **item,
                        "related_claims": [
                            {
                                "id": c["id"],
                                "status": c["status"],
                                "reason": c["reason"],
                                "publishable": claim_checks(c, self.state["evidence"])[
                                    "publishable"
                                ],
                            }
                            for c in self.state["claims"]
                            if item["id"] in c["refs"]
                        ],
                        "source": "Saved passage evidence",
                        "results": [
                            {
                                "title": item["title"],
                                "url": item["url"],
                                "summary": f"{item['locator']} [{item['access']}]\n{item['quote']}",
                            }
                        ],
                    },
                }
            )
        chart_evidence, chart = coverage_chart(self.state)
        charts = [chart] if chart else []
        if chart_evidence:
            evidence.append(chart_evidence)
        status = (
            "complete"
            if approved
            and not self.state["gaps"]
            and self.state["stop_reason"] == "coverage_satisfied"
            else "partial"
            if approved
            else "sources_only"
            if self.state["evidence"]
            else "no_evidence"
        )
        limitations = [
            "Evidence checks verify exact quote provenance, numeric/unit consistency and a separate model assessment; they do not guarantee truth or exhaustive coverage.",
            "Search is selective; source independence is approximated by deduplicated records, not author independence.",
            "Stop reason: " + self.state["stop_reason"],
        ]
        limitations += [
            g["question_id"] + ": " + g["reason"] for g in self.state["gaps"]
        ]
        rejected = [
            c
            for c in self.state["claims"]
            if not claim_checks(c, self.state["evidence"])["publishable"]
        ]
        limitations += [
            f"Withheld {c['id']} ({c['status']}): {c['reason']}" for c in rejected
        ]
        result = {
            "title": "Evidence research: " + self.question,
            "summary": summary,
            "background": None,
            "sections": sections,
            "claims": blocks,
            "comparison_table": comparison_table,
            "charts": charts,
            "status": status,
            "methodology": f"{self.state['round']} evidence rounds. Discovered sources were read at paragraph/page level; exact quotes were registered through MCP, candidates audited in a separate model call, gaps fed back to planning, and only supported claims published. State saved as {self.directory.name}. Subquestions: "
            + " | ".join(
                q["id"] + ": " + q["question"] for q in self.state["subquestions"]
            ),
            "limitations": " ".join(limitations),
            "evidence": evidence,
            "verification": verification_report(self.state, approved, numbers),
            "claim_registry": [
                {
                    "id": c["id"],
                    "question_id": c["question_id"],
                    "kind": c.get("kind", "direct"),
                    "text": c["text"],
                    "refs": c["refs"],
                    "status": c["status"],
                    "reason": c["reason"],
                    "checks": claim_checks(c, self.state["evidence"]),
                    "calculation": c.get("calculation"),
                    "numeric_facts": c.get("numeric_facts", []),
                }
                for c in self.state["claims"]
            ],
            "comparisons": self.state["comparisons"],
            "completion": {"status": status, "gaps": self.state["gaps"]},
            "execution": execution_status(self.state),
            "skills": {
                "discovered": list(self.skills.catalog),
                "activated": self.activated,
            },
            "stats": {
                "model_calls": self.state["model_calls"],
                "elapsed_ms": round((time.monotonic() - self.started) * 1000),
                "steps": self.state["tool_calls"],
                "usable_sources": len(
                    {s for c in approved for s in c.get("source_ids", [c["source_id"]])}
                ),
                "charts_count": len(charts),
            },
            "research": {
                "run_id": self.directory.name,
                "stop_reason": self.state["stop_reason"],
                "coverage": self.state["gaps"],
                "criteria": self.state["coverage_report"],
                "actions": self.state["research_actions"],
                "rounds": self.state["round"],
                "audits": len(self.state["audits"]),
                "study_count": len(
                    {
                        self.state["sources"].get(s, {}).get("study_id", s)
                        for c in approved
                        for s in c.get("source_ids", [c["source_id"]])
                    }
                ),
                "withheld_claims": len(rejected),
                "task_graph": self.state.get("task_graph"),
                "mcp_capabilities": self.state.get("mcp_capabilities", []),
            },
        }
        self.state["result"] = result
        self.save()
        self.event("live_result", result)
        return result
