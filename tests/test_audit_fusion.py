"""Audit tests for graph_relevance_search tokenising edge cases."""


from nexus_mcp.engines.fusion import _name_words, graph_relevance_search
from nexus_mcp.engines.graph_engine import RustworkxCodeGraph
from tests.test_fusion import _make_node


def _graph(*names):
    g = RustworkxCodeGraph()
    for i, name in enumerate(names):
        g.add_node(_make_node(name, line=i * 10 + 1))
    return g


def _names(g, query):
    return {r["symbol_name"] for r in graph_relevance_search(g, query)}


def test_graph_relevance_search_empty_graph_returns_empty_list():
    assert graph_relevance_search(RustworkxCodeGraph(), "order") == []


def test_graph_relevance_search_only_short_or_stop_words_returns_empty_list():
    assert graph_relevance_search(_graph("db_connect"), "db is it the") == []


def test_graph_relevance_search_digits_in_query_match_whole_name():
    assert _names(_graph("sha256", "parse_v2"), "sha256") == {"sha256"}


def test_graph_relevance_search_acronym_prefix_in_camel_case_is_split():
    assert _name_words("HTTPServer") == ["http", "server"]
    assert _names(_graph("HTTPServer", "Router"), "http") == {"HTTPServer"}


def test_graph_relevance_search_unicode_query_does_not_crash():
    assert graph_relevance_search(_graph("create_order"), "créer 订单") == []


def test_graph_relevance_search_limit_zero_returns_empty_list():
    assert graph_relevance_search(_graph("create_order"), "order", limit=0) == []


def test_graph_relevance_search_scores_stay_in_unit_range():
    results = graph_relevance_search(_graph("a_order", "b_order", "c_order"), "order")
    assert results
    assert all(0.0 <= r["score"] <= 1.0 for r in results)


def test_graph_relevance_search_matches_non_ascii_identifier_words():
    assert _names(_graph("créer_commande"), "créer") == {"créer_commande"}


def test_graph_relevance_search_singular_query_matches_plural_name_word():
    assert _names(_graph("get_orders"), "order") == {"get_orders"}


def test_graph_relevance_search_es_plural_matches_singular_name_word():
    assert _names(_graph("class_loader", "address_book"), "classes addresses") == {
        "class_loader",
        "address_book",
    }
