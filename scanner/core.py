import logging
import requests

import Config
from .state import State
from .constants import API_HEADERS, BROWSER_HEADERS, TIMEOUT
from .query import query_title


class SubitoScanner:
    def __init__(self, state: State, notifiers: list):
        self.state = state
        self.notifiers = notifiers

    def run(self, dry_run: bool = False) -> dict:
        """scan all active queries.

        returns {new, queries, skipped, listed, empty, errors}.
        listed = ads returned by subito (seen or not).
        empty  = queries that returned 0 ads (often filters too strict).
        errors = queries that failed http/json.
        """
        if dry_run:
            print("\n⚙️  DRY-RUN MODE ENABLED: No notifications will be sent, results will be printed only.\n")

        cookies = self._init_session()
        new_count = 0
        scanned = 0
        skipped = 0
        listed = 0
        empty = 0
        errors = 0

        for params in self.state.queries:
            if self.state.is_query_disabled(params):
                logging.info(f"skipping disabled query: {query_title(params)}")
                skipped += 1
                continue

            scanned += 1
            items, err = self._fetch_items(params, cookies)
            if err:
                errors += 1
                continue
            if not items:
                empty += 1
                logging.info(f"no listings for: {query_title(params)}")
            listed += len(items)

            # reverse so newest items appear at the bottom of the telegram chat
            for item in reversed(items):
                item_id = str(item["urn"]).split(":")[-1]
                if self.state.has_item(params, item_id):
                    continue

                title = item.get("subject") or ""
                body = (item.get("body") or "").strip()
                place = self._extract_place(item.get("geo") or {})
                posted = self._extract_date(item.get("dates") or {})
                url = item["urls"]["default"]
                price = self._extract_price(item["features"])
                image = ""
                images = item.get("images") or []
                if images:
                    image = f"{images[0]['cdn_base_url']}?rule=images-auto"

                if dry_run:
                    print(f"[DRY-RUN] found: {title} - {url}")
                    if body:
                        print(f"          {body[:120]}…")
                    print(f"          {place} · {posted}")
                elif not self.state.paused:
                    for notifier in self.notifiers:
                        notifier.send(
                            title, price, url, image, body,
                            place=place, posted=posted,
                        )

                self.state.add_item(params, item_id)
                self.state.save()
                new_count += 1

        return {
            "new": new_count,
            "queries": scanned,
            "skipped": skipped,
            "listed": listed,
            "empty": empty,
            "errors": errors,
        }

    # ── private ───────────────────────────────────────────────────────────────

    def _init_session(self) -> dict:
        try:
            session = requests.Session()
            session.post(Config.subito_url, headers=BROWSER_HEADERS, timeout=TIMEOUT)
            return session.cookies.get_dict()
        except requests.exceptions.RequestException as e:
            logging.error(f"error initializing session: {e}")
            return {}

    def _fetch_items(self, params: str, cookies: dict):
        """return (ads_list, error_message_or_none)."""
        try:
            response = requests.get(
                f"{Config.subito_api_url}{params}",
                cookies=cookies,
                headers=API_HEADERS,
                timeout=TIMEOUT,
            )
            if response.status_code != 200:
                msg = f"http {response.status_code}"
                logging.error(f"error fetching items for '{query_title(params)}': {msg}")
                return [], msg
            return response.json().get("ads", []), None
        except (requests.exceptions.RequestException, ValueError) as e:
            logging.error(f"error fetching items for '{query_title(params)}': {e}")
            return [], str(e)

    @staticmethod
    def _extract_price(features: list) -> str:
        for feature in features:
            if feature["uri"] == "/price":
                return feature["values"][0]["value"]
        return "N/A"

    @staticmethod
    def _extract_place(geo: dict) -> str:
        """town (prov) · region, e.g. 'borgo a mozzano (lu) · toscana'."""
        town = (geo.get("town") or {}).get("value") or ""
        city = geo.get("city") or {}
        prov = city.get("short_name") or city.get("value") or ""
        region = (geo.get("region") or {}).get("value") or ""
        bits = []
        if town and prov:
            bits.append(f"{town} ({prov})")
        elif town:
            bits.append(town)
        elif city.get("value"):
            bits.append(city["value"])
        if region:
            bits.append(region)
        return " · ".join(bits)

    @staticmethod
    def _extract_date(dates: dict) -> str:
        """prefer iso timestamp, fall back to display string."""
        iso = dates.get("display_iso8601") or ""
        if iso:
            # 2026-07-31T17:41:38.598+0200 → 31/07/2026 17:41
            try:
                date_part, time_part = iso.split("T", 1)
                y, m, d = date_part.split("-")
                hm = time_part[:5]
                return f"{d}/{m}/{y} {hm}"
            except ValueError:
                pass
        raw = dates.get("display") or ""
        if raw and " " in raw:
            # 2026-07-31 17:41:38 → 31/07/2026 17:41
            try:
                date_part, time_part = raw.split(" ", 1)
                y, m, d = date_part.split("-")
                return f"{d}/{m}/{y} {time_part[:5]}"
            except ValueError:
                return raw
        return raw
