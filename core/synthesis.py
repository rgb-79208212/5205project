"""Multi-evidence claims, evidence-bound calculations, and scoped comparisons."""

from decimal import Decimal, InvalidOperation

from .numeric import calculate, contains, relation_gate
from .verification import claim_checks


def derived_claim(candidate, parents, evidence):
    specification = candidate.get("calculation", {})
    ids = specification.get("input_claim_ids", [])
    indices = specification.get("input_fact_indices", [])
    if len(ids) != 2 or len(indices) != 2 or any(i not in parents for i in ids):
        raise ValueError("Calculation needs two approved input claims")
    try:
        facts = [
            parents[cid]["numeric_facts"][index] for cid, index in zip(ids, indices)
        ]
    except (IndexError, KeyError, TypeError) as exc:
        raise ValueError("Calculation input fact does not exist") from exc
    if (
        any(
            facts[0][key].strip().lower() != facts[1][key].strip().lower()
            for key in ("metric", "unit", "condition")
        )
        or not facts[0]["condition"].strip()
    ):
        raise ValueError(
            "Calculations require identical explicit metric, unit and condition"
        )
    required = {r for cid in ids for r in parents[cid]["refs"]}
    if not required.issubset(candidate["refs"]):
        raise ValueError("Calculation omits input evidence")
    # Recheck the original input bindings; do not trust user-supplied calculated inputs.
    for cid in ids:
        parent = parents[cid]
        if not relation_gate(
            parent["text"],
            "\n".join(evidence[r]["quote"] for r in parent["refs"]),
            parent["numeric_facts"],
        )["ok"]:
            raise ValueError("Calculation input failed its original numeric binding")
    operation = specification["operation"]
    value = calculate(operation, [f["value"] for f in facts]).quantize(
        Decimal("0.000001")
    )
    value_text = format(value.normalize(), "f")
    unit = (
        ("percentage points" if facts[0]["unit"] == "%" else facts[0]["unit"])
        if operation == "difference"
        else "%"
        if operation == "percent_change"
        else "ratio"
    )
    label = {
        "difference": "difference (first minus second)",
        "ratio": "ratio (first divided by second)",
        "percent_change": "relative change (second as baseline)",
    }[operation]
    text = f"Under {facts[0]['condition']}, the {facts[0]['metric']} {label} for {facts[0]['subject']} versus {facts[1]['subject']} is {value_text} {unit}."
    record = {
        **specification,
        "inputs": facts,
        "value": value_text,
        "unit": unit,
        "decimal_places": 6,
        "method": "Program calculation from approved numeric facts, not a model-supplied result",
    }
    return (
        text,
        {"ok": True, "missing": [], "relation_errors": [], "calculation": record},
        record,
    )


def build_synthesis(candidate, state):
    qid, refs = candidate["question_id"], candidate["refs"]
    question = next(q for q in state["subquestions"] if q["id"] == qid)
    parent_questions = {qid, *question.get("depends_on", [])}
    parents = {
        c["id"]: c
        for c in state["claims"]
        if c["question_id"] in parent_questions
        and c.get("kind", "direct") == "direct"
        and claim_checks(c, state["evidence"])["publishable"]
    }
    allowed_refs = {r for c in parents.values() for r in c["refs"]}
    if len(set(refs)) < 2 or not set(refs).issubset(allowed_refs):
        raise ValueError(
            "Synthesis must cite at least two registered evidence records from approved direct claims for this question or its declared dependencies"
        )
    records = [state["evidence"][r] for r in refs]
    text, calculation = candidate["text"], None
    if candidate["kind"] == "derived":
        try:
            text, gate, calculation = derived_claim(
                candidate, parents, state["evidence"]
            )
        except (InvalidOperation, ArithmeticError) as exc:
            raise ValueError(
                "Calculation could not produce a finite bounded result"
            ) from exc
    else:
        facts = candidate.get("numeric_facts", [])
        for fact in facts:
            record = state["evidence"].get(fact.get("evidence_id"))
            if (
                not record
                or record["id"] not in refs
                or fact["source_span"] not in record["quote"]
            ):
                raise ValueError(
                    "Each synthesized numeric fact must name its own registered evidence"
                )
            if any(
                not contains(record["quote"], fact[key])
                for key in ("subject", "metric")
            ) or (
                fact["condition"] and not contains(record["quote"], fact["condition"])
            ):
                raise ValueError(
                    "A synthesized number cannot borrow its subject or conditions from another source"
                )
        gate = relation_gate(text, "\n".join(r["quote"] for r in records), facts)
    sources = list(dict.fromkeys(r["source_id"] for r in records))
    return {
        "id": f"C{len(state['claims']) + 1}",
        "question_id": qid,
        "kind": candidate["kind"],
        "text": text,
        "refs": refs,
        "source_id": sources[0],
        "source_ids": sources,
        "access": "full_text"
        if all(r["access"] == "full_text" for r in records)
        else "mixed",
        "numeric_facts": candidate.get("numeric_facts", []),
        "numeric_gate": gate,
        "calculation": calculation,
        "parent_claim_ids": [
            cid for cid, c in parents.items() if set(c["refs"]) & set(refs)
        ],
        "status": "pending" if gate["ok"] else "blocked",
        "reason": "Multi-evidence claim awaits separate semantic audit"
        if gate["ok"]
        else "Multi-evidence numeric binding failed",
    }


def comparison_checks(comparisons, claims, evidence):
    """Validate declared comparability; this does not certify a semantic contradiction."""
    by_id, checked = {c["id"]: c for c in claims}, []
    for item in comparisons:
        if any(cid not in by_id for cid in item["claim_ids"]):
            raise ValueError("Comparison references a claim outside this audit")
        quotes = [
            "\n".join(evidence[r]["quote"] for r in by_id[cid]["refs"])
            for cid in item["claim_ids"]
        ]
        conditions = item["conditions"]
        grounded = all(
            value and contains(quote, value)
            for quote, condition in zip(quotes, conditions)
            for value in condition.values()
        )
        same = all(
            conditions[0][key].strip().lower() == conditions[1][key].strip().lower()
            for key in ("task", "dataset", "metric", "protocol")
        )
        valid_conflict = (
            grounded
            and same
            and len({r for cid in item["claim_ids"] for r in by_id[cid]["refs"]}) >= 2
        )
        result = {**item, "conditions_grounded": grounded, "same_conditions": same}
        if item["relationship"] == "comparable_conflict" and not valid_conflict:
            result.update(
                relationship="insufficient_context",
                reason="Comparable conflict not established: conditions incomplete, different, or not present in quoted content. "
                + item["reason"],
            )
        checked.append(result)
    return checked
