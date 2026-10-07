from collections.abc import Iterable


def review_reasons(
    *,
    rule_score: int,
    evidence_sufficient: bool,
    conflict: bool = False,
    model_evidence_insufficient: bool = False,
    model_recommended: bool = False,
    support_reasons: Iterable[str] = (),
) -> list[str]:
    """Review triggers shared by graph routing and the report commit guard."""
    return [
        reason
        for reason, applies in (
            ("high_risk", rule_score >= 60),
            ("evidence_insufficient", not evidence_sufficient),
            ("conflict", conflict),
            ("model_evidence_insufficient", model_evidence_insufficient),
            ("model_recommended", model_recommended),
            (
                "unverified_free_text_extension",
                "unverified_free_text_extension" in support_reasons,
            ),
        )
        if applies
    ]


def requires_review(
    *,
    rule_score: int,
    evidence_sufficient: bool,
    conflict: bool = False,
    model_evidence_insufficient: bool = False,
    model_recommended: bool = False,
    support_reasons: Iterable[str] = (),
) -> bool:
    return bool(
        review_reasons(
            rule_score=rule_score,
            evidence_sufficient=evidence_sufficient,
            conflict=conflict,
            model_evidence_insufficient=model_evidence_insufficient,
            model_recommended=model_recommended,
            support_reasons=support_reasons,
        )
    )
