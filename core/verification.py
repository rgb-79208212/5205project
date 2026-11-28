"""Separate publication safety, task completion, and execution health."""


def claim_checks(claim, evidence):
    refs = claim.get("refs", [])
    records = [evidence[r] for r in refs if r in evidence]
    provenance = bool(refs) and len(records) == len(refs)
    content = provenance and all(
        e.get("quote") and e.get("access") not in ("metadata_only", "unread")
        for e in records
    )
    numeric = bool(claim.get("numeric_gate", {}).get("ok"))
    semantic = claim.get("status") == "supported"
    return {
        "claim_id": claim["id"],
        "provenance": "passed" if provenance else "failed",
        "content_access": "passed" if content else "failed",
        "numeric": "passed" if numeric else "failed",
        "semantic": "model_supported" if semantic else claim.get("status", "pending"),
        "publishable": provenance and content and numeric and semantic,
        "reason": claim.get("reason", "Not yet assessed"),
    }


def execution_status(state):
    stop = state["stop_reason"]
    if stop == "stage_error":
        status = "failed"
    elif stop.endswith("budget_exhausted"):
        status = "budget_limited"
    elif state["errors"]:
        status = "completed_with_warnings"
    else:
        status = "completed"
    return {"status": status, "stop_reason": stop, "warnings": state["errors"]}


def verification_report(state, approved, extract_numbers):
    checks = [claim_checks(c, state["evidence"]) for c in state["claims"]]
    tokens = [extract_numbers(c["text"]) for c in state["claims"]]
    missing = [len(c["numeric_gate"].get("missing", [])) for c in state["claims"]]
    return {
        # Compatibility key now describes published claims, not overall coverage.
        "ok": bool(approved)
        and all(
            c["publishable"]
            for c in checks
            if c["claim_id"] in {a["id"] for a in approved}
        ),
        "scope": "Published claims passed recorded checks; not a guarantee of factual truth.",
        "grounded_claims": len(approved),
        "usable_sources": len(
            {s for c in approved for s in c.get("source_ids", [c["source_id"]])}
        ),
        "checks": checks,
        "pending_claims": sum(c["status"] == "pending" for c in state["claims"]),
        "numeric_grounding": {
            "checked": sum(map(len, tokens)),
            "matched": sum(max(0, len(t) - m) for t, m in zip(tokens, missing)),
            "all_matched": not any(missing),
            "blocked_claims": sum(not c["numeric_gate"]["ok"] for c in state["claims"]),
            "scope": "All candidate claims, including withheld candidates; relation checks reported per claim.",
        },
        "semantic_method": "Separate model call with fresh context; may share the reader's model and may misjudge support.",
    }
