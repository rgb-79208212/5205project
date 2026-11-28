"""Sixteen synthetic questions / 64 authored claim expectations, not real-paper gold.

The expectations are test authors' intended labels. Independent human review of real
research is exported separately; these examples cannot establish production accuracy.
"""

SPECS = [
    (
        "definition",
        "What information does the described retrieval system use?",
        "The described system retrieves passages from an external document collection and conditions generation on those passages.",
        [
            (
                "The described system uses passages retrieved from an external document collection.",
                True,
            ),
            ("The described system conditions generation on retrieved passages.", True),
            (
                "The described system uses only information stored in its model parameters.",
                False,
            ),
            (
                "The described system guarantees that generated answers are correct.",
                False,
            ),
        ],
    ),
    (
        "numeric_binding",
        "Which model obtained each accuracy on TestSet?",
        "On TestSet, Model A achieved accuracy of 90%; Model B achieved accuracy of 85%.",
        [
            ("On TestSet, Model A achieved accuracy of 90%.", True),
            ("On TestSet, Model B achieved accuracy of 85%.", True),
            ("On TestSet, Model A achieved accuracy of 85%.", False),
            ("On TestSet, Model A achieved accuracy of 99%.", False),
        ],
    ),
    (
        "signed_change",
        "What change did this experiment report?",
        "On TestSet, the experiment reports a change of -2% in accuracy under the fixed setting.",
        [
            ("On TestSet, the experiment reports a change of -2% in accuracy.", True),
            ("On TestSet, the experiment reports a change of +2% in accuracy.", False),
            ("On TestSet, the experiment reports a change of -3% in accuracy.", False),
            (
                "The experiment establishes that accuracy improved under the fixed setting.",
                False,
            ),
        ],
    ),
    (
        "causality",
        "What can the observational study establish?",
        "The observational study finds an association between retrieval use and answer quality. It does not identify a causal effect.",
        [
            (
                "The study reports an association between retrieval use and answer quality.",
                True,
            ),
            ("The study does not identify a causal effect.", True),
            ("The study proves that retrieval causes better answers.", False),
            (
                "The study establishes that retrieval can never improve answer quality.",
                False,
            ),
        ],
    ),
    (
        "abstract_scope",
        "Describe the detailed training protocol from the original full paper.",
        "The abstract introduces a retrieval system and states that evaluations were conducted. It gives no training protocol.",
        [
            ("The full paper used a frozen retriever during training.", False),
            ("The full paper trained the retriever jointly with the generator.", False),
            ("The full paper used contrastive training with hard negatives.", False),
            (
                "The original training protocol is verified by this abstract alone.",
                False,
            ),
        ],
    ),
    (
        "negation",
        "Did the tested intervention improve accuracy?",
        "The intervention did not improve accuracy in the tested setting. Latency was not measured.",
        [
            ("The intervention did not improve accuracy in the tested setting.", True),
            ("Latency was not measured in this experiment.", True),
            ("The intervention improved accuracy in the tested setting.", False),
            ("The intervention reduced latency in the tested setting.", False),
        ],
    ),
    (
        "uncertainty",
        "What cost conclusion does the evidence support?",
        "The authors suggest that caching may reduce cost, but did not measure deployment cost or establish a reliable saving.",
        [
            ("The authors suggest caching as a possible way to reduce cost.", True),
            ("The study did not measure deployment cost.", True),
            (
                "The study establishes a reliable deployment cost saving from caching.",
                False,
            ),
            ("The study proves that caching cannot reduce cost.", False),
        ],
    ),
    (
        "sample_scope",
        "What can be concluded from this dataset inspection?",
        "The inspected sample contains positive and negative labels. The population label distribution and model accuracy were not evaluated.",
        [
            ("Positive and negative labels appear in the inspected sample.", True),
            ("The inspection did not evaluate model accuracy.", True),
            ("The dataset population has a balanced label distribution.", False),
            ("The inspected dataset establishes strong model accuracy.", False),
        ],
    ),
    (
        "version_identity",
        "Do the two records represent independent studies?",
        "The repository record is a preprint version of the journal article. Both records describe the same experiment.",
        [
            (
                "The repository record and journal article describe the same experiment.",
                True,
            ),
            (
                "The repository record is a preprint version of the journal article.",
                True,
            ),
            ("The records constitute independent experimental replications.", False),
            (
                "The repository record describes a different experiment from the journal article.",
                False,
            ),
        ],
    ),
    (
        "no_doi",
        "What method is described in this readable official report?",
        "This official technical report describes a sparse lexical retriever followed by a generator. The report has no DOI.",
        [
            (
                "The report describes a sparse lexical retriever followed by a generator.",
                True,
            ),
            ("The readable report has no DOI.", True),
            (
                "The report describes a dense retriever rather than a sparse lexical retriever.",
                False,
            ),
            (
                "The report establishes superiority over all dense retrieval methods.",
                False,
            ),
        ],
    ),
    (
        "doi_metadata",
        "What empirical results can this metadata record establish?",
        "The index record contains a title, author list and DOI. It contains no abstract, methods or experimental results.",
        [
            ("The study experimentally established improved accuracy.", False),
            ("The study experimentally established lower latency.", False),
            ("The DOI certifies that the study's findings are correct.", False),
            ("The study proved that its method outperforms all alternatives.", False),
        ],
    ),
    (
        "conditions",
        "Can the reported results establish a global ranking?",
        "Method Alpha was tested on short English questions. Method Beta was tested on long multilingual documents. No matched comparison was performed.",
        [
            ("The methods were evaluated under different conditions.", True),
            ("No matched comparison of the methods was performed.", True),
            ("Method Alpha is globally superior to Method Beta.", False),
            (
                "The results demonstrate a contradiction under identical experimental conditions.",
                False,
            ),
        ],
    ),
    (
        "conflict",
        "What conclusion follows from the matched studies?",
        "Under the same dataset, metric and protocol, one study reports a gain and another reports a loss. The reason for this disagreement remains unresolved.",
        [
            ("The matched studies report an unresolved disagreement.", True),
            ("The studies used the same dataset, metric and protocol.", True),
            ("The studies consistently establish a gain.", False),
            ("The disagreement proves the intervention has no effect.", False),
        ],
    ),
    (
        "limitations",
        "What language scope was evaluated?",
        "The evaluation used English documents only. Multilingual transfer was not evaluated, so the results do not establish multilingual effectiveness.",
        [
            ("The evaluation used English documents only.", True),
            ("The results do not establish multilingual effectiveness.", True),
            ("The method demonstrated effective multilingual transfer.", False),
            (
                "The absence of multilingual evaluation proves multilingual transfer is impossible.",
                False,
            ),
        ],
    ),
    (
        "absence",
        "Does the evaluation establish equivalence?",
        "The small evaluation detected no clear difference. Its limited size does not establish equivalence across the broader population.",
        [
            ("The small evaluation detected no clear difference.", True),
            ("The evaluation does not establish population-wide equivalence.", True),
            ("The methods are proven equivalent across the entire population.", False),
            ("The evaluation proves that a large difference exists.", False),
        ],
    ),
    (
        "security_scope",
        "What does the security evaluation establish?",
        "The system resisted the tested prompt attacks. Adaptive attacks and private-data leakage were not evaluated, and general safety is not established.",
        [
            ("The system resisted the attacks included in the evaluation.", True),
            ("The evaluation did not assess private-data leakage.", True),
            ("The system is safe against all adaptive attacks.", False),
            (
                "The evaluation establishes that private-data leakage is impossible.",
                False,
            ),
        ],
    ),
]


def cases():
    result = []
    for index, (name, question, quote, candidates) in enumerate(SPECS, 1):
        rows = []
        for number, (text, supported) in enumerate(candidates, 1):
            facts = []
            if name == "numeric_binding":
                subject = "Model B" if "Model B" in text else "Model A"
                value = "90" if "90%" in text else "85" if "85%" in text else "99"
                source_span = (
                    "Model B achieved accuracy of 85%"
                    if subject == "Model B"
                    else "Model A achieved accuracy of 90%"
                    if value == "90"
                    else quote.rstrip(".")
                )
                facts = [
                    {
                        "subject": subject,
                        "metric": "accuracy",
                        "value": value,
                        "unit": "%",
                        "condition": "TestSet",
                        "source_span": source_span,
                        "claim_span": text.split(", ", 1)[1].rstrip("."),
                    }
                ]
            elif name == "signed_change" and number < 4:
                value = ("-2", "+2", "-3")[number - 1]
                facts = [
                    {
                        "subject": "the experiment",
                        "metric": "accuracy",
                        "value": value,
                        "unit": "%",
                        "condition": "TestSet",
                        "source_span": quote.split(", ", 1)[1].rstrip("."),
                        "claim_span": text.split(", ", 1)[1].rstrip("."),
                    }
                ]
            rows.append(
                {
                    "id": f"C{number}",
                    "text": text,
                    "expected_supported": supported,
                    "numeric_facts": facts,
                }
            )
        result.append(
            {
                "id": f"Q{index:02}",
                "category": name,
                "question": question,
                "quote": quote,
                "access": "abstract"
                if name == "abstract_scope"
                else "metadata_only"
                if name == "doi_metadata"
                else "full_text",
                "requires_full_text": name == "abstract_scope",
                "candidates": rows,
                "synthetic": True,
                "label_origin": "authored test expectations; not independent human annotations",
            }
        )
    return result
