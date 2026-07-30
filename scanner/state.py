import json
import logging
import os

STATE_FILE = "scanner_state.json"
LEGACY_ITEMS_FILE = "subito_items.txt"


class State:
    def __init__(
        self,
        paused: bool,
        queries: list,
        disabled_queries: list,
        last_update_id: int,
        items_by_query: dict,
    ):
        self.paused = paused
        self.queries = queries                  # ordered list of query strings
        self.disabled_queries = disabled_queries  # query strings (not indices)
        self.last_update_id = last_update_id
        self.items_by_query = items_by_query    # {query_str: [item_ids]}

    # ── persistence ───────────────────────────────────────────────────────────

    @classmethod
    def load(cls, seed_queries: list = None) -> "State":
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
        except (IOError, json.JSONDecodeError):
            data = {}

        # use persisted queries if present, otherwise seed from Config
        queries = data.get("queries") or list(seed_queries or [])

        state = cls(
            paused=data.get("paused", False),
            queries=queries,
            disabled_queries=data.get("disabled_queries", []),
            last_update_id=data.get("last_update_id", 0),
            items_by_query=data.get("items_by_query", {}),
        )
        state._migrate_numeric_keys(seed_queries or [])
        state._migrate_legacy_file()
        return state

    def save(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(self._to_dict(), f, indent=2)
        except IOError as e:
            logging.error(f"error saving state: {e}")

    def _to_dict(self) -> dict:
        return {
            "paused": self.paused,
            "queries": self.queries,
            "disabled_queries": self.disabled_queries,
            "last_update_id": self.last_update_id,
            "items_by_query": self.items_by_query,
        }

    def _migrate_numeric_keys(self, seed_queries: list):
        """one-time migration: convert numeric-indexed keys to query-string keys."""
        if not self.items_by_query or not any(k.isdigit() for k in self.items_by_query):
            return
        if not seed_queries:
            return

        new_items = {}
        for k, v in self.items_by_query.items():
            if k.isdigit() and int(k) < len(seed_queries):
                new_items[seed_queries[int(k)]] = v
            else:
                new_items[k] = v
        self.items_by_query = new_items

        new_disabled = []
        for d in self.disabled_queries:
            if isinstance(d, int) and d < len(seed_queries):
                new_disabled.append(seed_queries[d])
            elif isinstance(d, str):
                new_disabled.append(d)
        self.disabled_queries = new_disabled

        logging.info("migrated state from numeric to query-string keys")

    def _migrate_legacy_file(self):
        """one-time migration from the old flat subito_items.txt."""
        if not os.path.exists(LEGACY_ITEMS_FILE):
            return
        try:
            with open(LEGACY_ITEMS_FILE, "r", errors="ignore") as f:
                legacy_ids = [line.rstrip() for line in f if line.strip()]
            for q in self.queries:
                existing = set(self.items_by_query.get(q, []))
                self.items_by_query[q] = list(existing | set(legacy_ids))
            os.rename(LEGACY_ITEMS_FILE, LEGACY_ITEMS_FILE + ".bak")
            logging.info(f"migrated {len(legacy_ids)} item ids from {LEGACY_ITEMS_FILE}")
        except IOError as e:
            logging.error(f"error migrating legacy items: {e}")

    # ── query management ──────────────────────────────────────────────────────

    def is_query_disabled(self, query: str) -> bool:
        return query in self.disabled_queries

    def add_query(self, query: str):
        if query not in self.queries:
            self.queries.append(query)

    def remove_query(self, query: str):
        if query in self.queries:
            self.queries.remove(query)
        self.items_by_query.pop(query, None)
        if query in self.disabled_queries:
            self.disabled_queries.remove(query)

    def disable_query(self, query: str):
        self.disabled_queries.append(query)
        # clear history so re-enabling the query will notify fresh results
        self.items_by_query.pop(query, None)

    def enable_query(self, query: str):
        if query in self.disabled_queries:
            self.disabled_queries.remove(query)

    # ── item tracking ─────────────────────────────────────────────────────────

    def has_item(self, query: str, item_id: str) -> bool:
        return item_id in self.items_by_query.get(query, [])

    def add_item(self, query: str, item_id: str):
        seen = set(self.items_by_query.get(query, []))
        seen.add(item_id)
        self.items_by_query[query] = list(seen)

    def total_tracked(self) -> int:
        return len({item_id for ids in self.items_by_query.values() for item_id in ids})
