"""Frozen types and descriptions for the Stage 3 synthetic catalog."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from uuid import UUID


class SourceType(StrEnum):
    BUSINESS = "business"
    JUDICIAL = "judicial"
    NEWS = "news"
    INTELLECTUAL_PROPERTY = "intellectual_property"


class CoverageStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class RiskType(StrEnum):
    BUSINESS = "business"
    JUDICIAL = "judicial"
    NEWS = "news"
    INTELLECTUAL_PROPERTY = "intellectual_property"


BASELINE_DATE = date(2026, 9, 11)
STAGE3_SEED = 20_260_915
STABLE_UUID_NAMESPACE = UUID("2fdfdf1d-1d87-5b86-a525-df8c6dc34f2b")

PRESERVED_COMPANY_ID = UUID("20000000-0000-0000-0000-000000000001")
PRESERVED_COMPANY_SYNTHETIC_KEY = "judicial-open-case-story"
PRESERVED_COMPANY_NAME = "华辰智造（合成演示）有限公司"
PRESERVED_COMPANY_CREDIT_CODE = "SYNTHETIC-COMPANY-001"
PRESERVED_EVIDENCE_IDS = (
    UUID("21000000-0000-0000-0000-000000000001"),
    UUID("21000000-0000-0000-0000-000000000002"),
    UUID("21000000-0000-0000-0000-000000000003"),
)
PRESERVED_EVIDENCE_ROWS = (
    (
        PRESERVED_EVIDENCE_IDS[0],
        "synthetic://judicial/case-001/notice-a",
        "（2026）合成执字第001号立案信息",
        date(2026, 8, 10),
        "SYN-CASE-001",
        "合成司法来源显示：（2026）合成执字第001号案件状态为未结案。",
    ),
    (
        PRESERVED_EVIDENCE_IDS[1],
        "synthetic://judicial/case-001/notice-b",
        "（2026）合成执字第001号状态复核",
        date(2026, 8, 12),
        "SYN-CASE-001",
        "合成司法来源再次确认：（2026）合成执字第001号案件仍为未结案。",
    ),
    (
        PRESERVED_EVIDENCE_IDS[2],
        "synthetic://judicial/case-002/notice",
        "（2026）合成执字第002号立案信息",
        date(2026, 8, 15),
        "SYN-CASE-002",
        "合成司法来源显示：（2026）合成执字第002号案件状态为未结案。",
    ),
)


@dataclass(frozen=True)
class CompanySpec:
    id: UUID
    synthetic_key: str
    name: str
    unified_credit_code: str
    is_synthetic: bool = True


@dataclass(frozen=True)
class AliasSpec:
    id: UUID
    company_id: UUID
    name: str


@dataclass(frozen=True)
class EvidenceSpec:
    id: UUID
    company_id: UUID
    source_type: SourceType
    risk_type: RiskType
    source_uri: str
    title: str
    observed_at: date
    fact_type: str
    fact_key: str
    fact_value: str
    polarity: str
    content: str
    content_hash: str


@dataclass(frozen=True)
class CoverageSpec:
    id: UUID
    company_id: UUID
    source_type: SourceType
    as_of_date: date
    status: CoverageStatus


@dataclass(frozen=True)
class ScenarioExpectation:
    expected_rule_score: int
    requires_review: bool


@dataclass(frozen=True)
class CompanyScenario:
    key: str
    description: str
    company: CompanySpec
    aliases: tuple[AliasSpec, ...]
    evidence: tuple[EvidenceSpec, ...]
    coverage: tuple[CoverageSpec, ...]
    tags: tuple[str, ...]
    expectation: ScenarioExpectation


SCENARIO_ORDER = (
    "existing-judicial",
    "clean-operations",
    "all-rules-95",
    "alias-homonym",
    "duplicate-news",
    "cross-source-conflict",
    "unavailable-source",
    "news-window-boundaries",
    "business-date-boundaries",
    "prompt-injection",
)

SCENARIO_DESCRIPTIONS = {
    "existing-judicial": "保留的华辰智造司法未结案基线。",
    "clean-operations": "四类来源可用且没有风险事实的正常经营企业。",
    "all-rules-95": "司法、经营、新闻和核心知识产权规则同时命中的满分企业。",
    "alias-homonym": "有历史别名且名称与另一企业相同的隔离案例。",
    "duplicate-news": "两条报道共享一个规范新闻故事键，不能当作两个独立故事。",
    "cross-source-conflict": "不同来源对同一司法案件给出互斥规范状态。",
    "unavailable-source": "工商来源不可用，空结果只能表示证据不足。",
    "news-window-boundaries": "新闻分别位于 D-365、D-364、D、D+1 的边界案例。",
    "business-date-boundaries": (
        "经营异常包含基准日后解除、基准日解除和基准日后新增事实。"
    ),
    "prompt-injection": "正文含提示词注入文本，但它只是不可置信的证据数据。",
}

REQUIRED_SCENARIO_TAGS = frozenset(
    {
        "existing_judicial",
        "clean",
        "all_rules_95",
        "alias_homonym",
        "duplicate_news",
        "cross_source_conflict",
        "unavailable_source",
        "news_window_boundary",
        "business_date_boundary",
        "prompt_injection",
    }
)
