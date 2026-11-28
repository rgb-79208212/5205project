"""Charts are deterministic run diagnostics, never model-invented research measurements."""


def coverage_chart(state):
    questions = state["subquestions"]
    if len(questions) < 2:
        return None, None
    counts = {
        q["id"]: len(
            {
                state.get("sources", {}).get(source_id, {}).get("study_id", source_id)
                for c in state["claims"]
                for source_id in c.get("source_ids", [c["source_id"]])
                if c["question_id"] == q["id"] and c["status"] == "supported"
            }
        )
        for q in questions
    }
    evidence = {
        "id": "E_RUN_COVERAGE",
        "tool": "deterministic_coverage",
        "data": {
            "title": "Approved study-record counts by subquestion",
            "source": "Saved research state",
            "counts": counts,
            "definition": "Distinct study groups among audit-supported claims. Explicit identifiers/exact titles merge versions; not proof of author independence or exhaustive research coverage.",
            "claim_ids": {
                q["id"]: [
                    c["id"]
                    for c in state["claims"]
                    if c["question_id"] == q["id"] and c["status"] == "supported"
                ]
                for q in questions
            },
        },
    }
    chart = {
        "chart_type": "column",
        "title": "Audit-supported study records for each research question",
        "evidence_id": evidence["id"],
        "source_note": evidence["data"]["definition"],
        "data": [{"category": key, "value": value} for key, value in counts.items()],
        "image_url": None,
        "renderer": "inline_svg",
    }
    return evidence, chart
