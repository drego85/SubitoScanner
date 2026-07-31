TIMEOUT = 30

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/png,image/svg+xml,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-GPC": "1",
    "Priority": "u=0, i",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}

# subito hades api region ids (r=). keys are lowercase aliases users may type.
REGION_IDS = {
    "1": "Valle d'Aosta",
    "2": "Piemonte",
    "3": "Liguria",
    "4": "Lombardia",
    "5": "Trentino-Alto Adige",
    "6": "Veneto",
    "7": "Friuli-Venezia Giulia",
    "8": "Emilia-Romagna",
    "9": "Toscana",
    "10": "Umbria",
    "11": "Lazio",
    "12": "Marche",
    "13": "Abruzzo",
    "14": "Molise",
    "15": "Campania",
    "16": "Puglia",
    "17": "Basilicata",
    "18": "Calabria",
    "19": "Sardegna",
    "20": "Sicilia",
}

# alias → region id (include english + common shortenings)
_REGION_ALIASES = {
    "valle-d-aosta": "1", "valledaosta": "1", "aosta": "1", "vda": "1",
    "piemonte": "2", "piedmont": "2",
    "liguria": "3",
    "lombardia": "4", "lombardy": "4",
    "trentino-alto-adige": "5", "trentino": "5", "alto-adige": "5", "sudtirol": "5",
    "veneto": "6",
    "friuli-venezia-giulia": "7", "friuli": "7", "fvg": "7",
    "emilia-romagna": "8", "emilia": "8", "romagna": "8",
    "toscana": "9", "tuscany": "9",
    "umbria": "10",
    "lazio": "11",
    "marche": "12",
    "abruzzo": "13",
    "molise": "14",
    "campania": "15",
    "puglia": "16", "apulia": "16",
    "basilicata": "17",
    "calabria": "18",
    "sardegna": "19", "sardinia": "19",
    "sicilia": "20", "sicily": "20",
}


def _normalize_region_key(name: str) -> str:
    return (
        name.strip()
        .lower()
        .replace(" ", "-")
        .replace("'", "")
        .replace("à", "a")
        .replace("è", "e")
        .replace("é", "e")
        .replace("ì", "i")
        .replace("ò", "o")
        .replace("ù", "u")
    )


def resolve_region(name: str):
    """return (region_id, display_name) or None if unknown."""
    if not name:
        return None
    raw = name.strip()
    if raw.isdigit() and raw in REGION_IDS:
        return raw, REGION_IDS[raw]
    key = _normalize_region_key(raw)
    region_id = _REGION_ALIASES.get(key)
    if not region_id:
        return None
    return region_id, REGION_IDS[region_id]


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
    """human-readable query label with exact, region, and price badges."""
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
    return " ".join(bits)


def parse_search_args(args: list):
    """parse /add args into (term_args, region_id, min_price, max_price, error).

    supported filters (any order, mixed with the search term):
      in <region>
      min <n>
      max <n>
      <n>-<m>          shorthand for min-max (e.g. 500-1500)

    error is a short user-facing string, or None on success.
    """
    term_parts = []
    region_id = None
    min_price = None
    max_price = None
    i = 0
    while i < len(args):
        token = args[i]
        low = token.lower()

        if low == "in" and i + 1 < len(args):
            resolved = resolve_region(args[i + 1])
            if not resolved:
                return [], None, None, None, f"unknown region '{args[i + 1]}'"
            region_id = resolved[0]
            i += 2
            continue

        if low == "min" and i + 1 < len(args):
            if not args[i + 1].isdigit():
                return [], None, None, None, "min price must be a number (e.g. min 100)"
            min_price = int(args[i + 1])
            i += 2
            continue

        if low == "max" and i + 1 < len(args):
            if not args[i + 1].isdigit():
                return [], None, None, None, "max price must be a number (e.g. max 500)"
            max_price = int(args[i + 1])
            i += 2
            continue

        # shorthand 100-500
        if token.count("-") == 1:
            left, right = token.split("-", 1)
            if left.isdigit() and right.isdigit():
                min_price = int(left)
                max_price = int(right)
                i += 1
                continue

        term_parts.append(token)
        i += 1

    if min_price is not None and max_price is not None and min_price > max_price:
        return [], None, None, None, "min price cannot be greater than max price"

    return term_parts, region_id, min_price, max_price, None


def split_term_and_region(args: list):
    """legacy helper: split trailing 'in <region>' only."""
    term_args, region_id, _, _, _ = parse_search_args(args)
    return term_args, region_id


def build_query(
    term: str,
    exact: bool = False,
    region: str = None,
    min_price: int = None,
    max_price: int = None,
) -> str:
    """build a default subito search query from a plain search term.

    exact=true sets qso=true (subito: search keywords in the listing title only).
    region is a subito region id (r=), e.g. '9' for Toscana. omit for all italy.
    min_price / max_price map to ps= / pe= (euros).
    """
    q = term.strip().replace(" ", "+")
    exact_flag = "true" if exact else "false"
    parts = [f"q={q}", "t=s", "shp=true", f"qso={exact_flag}"]
    if region:
        parts.append(f"r={region}")
    if min_price is not None:
        parts.append(f"ps={int(min_price)}")
    if max_price is not None:
        parts.append(f"pe={int(max_price)}")
    parts.extend(["sort=datedesc", "lim=10", "start=0"])
    return "&".join(parts)


# sentinel: leave this field unchanged when patching a query
_KEEP = object()


def parse_edit_args(args: list):
    """parse /edit filters into a patch dict.

    returns (patch, error). patch keys (only present when changed):
      term, region, min_price, max_price, exact
    region / min_price / max_price may be None to clear that filter.

    supported tokens (any order):
      in <region> | anywhere | clear region
      min <n> | max <n> | <n>-<m> | clear min | clear max | clear price
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
            return {}, f"unknown clear target '{args[i + 1]}' (use region, price, min, max)"

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

        if token.count("-") == 1:
            left, right = token.split("-", 1)
            if left.isdigit() and right.isdigit():
                patch["min_price"] = int(left)
                patch["max_price"] = int(right)
                i += 1
                continue

        term_parts.append(token)
        i += 1

    if term_parts:
        patch["term"] = " ".join(term_parts).strip()

    min_p = patch.get("min_price", _KEEP)
    max_p = patch.get("max_price", _KEEP)
    # only validate when both bounds are concrete numbers in this patch;
    # full validation against existing values happens in apply_query_patch
    if isinstance(min_p, int) and isinstance(max_p, int) and min_p > max_p:
        return {}, "min price cannot be greater than max price"

    if not patch:
        return {}, None  # empty patch is ok — caller shows current state / usage

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

    return build_query(
        term,
        exact=exact,
        region=region,
        min_price=min_price,
        max_price=max_price,
    )
