from hashlib import sha256
from uuid import UUID

import pytest

from tracerisk.retrieval.indexing import QUERY_VERSION, RISK_QUERIES
from tracerisk.retrieval.keyword import KeywordDocument, rank_keyword, tokenize
from tracerisk.retrieval.queries import workflow_queries


def document(number, **fields):
    return KeywordDocument(chunk_id=UUID(int=number), **fields)


def test_unicode_whitespace_and_chinese_bigrams():
    assert tokenize(" ＦＯＯ\tBar\nＣＡＦÉ ") == tokenize("foo bar cafe\u0301")
    assert {"经营", "营异", "异常"} <= tokenize("经营异常")
    assert "常名" not in tokenize("异常 名录")
    assert "ab" not in tokenize("abc")


def test_title_and_structured_fact_boosts_and_explicit_scores():
    rows = rank_keyword(
        "异常",
        [
            document(1, content="异常"),
            document(2, fact_value="异常"),
            document(3, title="异常"),
            document(4, content="normal"),
        ],
    )
    assert [row.chunk_id.int for row in rows] == [3, 2, 1, 4]
    assert [row.rank for row in rows] == [1, 2, 3, 4]
    assert rows[0].score > rows[1].score > rows[2].score > rows[3].score == 0


def test_fact_fields_are_independently_searchable():
    for field in ("fact_type", "fact_key", "fact_value", "polarity"):
        assert (
            rank_keyword("negative", [document(1, **{field: "negative"})])[0].score > 0
        )


@pytest.mark.parametrize("query", ["", " \t\n"])
def test_empty_queries_have_no_lexical_ranking(query):
    assert rank_keyword(query, [document(1, content="news")]) == ()


def test_keyword_ties_are_independent_of_input_order():
    documents = [document(i, content="新闻报道") for i in (3, 1, 2)]
    assert rank_keyword("新闻", documents) == rank_keyword("新闻", documents[::-1])
    assert [r.chunk_id.int for r in rank_keyword("新闻", documents)] == [1, 2, 3]


def test_exact_versioned_queries_and_task1_compatibility():
    expected = {
        "business": "企业经营异常、经营异常名录和工商登记风险",
        "judicial": "企业尚未结案的司法案件、诉讼和执行风险",
        "news": "企业负面新闻、舆情及经营相关报道风险",
        "intellectual_property": "企业核心知识产权、专利失效和权利状态异常风险",
    }
    queries = workflow_queries()
    assert queries == workflow_queries()
    assert RISK_QUERIES == expected
    assert QUERY_VERSION == "stage4-risk-queries-v1"
    assert [(q.risk_type, q.text) for q in queries] == list(expected.items())
    for query in queries:
        assert query.version == QUERY_VERSION
        assert (
            query.content_hash == sha256(expected[query.risk_type].encode()).hexdigest()
        )
    with pytest.raises(TypeError):
        RISK_QUERIES["news"] = "mutable contract"
