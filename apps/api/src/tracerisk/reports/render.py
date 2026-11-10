import json
from html import escape


def render_report(snapshot: dict[str, object]) -> str:
    """Render immutable snapshot values only as escaped text, never markup."""
    sections = [
        ("company", "企业"),
        ("as_of_date", "调查基准日（历史未记录时为空）"),
        ("rule_score", "规则总分"),
        ("model_execution", "模型调用记录（未记录时无法确认模式）"),
        ("review_reasons", "复核原因"),
        ("conflicts", "事实冲突"),
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
    execution = snapshot.get("model_execution")
    mode = execution.get("mode") if isinstance(execution, dict) else None
    model_label = {
        "mock": "Mock 模拟模型（未调用真实模型）",
        "real": "真实模型调用",
    }.get(str(mode), "模型来源未记录，不能确认 Mock 或真实调用")
    coverage = snapshot.get("coverage")
    sufficient = (
        isinstance(coverage, dict)
        and coverage.get("evidence_sufficiency") == "sufficient"
    )
    warning = "" if sufficient else "<p>来源不足或覆盖未确认；零分不代表企业安全。</p>"
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>风险报告</title></head><body><h1>风险报告</h1>"
        "<p>"
        + escape(str(snapshot.get("notice", "合成数据，仅供演示")))
        + "</p>"
        + "<p>"
        + model_label
        + "</p>"
        + warning
        + "<section><h2>评分说明</h2><p>司法：存在未结案件 +35；"
        "经营：基准日有效异常 +25；"
        "新闻：基准日前含当日365天内至少两条独立负面报道 +20；"
        "知识产权：核心专利失效或权属争议 +15。</p>"
        "<p>每条最多计分一次，最高95；重复案件和转载不重复计分。"
        "0～29为低、30～59为中、60～95为高。权重仅为演示配置，"
        "不是信用标准或违约概率；AI 与人工不能修改规则分。</p></section>"
        + body
        + "</body></html>"
    )
