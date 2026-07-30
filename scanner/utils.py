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


def query_region_id(query_str: str):
    """extract r= value from a query string, or None."""
    for part in query_str.split("&"):
        if part.startswith("r="):
            return part[2:]
    return None


def query_region_name(query_str: str) -> str:
    rid = query_region_id(query_str)
    if not rid:
        return ""
    return REGION_IDS.get(rid, rid)


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
    """human-readable query label with exact + region badges."""
    title = query_title(query_str)
    bits = [title]
    if is_exact_query(query_str):
        bits.append("[exact]")
    region = query_region_name(query_str)
    if region:
        bits.append(f"· {region}")
    return " ".join(bits)


def split_term_and_region(args: list):
    """split args into (term_args, region_id|None).

    supports a trailing: in <region>
    e.g. ['wd', 'red', 'in', 'toscana'] → (['wd', 'red'], '9')
    """
    if len(args) >= 3 and args[-2].lower() == "in":
        resolved = resolve_region(args[-1])
        if resolved:
            return args[:-2], resolved[0]
    return args, None


def build_query(term: str, exact: bool = False, region: str = None) -> str:
    """build a default subito search query from a plain search term.

    exact=true sets qso=true (subito: search keywords in the listing title only).
    region is a subito region id (r=), e.g. '9' for Toscana. omit for all italy.
    """
    q = term.strip().replace(" ", "+")
    exact_flag = "true" if exact else "false"
    parts = [f"q={q}", "t=s", "shp=true", f"qso={exact_flag}"]
    if region:
        parts.append(f"r={region}")
    parts.extend(["sort=datedesc", "lim=10", "start=0"])
    return "&".join(parts)
