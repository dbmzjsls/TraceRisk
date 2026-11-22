"""Export only the public synthetic fields from the verified competition captures."""

import json
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/competition/conversations"
DESTINATION = ROOT / "apps/web/src/features/demo"

CASE_LABELS = {
    "01": ("综合调查", "规则合计", "overview", ("规则合计",)),
    "02": (
        "来源不足",
        "规则合计",
        "coverage",
        ("工商来源不可用", "知识产权来源已过期", "没有待审AI发现"),
    ),
    "03": ("司法追问", "司法规则得分", "rules", ("司法规则",)),
}


def export() -> None:
    manifest = json.loads((SOURCE / "manifest.json").read_bytes())
    if manifest["execution_status"] != "captured_local_real":
        raise ValueError("Expected verified local real-model captures")
    captured = []
    screenshots = {}
    for example in manifest["examples"]:
        key = example["example_id"]
        title, metric, section_kind, phrases = CASE_LABELS[key]
        files = {}
        for name in ("prompt.txt", "response.json", "screenshot.png"):
            contents = (SOURCE / key / name).read_bytes()
            if sha256(contents).hexdigest() != example["files"][name]:
                raise ValueError(f"Capture digest changed: {key}/{name}")
            files[name] = contents
        answer = json.loads(files["response.json"])
        if (
            answer["question"] != files["prompt.txt"].decode().rstrip("\n")
            or answer["assistant_execution"]["mode"] != "real"
            or answer["investigation_model_execution"]["mode"] != "real"
            or answer["run_status"] != "awaiting_review"
            or answer["rule_score"] != example["expected_rule_score"]
            or example["retrieval_mode"] != "fake"
            or not example["unified_credit_code"].startswith("SYNTHETIC-COMPANY-")
            or "（合成）" not in example["company_name"]
            or any(
                not item["source_uri"].startswith("synthetic://")
                for item in answer["citations"]
            )
        ):
            raise ValueError(
                f"Capture does not match the synthetic demo contract: {key}"
            )
        sections = [
            {name: item[name] for name in ("kind", "title", "lines")}
            for item in answer["sections"]
        ]
        lines = next(item["lines"] for item in sections if item["kind"] == section_kind)
        preview = [next(line for line in lines if phrase in line) for phrase in phrases]
        if key == "01":
            preview.extend(
                next(item["lines"] for item in sections if item["kind"] == "rules")
            )
        if key == "02":
            preview.append(
                next(
                    line
                    for item in sections
                    for line in item["lines"]
                    if "0分不代表安全" in line
                )
            )
        rules = example["expected_explained_rules"]
        if key == "03" and (
            len(rules) != 1 or f"计{next(iter(rules.values()))}分" not in preview[0]
        ):
            raise ValueError("Judicial score must appear in the saved answer")
        captured.append(
            {
                "id": key,
                "title": title,
                "metric": metric,
                "score": answer["rule_score"]
                if key != "03"
                else next(iter(rules.values())),
                "company": example["company_name"],
                "asOfDate": example["as_of_date"],
                "question": answer["question"],
                "preview": preview,
                "sections": sections,
                "citations": [
                    {
                        name: item[name]
                        for name in (
                            "chunk_id",
                            "title",
                            "content",
                            "source_uri",
                            "observed_at",
                        )
                    }
                    for item in answer["citations"]
                ],
                "model": answer["assistant_execution"]["model"],
                "reuse": example["reuse_run_from"],
            }
        )
        screenshots[key] = files["screenshot.png"]
    by_id = {example["example_id"]: example for example in manifest["examples"]}
    if (
        len(captured) != 3
        or set(by_id) != set(CASE_LABELS)
        or by_id["01"]["run_id"] != by_id["03"]["run_id"]
        or by_id["01"]["investigation_id"] != by_id["03"]["investigation_id"]
    ):
        raise ValueError("Demo cases must include the same-Run judicial follow-up")
    assets = DESTINATION / "captures"
    assets.mkdir(parents=True, exist_ok=True)
    for key, contents in screenshots.items():
        (assets / f"{key}.png").write_bytes(contents)
    (DESTINATION / "examples.json").write_bytes(
        (json.dumps(captured, ensure_ascii=False, indent=2) + "\n").encode()
    )


if __name__ == "__main__":
    export()
