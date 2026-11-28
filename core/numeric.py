"""Exact numeric tokens, local subject bindings, and explicit Decimal calculations.

These checks reject inconsistent declared bindings; semantic scope still needs audit.
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

NUMBER = re.compile(
    r"(?<![\w])[-+−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*(?:%|percent\b|ms\b|seconds\b|million\b|billion\b|GB\b|MB\b|times\b|fold\b|[BMK]\b))?",
    re.I,
)
SYMBOL = re.compile(
    r"\b[A-Za-z][\w]*[-−]\d+(?:\.\d+)?\b|\b\d+(?:st|nd|rd|th)\b|\b(?:Algorithm|Figure|Table|Section)\s+\d+(?:\.\d+)?\b",
    re.I,
)
ORDINALS = {
    "1st": "first",
    "2nd": "second",
    "3rd": "third",
    "4th": "fourth",
    "5th": "fifth",
    "6th": "sixth",
    "7th": "seventh",
    "8th": "eighth",
    "9th": "ninth",
    "10th": "tenth",
}


def compatible(text):
    return unicodedata.normalize("NFKC", text).replace("−", "-")


def symbolic_tokens(text):
    return {
        ORDINALS.get(m.group().lower(), m.group().lower())
        for m in SYMBOL.finditer(compatible(text))
    }


def numbers(text):
    result = set()
    symbols = list(SYMBOL.finditer(text))
    for occurrence in NUMBER.finditer(text):
        if any(m.start() <= occurrence.start() < m.end() for m in symbols):
            continue
        token = occurrence.group()
        match = re.match(r"([-+−]?[\d,]+(?:\.\d+)?)(.*)", token)
        value = Decimal(match[1].replace(",", "").replace("−", "-"))
        unit = match[2].strip().lower().replace("percent", "%")
        result.add((value, unit))
    return result


def numeric_gate(text, quote):
    missing = numbers(text) - numbers(quote)
    source = compatible(quote).casefold()
    for ordinal, word in ORDINALS.items():
        source = re.sub(r"\b" + ordinal + r"\b", word, source)
    symbolic_missing = [s for s in symbolic_tokens(text) if not contains(source, s)]
    return {
        "ok": not missing and not symbolic_missing,
        "missing": [f"{v} {u}".strip() for v, u in sorted(missing)],
        "missing_identifiers": symbolic_missing,
    }


def contains(text, phrase):
    text, phrase = compatible(text), compatible(phrase)
    return (
        bool(phrase.strip())
        and re.search(r"(?<!\w)" + re.escape(phrase.strip()) + r"(?!\w)", text, re.I)
        is not None
    )


def local_binding(span, subject, value, subjects):
    """Require a local clause and reject a closer, competing named subject."""
    span, subject, subjects = (
        compatible(span),
        compatible(subject),
        [compatible(s) for s in subjects],
    )
    if not contains(span, subject):
        return False
    # A span cannot hide a reversed relationship by joining two separate clauses.
    if re.search(r";|\b(?:whereas|while|but)\b|(?<!\d)\.(?!\d)", span, re.I):
        return False
    anchors = set(subjects) | set(
        re.findall(r"\b(?:[Mm]odel|[Ss]ystem|[Mm]ethod)\s+[A-Z][\w.-]*", span)
    )
    for match in NUMBER.finditer(span):
        if value not in numbers(match.group()):
            continue
        prefix = span[: match.start()]
        occurrences = [
            (m.end(), anchor.lower())
            for anchor in anchors
            for m in re.finditer(
                r"(?<!\w)" + re.escape(anchor) + r"(?!\w)", prefix, re.I
            )
        ]
        if occurrences and max(occurrences)[1] == subject.lower():
            return True
    return False


def relation_gate(text, quote, facts):
    gate = numeric_gate(text, quote)
    errors, covered = [], set()
    subjects = [f.get("subject", "") for f in facts]
    for fact in facts:
        try:
            value = (
                Decimal(fact["value"].replace(",", "").replace("−", "-")),
                fact["unit"].strip().lower().replace("percent", "%"),
            )
        except (InvalidOperation, KeyError, AttributeError):
            errors.append("Invalid numeric fact")
            continue
        source_span, claim_span = (
            fact.get("source_span", ""),
            fact.get("claim_span", ""),
        )
        if (
            not source_span
            or source_span not in quote
            or not claim_span
            or claim_span not in text
        ):
            errors.append(
                "Numeric spans must be exact substrings of the quote and claim"
            )
            continue
        if any(
            not contains(content, fact.get("metric", "")) for content in (quote, text)
        ):
            errors.append("Metric label is not present in both quote and claim")
        condition = fact.get("condition", "")
        if condition and any(
            not contains(content, condition) for content in (quote, text)
        ):
            errors.append("Stated condition is not preserved in quote and claim")
        elif condition:
            covered.update(numbers(condition) & numbers(text) & numbers(quote))
        if not all(
            local_binding(span, fact.get("subject", ""), value, subjects)
            for span in (source_span, claim_span)
        ):
            errors.append("Numeric value is not locally bound to the same subject")
        if value not in numbers(text) or value not in numbers(quote):
            errors.append("Fact value/unit does not occur in both quote and claim")
        covered.add(value)
    unbound = numbers(text) - covered
    if unbound:
        errors.append("Every numeric token needs an explicit subject/metric binding")
    return {
        **gate,
        "ok": gate["ok"] and not errors,
        "relation_errors": list(dict.fromkeys(errors)),
        "facts_checked": len(facts),
        "unbound_values": [f"{v} {u}".strip() for v, u in sorted(unbound)],
    }


def json_numeric_facts(text, quote):
    """Bind literal JSON fields to the same named fields in dataset-sample prose."""
    facts = []
    for entry in re.finditer(
        r'"([^"\\]{1,50})"\s*:\s*(-?\d+(?:\.\d+)?)(?![\w.])', quote
    ):
        field, value = entry.group(1), Decimal(entry.group(2))
        for occurrence in NUMBER.finditer(text):
            if (value, "") not in numbers(occurrence.group()):
                continue
            prefix = text[: occurrence.start()]
            fields = list(
                re.finditer(r"(?<!\w)" + re.escape(field) + r"(?!\w)", prefix, re.I)
            )
            if not fields or occurrence.start() - fields[-1].start() > 90:
                continue
            facts.append(
                {
                    "subject": field,
                    "metric": field,
                    "value": entry.group(2),
                    "unit": "",
                    "condition": "",
                    "source_span": entry.group(),
                    "claim_span": text[fields[-1].start() : occurrence.end()],
                    "origin": "deterministic_json_field_binding",
                }
            )
            break
    return list(
        {(f["subject"], f["value"], f["claim_span"]): f for f in facts}.values()
    )


def bind_json_facts(text, quote, proposed):
    """Prefer independently derived field bindings over redundant model formatting guesses."""
    derived = json_numeric_facts(text, quote)

    def key(fact):
        try:
            return fact.get("subject"), Decimal(
                str(fact.get("value", "")).replace(",", "")
            )
        except InvalidOperation:
            return None

    verified = {key(f) for f in derived}
    return [f for f in proposed if key(f) not in verified] + derived


def calculate(operation, values):
    """Only named operations; no model expressions or eval. Inputs remain evidence-bound."""
    left, right = map(Decimal, values)
    if operation == "difference":
        return left - right
    if operation == "ratio":
        if not right:
            raise ValueError("Cannot divide by zero")
        return left / right
    if operation == "percent_change":
        if not right:
            raise ValueError("Zero baseline has no defined relative change")
        return (left - right) / abs(right) * 100
    raise ValueError("Unsupported calculation operation")
