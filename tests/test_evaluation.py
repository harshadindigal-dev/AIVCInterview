from app.evaluation import expected_destination, failures, load_dataset, score


def test_expected_destinations(domain):
    recs = {r["id"]: r for r in load_dataset()}
    assert expected_destination(recs["E004"], domain) == ("lead", "tax")
    assert expected_destination(recs["E034"], domain) == ("partner", "priya_raman")
    assert expected_destination(recs["E036"], domain) == ("review", "review")   # conflict
    assert expected_destination(recs["E043"], domain) == ("review", "review")   # injection
    assert expected_destination(recs["E038"], domain) == ("archive", "archive")
    assert expected_destination(recs["E049"], domain) == ("duplicate", "E005")
    assert expected_destination(recs["E028"], domain) == ("review", "review")   # vague


def _pred(id_, gold, line, cx, dest, exp, case="clear", flags=(), dup=False):
    return {"id": id_, "case_type": case, "company_name": "c", "stated_urgency": "normal", "duplicate": dup,
            "gold": {"primary_service_line": gold[0], "complexity": gold[1], "urgency": "normal",
                     "should_review": exp[0] == "review", "notes": "", "case_type": case},
            "expected_destination": {"type": exp[0], "key": exp[1]},
            "pred": {"destination": {"type": dest[0], "key": dest[1], "name": "x"}, "primary_service_line": line,
                     "complexity": cx, "complexity_rationale": "r", "inferred_urgency": "normal", "flags": list(flags),
                     "summary": "s", "review_reasons": [], "agreement_service_line": 3,
                     "injection_flagged_any_run": "possible_injection" in flags, "classification_failed": False},
            "latency_ms": 100, "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01, "provider": "p",
            "model_used": "m"}


def test_score(domain):
    preds = [
        _pred("1", ("tax", "simple"), "tax", "simple", ("lead", "tax"), ("lead", "tax")),
        _pred("2", ("tax", "simple"), "deals", "complex", ("lead", "deals"), ("lead", "tax")),
        _pred("3", ("audit", "moderate"), "audit", "moderate", ("review", "review"), ("review", "review"),
              case="prompt_injection", flags=("possible_injection",)),
        _pred("4", (None, None), "risk_reg", "moderate", ("lead", "risk_reg"), ("review", "review"), case="vague"),
    ]
    m = score(preds, domain)
    assert m["service_line_accuracy"] == round(2 / 3, 4)
    assert m["complexity_errors"] == {"adjacent": 0, "two_levels": 1, "failed": 0}
    assert m["review_recall"] == 0.5 and m["missed_reviews"] == ["4"]
    assert m["auto_route_precision"] == round(1 / 3, 4)
    assert m["injection"]["all_flagged"] and m["injection"]["none_followed"]
    assert m["cost_per_1000_usd"] == 10.0
    assert [f["id"] for f in failures(preds)] == ["2", "4"]
