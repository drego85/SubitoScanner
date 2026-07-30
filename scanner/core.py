import logging
import requests

import Config
from .state import State
from .utils import BROWSER_HEADERS, TIMEOUT, query_title


class SubitoScanner:
    def __init__(self, state: State, notifiers: list):
        self.state = state
        self.notifiers = notifiers

    def run(self, dry_run: bool = False) -> dict:
        """scan all active queries. returns {new, queries, skipped}."""
        if dry_run:
            print("\n⚙️  DRY-RUN MODE ENABLED: No notifications will be sent, results will be printed only.\n")

        cookies = self._init_session()
        new_count = 0
        scanned = 0
        skipped = 0

        for params in self.state.queries:
            if self.state.is_query_disabled(params):
                logging.info(f"skipping disabled query: {query_title(params)}")
                skipped += 1
                continue

            scanned += 1
            items = self._fetch_items(params, cookies)

            # reverse so newest items appear at the bottom of the telegram chat
            for item in reversed(items):
                item_id = str(item["urn"]).split(":")[-1]
                if self.state.has_item(params, item_id):
                    continue

                title = item["subject"]
                url = item["urls"]["default"]
                price = self._extract_price(item["features"])
                image = ""
                images = item.get("images") or []
                if images:
                    image = f"{images[0]['cdn_base_url']}?rule=images-auto"

                if dry_run:
                    print(f"[DRY-RUN] found: {title} - {url}")
                elif not self.state.paused:
                    for notifier in self.notifiers:
                        notifier.send(title, price, url, image)

                self.state.add_item(params, item_id)
                self.state.save()
                new_count += 1

        return {"new": new_count, "queries": scanned, "skipped": skipped}

    # ── private ───────────────────────────────────────────────────────────────

    def _init_session(self) -> dict:
        try:
            session = requests.Session()
            session.post(Config.subito_url, headers=BROWSER_HEADERS, timeout=TIMEOUT)
            return session.cookies.get_dict()
        except requests.exceptions.RequestException as e:
            logging.error(f"error initializing session: {e}")
            return {}

    def _fetch_items(self, params: str, cookies: dict) -> list:
        try:
            response = requests.get(
                f"{Config.subito_api_url}{params}",
                cookies=cookies,
                headers=BROWSER_HEADERS,
            )
            return response.json().get("ads", [])
        except (requests.exceptions.RequestException, ValueError) as e:
            logging.error(f"error fetching items for '{query_title(params)}': {e}")
            return []

    @staticmethod
    def _extract_price(features: list) -> str:
        for feature in features:
            if feature["uri"] == "/price":
                return feature["values"][0]["value"]
        return "N/A"
