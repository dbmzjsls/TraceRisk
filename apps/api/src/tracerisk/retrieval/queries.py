"""One immutable, versioned query contract for indexing and workflow retrieval."""

from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType

QUERY_VERSION = "stage4-risk-queries-v1"
RISK_QUERIES = MappingProxyType(
    {
        "business": "企业经营异常、经营异常名录和工商登记风险",
        "judicial": "企业尚未结案的司法案件、诉讼和执行风险",
        "news": "企业负面新闻、舆情及经营相关报道风险",
        "intellectual_property": "企业核心知识产权、专利失效和权利状态异常风险",
    }
)


@dataclass(frozen=True)
class RiskQuery:
    risk_type: str
    text: str
    version: str = QUERY_VERSION

    @property
    def content_hash(self) -> str:
        return sha256(self.text.encode("utf-8")).hexdigest()


def workflow_queries() -> tuple[RiskQuery, ...]:
    return tuple(RiskQuery(risk, text) for risk, text in RISK_QUERIES.items())
