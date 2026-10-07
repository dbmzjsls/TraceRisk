from tracerisk.workflows.routing import requires_review, review_reasons


def test_review_policy() -> None:
    assert review_reasons(rule_score=35, evidence_sufficient=True) == []
    assert not requires_review(rule_score=35, evidence_sufficient=True)
    assert review_reasons(
        rule_score=60,
        evidence_sufficient=False,
        conflict=True,
        model_evidence_insufficient=True,
        model_recommended=True,
    ) == [
        "high_risk",
        "evidence_insufficient",
        "conflict",
        "model_evidence_insufficient",
        "model_recommended",
    ]


def test_unverified_support_reason_routes_to_review_independently() -> None:
    assert review_reasons(
        rule_score=0,
        evidence_sufficient=True,
        support_reasons=(
            "unverified_free_text_extension",
            "unverified_free_text_extension",
        ),
    ) == ["unverified_free_text_extension"]
    assert requires_review(
        rule_score=0,
        evidence_sufficient=True,
        support_reasons=("unverified_free_text_extension",),
    )
