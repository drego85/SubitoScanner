from datetime import date, datetime, timedelta

from .regions import REGION_IDS, resolve_region

# client-only param — subito ignores it; we filter ads by listing date locally
_SINCE_KEY = "since"
_CLIENT_KEYS = {_SINCE_KEY}


def query_param(query_str: str, key: str):
    """return the value of key= in a query string, or None."""
    prefix = f"{key}="
    for part in query_str.split("&"):
        if part.startswith(prefix):
            return part[len(prefix):]
    return None


def query_region_id(query_str: str):
    """extract r= value from a query string, or None."""
    return query_param(query_str, "r")


def query_region_name(query_str: str) -> str:
    rid = query_region_id(query_str)
    if not rid:
        return ""
    return REGION_IDS.get(rid, rid)


def query_price_range(query_str: str):
    """return (min_price, max_price) as ints or None for missing bounds."""
    ps = query_param(query_str, "ps")
    pe = query_param(query_str, "pe")
    min_p = int(ps) if ps and ps.isdigit() else None
    max_p = int(pe) if pe and pe.isdigit() else None
    return min_p, max_p


def format_price_range(min_price, max_price) -> str:
    if min_price is not None and max_price is not None:
        return f"{min_price}–{max_price}€"
    if min_price is not None:
        return f"≥{min_price}€"
    if max_price is not None:
        return f"≤{max_price}€"
    return ""


def query_since(query_str: str):
    """return since-date (date) or None. stored as since=YYYY-MM-DD."""
    raw = query_param(query_str, _SINCE_KEY)
    if not raw:
        return None
    return parse_since_value(raw)


def format_since(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        d = parse_since_value(d)
    if not d:
        return ""
    return d.strftime("%d/%m/%Y")


def parse_since_value(raw: str):
    """parse a date string into date, or None if invalid.

    accepts: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, today/oggi, Nd / Ndays.
    """
    if not raw:
        return None
    text = raw.strip().lower().replace("+", " ")
    if text in ("today", "oggi"):
        return date.today()
    if text in ("yesterday", "ieri"):
        return date.today() - timedelta(days=1)

    # 7d / 30days / last7d
    m = text
    if m.startswith("last"):
        m = m[4:].lstrip()
    if m.endswith("days"):
        m = m[:-4].strip()
    elif m.endswith("day"):
        m = m[:-3].strip()
    elif m.endswith("d") and m[:-1].isdigit():
        m = m[:-1]
    if m.isdigit():
        return date.today() - timedelta(days=int(m) - 1) if int(m) > 0 else date.today()

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def query_title(query_str: str) -> str:
    """extract the human-readable title from a subito query string."""
    for part in query_str.split("&"):
        if part.startswith("q="):
            return part[2:].replace("+", " ")
    return query_str


def is_exact_query(query_str: str) -> bool:
    """true when the query uses subito's title-only / exact keyword filter (qso=true)."""
    return any(part.lower() == "qso=true" for part in query_str.split("&"))


def query_label(query_str: str) -> str:
    """human-readable query label with exact, region, price, since badges."""
    title = query_title(query_str)
    bits = [title]
    if is_exact_query(query_str):
        bits.append("[exact]")
    region = query_region_name(query_str)
    if region:
        bits.append(f"· {region}")
    price = format_price_range(*query_price_range(query_str))
    if price:
        bits.append(f"· {price}")
    since = format_since(query_since(query_str))
    if since:
        bits.append(f"· from {since}")
    return " ".join(bits)


def api_query(query_str: str) -> str:
    """strip client-only params before calling hades."""
    parts = []
    for part in query_str.split("&"):
        if not part or "=" not in part:
            continue
        key = part.split("=", 1)[0]
        if key in _CLIENT_KEYS:
            continue
        parts.append(part)
    return "&".join(parts)


def item_posted_date(item: dict):
    """listing publication date from hades ad payload, or None."""
    dates = item.get("dates") or {}
    iso = dates.get("display_iso8601") or ""
    if iso:
        try:
            return datetime.strptime(iso[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    raw = dates.get("display") or ""
    if raw:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def item_matches_since(item: dict, since) -> bool:
    """true if item has no date (keep) or was posted on/after since."""
    if not since:
        return True
    posted = item_posted_date(item)
    if posted is None:
        return True
    return posted >= since


def parse_search_args(args: list):
    """parse /add args into (term_args, region_id, min_price, max_price, since, error).

    supported filters (any order, mixed with the search term):
      in <region>
      min <n>
      max <n>
      <n>-<m>          shorthand for min-max (e.g. 500-1500)
      since <date> | from <date> | dal <date>
      last <n>d | last <n> days

    error is a short user-facing string, or None on success.
    """
    term_parts = []
    region_id = None
    min_price = None
    max_price = None
    since = None
    i = 0
    while i < len(args):
        token = args[i]
        low = token.lower()

        if low == "in" and i + 1 < len(args):
            resolved = resolve_region(args[i + 1])
            if not resolved:
                return [], None, None, None, None, f"unknown region '{args[i + 1]}'"
            region_id = resolved[0]
            i += 2
            continue

        if low == "min" and i + 1 < len(args):
            if not args[i + 1].isdigit():
                return [], None, None, None, None, "min price must be a number (e.g. min 100)"
            min_price = int(args[i + 1])
            i += 2
            continue

        if low == "max" and i + 1 < len(args):
            if not args[i + 1].isdigit():
                return [], None, None, None, None, "max price must be a number (e.g. max 500)"
            max_price = int(args[i + 1])
            i += 2
            continue

        if low in ("since", "from", "dal") and i + 1 < len(args):
            parsed = parse_since_value(args[i + 1])
            if not parsed:
                return [], None, None, None, None, (
                    f"invalid date '{args[i + 1]}' (use DD/MM/YYYY or YYYY-MM-DD)"
                )
            since = parsed
            i += 2
            continue

        if low == "last" and i + 1 < len(args):
            parsed = parse_since_value("last " + args[i + 1])
            if not parsed:
                return [], None, None, None, None, "use last 7d or last 30 days"
            since = parsed
            i += 2
            continue

        # last7d as one token
        if low.startswith("last") and len(low) > 4:
            parsed = parse_since_value(low)
            if parsed:
                since = parsed
                i += 1
                continue

        # shorthand 100-500 (not a date like 01-08-2026 — those have leading zeros / year)
        if token.count("-") == 1:
            left, right = token.split("-", 1)
            if left.isdigit() and right.isdigit() and len(left) <= 6 and len(right) <= 6:
                # avoid treating DD-MM-YYYY fragments; pure price ranges are short ints
                if len(left) <= 5 and len(right) <= 5 and int(left) < 1000000:
                    # if looks like date DD-MM with year missing skip — price ranges ok
                    min_price = int(left)
                    max_price = int(right)
                    i += 1
                    continue

        # bare date token as since
        bare = parse_since_value(token)
        if bare and ("/" in token or "." in token or (token.count("-") == 2)):
            since = bare
            i += 1
            continue

        term_parts.append(token)
        i += 1

    if min_price is not None and max_price is not None and min_price > max_price:
        return [], None, None, None, None, "min price cannot be greater than max price"

    return term_parts, region_id, min_price, max_price, since, None


def split_term_and_region(args: list):
    """legacy helper: split trailing 'in <region>' only."""
    term_args, region_id, _, _, _, _ = parse_search_args(args)
    return term_args, region_id


def build_query(
    term: str,
    exact: bool = False,
    region: str = None,
    min_price: int = None,
    max_price: int = None,
    shipping: bool = None,
    since=None,
) -> str:
    """build a default subito search query from a plain search term.

    exact=true sets qso=true (subito: search keywords in the listing title only).
    region is a subito region id (r=), e.g. '9' for Toscana. omit for all italy.
    min_price / max_price map to ps= / pe= (euros).
    shipping=true/false maps to shp=; omit (default) to include all listings —
    forcing shp=true hides most vehicles/large items that never ship.
    since is a date or YYYY-MM-DD — client-side filter (hades has no date param).
    """
    q = term.strip().replace(" ", "+")
    exact_flag = "true" if exact else "false"
    parts = [f"q={q}", "t=s", f"qso={exact_flag}"]
    if shipping is not None:
        parts.append(f"shp={'true' if shipping else 'false'}")
    if region:
        parts.append(f"r={region}")
    if min_price is not None:
        parts.append(f"ps={int(min_price)}")
    if max_price is not None:
        parts.append(f"pe={int(max_price)}")
    parts.extend(["sort=datedesc", "lim=10", "start=0"])
    if since is not None:
        if isinstance(since, str):
            since = parse_since_value(since)
        if since:
            parts.append(f"{_SINCE_KEY}={since.isoformat()}")
    return "&".join(parts)


# sentinel: leave this field unchanged when patching a query
_KEEP = object()


def parse_edit_args(args: list):
    """parse /edit filters into a patch dict.

    returns (patch, error). patch keys (only present when changed):
      term, region, min_price, max_price, exact, since
    region / min_price / max_price / since may be None to clear that filter.

    supported tokens (any order):
      in <region> | anywhere | clear region
      min <n> | max <n> | <n>-<m> | clear min | clear max | clear price
      since <date> | from <date> | last <n>d | clear since | anydate
      exact | broad
      <words…>  → new search term (optional)
    """
    patch = {}
    term_parts = []
    i = 0
    while i < len(args):
        token = args[i]
        low = token.lower()

        if low in ("exact", "title-only", "titleonly"):
            patch["exact"] = True
            i += 1
            continue
        if low in ("broad", "noexact", "no-exact"):
            patch["exact"] = False
            i += 1
            continue

        if low in ("anydate", "anytime", "anyday") or (
            low == "any" and i + 1 < len(args) and args[i + 1].lower() in ("date", "day", "time")
        ):
            patch["since"] = None
            i += 2 if low == "any" else 1
            continue

        if low == "anywhere" or (low == "all" and i + 1 < len(args) and args[i + 1].lower() == "italy"):
            patch["region"] = None
            i += 2 if low == "all" else 1
            continue

        if low == "clear" and i + 1 < len(args):
            what = args[i + 1].lower()
            if what in ("region", "location", "where"):
                patch["region"] = None
                i += 2
                continue
            if what in ("price", "prices"):
                patch["min_price"] = None
                patch["max_price"] = None
                i += 2
                continue
            if what == "min":
                patch["min_price"] = None
                i += 2
                continue
            if what == "max":
                patch["max_price"] = None
                i += 2
                continue
            if what in ("since", "date", "from", "dal"):
                patch["since"] = None
                i += 2
                continue
            return {}, f"unknown clear target '{args[i + 1]}' (use region, price, min, max, since)"

        if low == "in" and i + 1 < len(args):
            resolved = resolve_region(args[i + 1])
            if not resolved:
                return {}, f"unknown region '{args[i + 1]}'"
            patch["region"] = resolved[0]
            i += 2
            continue

        if low == "min" and i + 1 < len(args):
            if not args[i + 1].isdigit():
                return {}, "min price must be a number (e.g. min 100)"
            patch["min_price"] = int(args[i + 1])
            i += 2
            continue

        if low == "max" and i + 1 < len(args):
            if not args[i + 1].isdigit():
                return {}, "max price must be a number (e.g. max 500)"
            patch["max_price"] = int(args[i + 1])
            i += 2
            continue

        if low in ("since", "from", "dal") and i + 1 < len(args):
            parsed = parse_since_value(args[i + 1])
            if not parsed:
                return {}, f"invalid date '{args[i + 1]}' (use DD/MM/YYYY or YYYY-MM-DD)"
            patch["since"] = parsed
            i += 2
            continue

        if low == "last" and i + 1 < len(args):
            parsed = parse_since_value("last " + args[i + 1])
            if not parsed:
                return {}, "use last 7d or last 30 days"
            patch["since"] = parsed
            i += 2
            continue

        if low.startswith("last") and len(low) > 4:
            parsed = parse_since_value(low)
            if parsed:
                patch["since"] = parsed
                i += 1
                continue

        if token.count("-") == 1:
            left, right = token.split("-", 1)
            if left.isdigit() and right.isdigit() and len(left) <= 5 and len(right) <= 5:
                patch["min_price"] = int(left)
                patch["max_price"] = int(right)
                i += 1
                continue

        bare = parse_since_value(token)
        if bare and ("/" in token or "." in token or token.count("-") == 2):
            patch["since"] = bare
            i += 1
            continue

        term_parts.append(token)
        i += 1

    if term_parts:
        patch["term"] = " ".join(term_parts).strip()

    min_p = patch.get("min_price", _KEEP)
    max_p = patch.get("max_price", _KEEP)
    if isinstance(min_p, int) and isinstance(max_p, int) and min_p > max_p:
        return {}, "min price cannot be greater than max price"

    if not patch:
        return {}, None

    return patch, None


def apply_query_patch(query_str: str, patch: dict) -> str:
    """rebuild a query string applying patch on top of existing params."""
    term = patch["term"] if "term" in patch else query_title(query_str)
    exact = patch["exact"] if "exact" in patch else is_exact_query(query_str)

    if "region" in patch:
        region = patch["region"]
    else:
        region = query_region_id(query_str)

    cur_min, cur_max = query_price_range(query_str)
    min_price = patch["min_price"] if "min_price" in patch else cur_min
    max_price = patch["max_price"] if "max_price" in patch else cur_max

    if min_price is not None and max_price is not None and min_price > max_price:
        raise ValueError("min price cannot be greater than max price")

    shipping = None
    if "shipping" in patch:
        shipping = patch["shipping"]
    else:
        shp = query_param(query_str, "shp")
        if shp == "false":
            shipping = False

    if "since" in patch:
        since = patch["since"]
    else:
        since = query_since(query_str)

    return build_query(
        term,
        exact=exact,
        region=region,
        min_price=min_price,
        max_price=max_price,
        shipping=shipping,
        since=since,
    )
