import json
from hashlib import sha256
from pathlib import Path

import pytest
from scripts.audit_public_evidence import audit


def test_read_only_gate_rejects_securities_id_and_prepublication_fact(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    pdf = tmp_path / "original.pdf"
    pdf.write_bytes(b"test fixture, not public evidence")
    content = "fixture excerpt"
    rows = {
        "entities": [
            {
                "entity_id": "example",
                "identifier_type": "securities_code",
                "identifier_value": "002966",
            }
        ],
        "sources": [
            {
                "source_id": "source",
                "published_at": "2026-06-25",
                "local_path": "original.pdf",
                "content_sha256": sha256(pdf.read_bytes()).hexdigest(),
                "document_url": "https://example.invalid/source",
            }
        ],
        "evidence": [
            {
                "evidence_id": "fixture",
                "entity_id": "example",
                "source_id": "source",
                "origin_type": "real_public",
                "review_status": "pending_human_review",
                "observed_at": "2026-06-23",
                "fact_key": None,
                "content": content,
                "content_hash": sha256(content.encode()).hexdigest(),
                "page_or_section": "page 1",
            }
        ],
    }
    for name, values in rows.items():
        (data / f"{name}.jsonl").write_text(
            "\n".join(json.dumps(row) for row in values), encoding="utf-8"
        )
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = audit(tmp_path)
    assert result["accepted"] == 0
    assert result["rejected"] == 1
    assert set(result["records"][0]["reasons"]) == {
        "identity_not_verified_uscc",
        "fact_review_not_verified",
        "missing_fact_key",
        "observed_at_precedes_publication",
    }
    assert result["records"][0]["required_visibility_date"] == "2026-06-25"
    assert before == {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    pdf.write_bytes(b"changed")
    assert (
        "source_pdf_missing_or_hash_mismatch"
        in audit(tmp_path)["records"][0]["reasons"]
    )


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("91320000768299855B", True),
        (None, False),
        ("", False),
        ("002966", False),
        ("91320000768299855", False),
        ("91320000768299855BB", False),
        ("91320000768299855A", False),
        ("91320000768299855I", False),
        ("91320000768299855b", False),
        (" 91320000768299855B", False),
        (913200007682998555, False),
    ],
)
def test_verified_identity_requires_valid_uscc(
    tmp_path: Path, value: object, accepted: bool
) -> None:
    # A well-formed test value is not evidence that an actual identity is verified.
    data = tmp_path / "data"
    data.mkdir()
    (tmp_path / "original.pdf").write_bytes(b"fixture")
    entity = {
        "entity_id": "fixture",
        "identifier_type": "unified_social_credit_code",
        "identity_review_status": "verified",
    }
    if value is not None:
        entity["identifier_value"] = value
    rows = {
        "entities": entity,
        "sources": {
            "source_id": "source",
            "published_at": "2026-06-25",
            "local_path": "original.pdf",
            "content_sha256": sha256(b"fixture").hexdigest(),
            "document_url": "https://example.invalid/source",
        },
        "evidence": {
            "evidence_id": "fixture",
            "entity_id": "fixture",
            "source_id": "source",
            "origin_type": "real_public",
            "review_status": "verified",
            "observed_at": "2026-06-25",
            "fact_key": "fixture",
            "content": "fixture",
            "content_hash": sha256(b"fixture").hexdigest(),
            "page_or_section": "page 1",
        },
    }
    for name, row in rows.items():
        (data / f"{name}.jsonl").write_text(json.dumps(row), encoding="utf-8")
    result = audit(tmp_path)
    assert result["accepted"] == int(accepted)
    assert result["records"][0]["reasons"] == (
        [] if accepted else ["identity_not_verified_uscc"]
    )
