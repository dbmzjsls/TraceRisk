from tracerisk.reports.render import render_report


def test_report_escapes_every_section() -> None:
    snapshot: dict[str, object] = {
        "company": {"name": "<script>company</script>"},
        "rules": [],
        "ai_findings": [{"title": "rejected <script>"}],
        "human_decisions": [{"reason": "<script>review</script>"}],
        "final_findings": [{"title": "approved"}],
        "evidence_references": [{"content": "<script>evidence</script>"}],
    }
    result = render_report(snapshot)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    for heading in ("确定性规则", "AI 原始结论", "人工决定", "最终结论"):
        assert heading in result
    final = result.split('<section id="final-findings">')[1].split("</section>")[0]
    assert "approved" in final
    assert "rejected" not in final
    assert "合成数据，仅供演示" in result


def test_report_explains_score_coverage_and_model_source() -> None:
    result = render_report(
        {
            "rule_score": 0,
            "as_of_date": "2026-11-08",
            "model_execution": {"mode": "mock", "models": ["mock"]},
            "coverage": {"evidence_sufficiency": "insufficient"},
        }
    )
    assert "零分不代表企业安全" in result
    assert "Mock 模拟模型" in result
    assert "2026-11-08" in result
    assert "最高95" in result
    assert "模型来源未记录" in render_report({})
