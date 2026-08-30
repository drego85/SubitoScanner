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
