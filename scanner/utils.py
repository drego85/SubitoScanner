"""compat re-exports — prefer constants / regions / query for new code."""
from .constants import BROWSER_HEADERS, TIMEOUT
from .regions import REGION_IDS, resolve_region
from .query import (
    apply_query_patch,
    build_query,
    format_price_range,
    is_exact_query,
    parse_edit_args,
    parse_search_args,
    query_label,
    query_param,
    query_price_range,
    query_region_id,
    query_region_name,
    query_title,
    split_term_and_region,
)

__all__ = [
    "BROWSER_HEADERS",
    "TIMEOUT",
    "REGION_IDS",
    "resolve_region",
    "apply_query_patch",
    "build_query",
    "format_price_range",
    "is_exact_query",
    "parse_edit_args",
    "parse_search_args",
    "query_label",
    "query_param",
    "query_price_range",
    "query_region_id",
    "query_region_name",
    "query_title",
    "split_term_and_region",
]
