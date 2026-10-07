import json
from html import escape


def render_report(snapshot: dict[str, object]) -> str:
    """Render immutable snapshot values only as escaped text, never markup."""
    sections = [
        ("company", "企业"),
        ("rules", "确定性规则"),
        ("ai_findings", "AI 原始结论"),
        ("human_decisions", "人工决定"),
        ("final_findings", "最终结论"),
        ("evidence_references", "证据引用"),
        ("retrieved_evidence", "检索证据"),
        ("coverage", "证据覆盖"),
    ]
    body = "".join(
        f'<section id="{key.replace("_", "-")}"><h2>{heading}</h2><pre>'
        + escape(json.dumps(snapshot.get(key, []), ensure_ascii=False, indent=2))
        + "</pre></section>"
        for key, heading in sections
    )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>风险报告</title></head><body><h1>风险报告</h1>"
        "<p>合成数据，仅供演示</p>" + body + "</body></html>"
    )
