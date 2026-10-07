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
