from datetime import date, timedelta

from scanner.query import (
    api_query,
    apply_query_patch,
    build_query,
    item_matches_since,
    parse_edit_args,
    parse_search_args,
    parse_since_value,
    query_label,
    query_since,
)
from scanner.regions import resolve_region


def test_resolve_region_aliases():
    assert resolve_region("toscana")[0] == "9"
    assert resolve_region("tuscany")[0] == "9"
    assert resolve_region("9")[0] == "9"
    assert resolve_region("nope") is None


def test_build_and_label():
    q = build_query("sh 125", exact=True, region="9", min_price=500, max_price=2000)
    assert "qso=true" in q
    assert "r=9" in q
    assert "ps=500" in q
    assert "shp=" not in q  # shipping filter off by default (vehicles, etc.)
    label = query_label(q)
    assert "sh 125" in label
    assert "[exact]" in label
    assert "Toscana" in label


def test_build_shipping_optional():
    assert "shp=true" in build_query("iphone", shipping=True)
    assert "shp=false" in build_query("iphone", shipping=False)


def test_parse_search_args():
    term, rid, lo, hi, since, err = parse_search_args(
        "wd red in toscana min 50 max 100".split()
    )
    assert err is None
    assert term == ["wd", "red"]
    assert rid == "9"
    assert lo == 50 and hi == 100
    assert since is None


def test_parse_since_filters():
    term, _, _, _, since, err = parse_search_args(
        "sh 125 since 01/08/2026".split()
    )
    assert err is None and term == ["sh", "125"]
    assert since == date(2026, 8, 1)
    q = build_query("sh 125", since=since)
    assert "since=2026-08-01" in q
    assert "since=" not in api_query(q)
    assert query_since(q) == date(2026, 8, 1)
    assert "from 01/08/2026" in query_label(q)


def test_item_matches_since():
    since = date(2026, 8, 1)
    assert item_matches_since(
        {"dates": {"display_iso8601": "2026-08-02T10:00:00+0200"}}, since
    )
    assert not item_matches_since(
        {"dates": {"display": "2026-07-31 10:00:00"}}, since
    )


def test_parse_since_relative():
    assert parse_since_value("today") == date.today()
    assert parse_since_value("7d") == date.today() - timedelta(days=6)


def test_edit_patch_stacks_filters():
    q = build_query("iphone 15")
    patch, err = parse_edit_args("in lombardia".split())
    assert err is None
    q2 = apply_query_patch(q, patch)
    assert "r=4" in q2
    patch2, err2 = parse_edit_args("min 100 max 500 exact".split())
    assert err2 is None
    q3 = apply_query_patch(q2, patch2)
    assert "ps=100" in q3 and "pe=500" in q3 and "qso=true" in q3
    patch3, err3 = parse_edit_args("anywhere clear price broad".split())
    assert err3 is None
    q4 = apply_query_patch(q3, patch3)
    assert "r=" not in q4 and "ps=" not in q4 and "qso=false" in q4
    patch4, err4 = parse_edit_args("since 2026-08-01".split())
    assert err4 is None
    q5 = apply_query_patch(q4, patch4)
    assert query_since(q5) == date(2026, 8, 1)
    patch5, err5 = parse_edit_args("clear since".split())
    assert err5 is None
    q6 = apply_query_patch(q5, patch5)
    assert query_since(q6) is None
