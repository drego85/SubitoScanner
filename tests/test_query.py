from scanner.query import (
    apply_query_patch,
    build_query,
    parse_edit_args,
    parse_search_args,
    query_label,
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
    term, rid, lo, hi, err = parse_search_args("wd red in toscana min 50 max 100".split())
    assert err is None
    assert term == ["wd", "red"]
    assert rid == "9"
    assert lo == 50 and hi == 100


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
