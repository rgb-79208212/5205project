"""Criterion-level coverage uses supported claims, not source counts alone."""

from .verification import claim_checks


def coverage(state):
    gaps, report = [], []
    for question in state["subquestions"]:
        qid = question["id"]
        claims = {
            c["id"]: c
            for c in state["claims"]
            if c["question_id"] == qid
            and claim_checks(c, state["evidence"])["publishable"]
            and (not question["requires_full_text"] or c["access"] == "full_text")
        }
        studies = {
            state["sources"].get(s, {}).get("study_id", s)
            for c in claims.values()
            for s in c.get("source_ids", [c["source_id"]])
        }
        if len(studies) < question["min_sources"]:
            gaps.append(
                {
                    "id": f"{qid}:sources",
                    "question_id": qid,
                    "kind": "source_coverage",
                    "reason": f"{len(studies)}/{question['min_sources']} distinct study records with supported content and required access",
                    "requires_full_text": question["requires_full_text"],
                }
            )
        for criterion in question.get("criteria", []):
            review = state.get("criteria_reviews", {}).get(criterion["id"], {})
            refs = review.get("claim_ids", [])
            met = (
                review.get("status") == "met"
                and bool(refs)
                and all(r in claims for r in refs)
            )
            report.append(
                {
                    "question_id": qid,
                    **criterion,
                    "status": "met"
                    if met
                    else review.get("status", "missing")
                    if review.get("status") != "met"
                    else "missing",
                    "claim_ids": refs if met else [],
                    "reason": review.get("reason", "No audited supporting claims"),
                }
            )
            if not met:
                gaps.append(
                    {
                        "id": criterion["id"],
                        "question_id": qid,
                        "kind": "criterion",
                        "reason": criterion["description"]
                        + ": "
                        + review.get("reason", "No audited supporting claims"),
                        "requires_full_text": question["requires_full_text"],
                    }
                )
        if question.get("synthesis_required") and not any(
            c.get("kind") in ("synthesis", "inference", "derived")
            and len(
                {
                    state["sources"].get(s, {}).get("study_id", s)
                    for s in c.get("source_ids", [c["source_id"]])
                }
            )
            >= question["min_sources"]
            for c in claims.values()
        ):
            gaps.append(
                {
                    "id": qid + ":synthesis",
                    "question_id": qid,
                    "kind": "synthesis",
                    "reason": "A multi-evidence comparison or synthesis is required, separately audited; isolated findings are insufficient",
                }
            )
    for claim in state["claims"]:
        if claim["status"] == "conflict":
            gaps.append(
                {
                    "id": "conflict:" + claim["id"],
                    "question_id": claim["question_id"],
                    "kind": "conflict",
                    "reason": "Unresolved comparable evidence conflict: "
                    + claim["reason"],
                }
            )
    state["gaps"], state["coverage_report"] = gaps, report
    return gaps
