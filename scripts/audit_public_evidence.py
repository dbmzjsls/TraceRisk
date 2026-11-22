"""Read-only competition intake gate; never imports records or connects to a DB."""

import argparse
import json
from collections import Counter
from datetime import date
from hashlib import sha256
from pathlib import Path


def valid_uscc(value: object) -> bool:
    """Check the GB 32100 alphabet and MOD 31 check digit, not registration."""
    alphabet = "0123456789ABCDEFGHJKLMNPQRTUWXY"
    if (
        not isinstance(value, str)
        or len(value) != 18
        or any(character not in alphabet for character in value)
    ):
        return False
    weighted_sum = sum(
        alphabet.index(character) * pow(3, position, 31)
        for position, character in enumerate(value[:17])
    )
    return value[-1] == alphabet[(-weighted_sum) % 31]


def load_rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def audit(package: Path) -> dict:
    entities = {
        row["entity_id"]: row for row in load_rows(package / "data/entities.jsonl")
    }
    sources = {
        row["source_id"]: row for row in load_rows(package / "data/sources.jsonl")
    }
    records = []
    for row in load_rows(package / "data/evidence.jsonl"):
        if row.get("origin_type") != "real_public":
            continue
        entity = entities.get(row["entity_id"], {})
        source = sources.get(row["source_id"], {})
        reasons = []
        # A securities identifier never supplies the required verified USCC.
        # This gate deliberately cannot promote a search snippet to verified identity.
        if (
            entity.get("identifier_type") != "unified_social_credit_code"
            or entity.get("identity_review_status") != "verified"
            or not valid_uscc(entity.get("identifier_value"))
        ):
            reasons.append("identity_not_verified_uscc")
        if row.get("review_status") != "verified":
            reasons.append("fact_review_not_verified")
        if sha256(row.get("content", "").encode()).hexdigest() != row.get(
            "content_hash"
        ):
            reasons.append("content_hash_mismatch")
        if not row.get("fact_key"):
            reasons.append("missing_fact_key")
        published = source.get("published_at")
        if not published:
            reasons.append("missing_publication_date")
        else:
            try:
                if date.fromisoformat(row["observed_at"]) < date.fromisoformat(
                    published
                ):
                    reasons.append("observed_at_precedes_publication")
            except (KeyError, ValueError):
                reasons.append("invalid_visibility_date")
        pdf = package / source.get("local_path", "__missing__")
        pdf_hash = sha256(pdf.read_bytes()).hexdigest() if pdf.is_file() else None
        if pdf_hash != source.get("content_sha256") or pdf_hash is None:
            reasons.append("source_pdf_missing_or_hash_mismatch")
        if not source.get("document_url") or not row.get("page_or_section"):
            reasons.append("source_location_missing")
        records.append(
            {
                "evidence_id": row["evidence_id"],
                "entity_id": row["entity_id"],
                "source_id": row["source_id"],
                "accepted": not reasons,
                "reasons": reasons,
                "event_date": row.get("event_date"),
                "input_observed_at": row.get("observed_at"),
                "published_at": published,
                "accessed_at": source.get("accessed_at"),
                "required_visibility_date": published,
            }
        )
    return {
        "mode": "read_only_dry_run",
        "scope": "real_public evidence rows only",
        "accepted": sum(row["accepted"] for row in records),
        "rejected": sum(not row["accepted"] for row in records),
        "reason_counts_nonexclusive": dict(
            Counter(reason for row in records for reason in row["reasons"])
        ),
        "input_sha256": {
            str(path.relative_to(package)).replace("\\", "/"): sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted((package / "data").glob("*.jsonl"))
        },
        "records": records,
        "limitations": [
            "Package identity declarations are unverified; no external identity registry is consumed.",
            "Local PDF hashes and locator presence do not establish semantic correctness or independent review.",
            "Passing this dry-run alone never authorizes import; source, identity, review and ownership must be verified.",
            "Duplicate content and per-case reused evidence require deduplication before any future import.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.package), ensure_ascii=False, indent=2))
