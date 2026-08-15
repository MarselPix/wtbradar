"""
config.py — Configuration manager with hot-reload support.
Loads config.json for credentials/settings, and reads
keywords.txt / exclude.txt fresh on every access (hot-reload).
"""

import json
import logging
from pathlib import Path
from typing import List, Set

logger = logging.getLogger("WTBRadar.config")

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
KEYWORDS_FILE = BASE_DIR / "keywords.txt"
EXCLUDE_FILE = BASE_DIR / "exclude.txt"


class Config:
    def __init__(self):
        self.data: dict = {}
        self.load()

    # ─── Core Load/Save ───────────────────────────────────────────────────────

    def load(self):
        """Load config.json from disk."""
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            self.data = json.load(f)

    def save(self):
        """Persist config.json to disk."""
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    # ─── Keywords (hot-reload) ─────────────────────────────────────────────────

    @property
    def keywords(self) -> Set[str]:
        """Read keywords.txt fresh (hot-reload). Ignores blank lines and comments."""
        result: Set[str] = set()
        if KEYWORDS_FILE.exists():
            with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    word = line.strip().lower()
                    if word and not word.startswith("#"):
                        result.add(word)
        return result

    @property
    def excludes(self) -> Set[str]:
        """Read exclude.txt fresh (hot-reload). Ignores blank lines and comments."""
        result: Set[str] = set()
        if EXCLUDE_FILE.exists():
            with open(EXCLUDE_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    word = line.strip().lower()
                    if word and not word.startswith("#"):
                        result.add(word)
        return result

    def add_keyword(self, keyword: str) -> bool:
        keyword = keyword.strip().lower()
        if not keyword or keyword in self.keywords:
            return False
        with open(KEYWORDS_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n{keyword}")
        logger.info(f"Added keyword: {keyword}")
        return True

    def add_keywords_bulk(self, raw_input: str) -> Tuple[int, int]:
        """Parses comma or newline separated text and adds keywords. Returns (added_count, skipped_count)."""
        items = [k.strip().lower() for k in re.split(r"[,\n]", raw_input) if k.strip()]
        added = 0
        skipped = 0
        current = self.keywords
        new_items = []
        for item in items:
            if item and item not in current and item not in new_items:
                new_items.append(item)
                added += 1
            else:
                skipped += 1

        if new_items:
            with open(KEYWORDS_FILE, "a", encoding="utf-8") as f:
                for item in new_items:
                    f.write(f"\n{item}")
        return added, skipped

    def remove_keyword(self, keyword: str) -> bool:
        keyword = keyword.strip().lower()
        current = self.keywords
        if keyword not in current:
            return False
        remaining = sorted(k for k in current if k != keyword)
        with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
            f.write("# WTB Include Keywords\n")
            f.write("\n".join(remaining))
        logger.info(f"Removed keyword: {keyword}")
        return True

    def remove_keywords_bulk(self, raw_input: str) -> Tuple[int, int]:
        """Parses comma or newline separated text and removes keywords. Returns (removed_count, not_found_count)."""
        to_remove = set(k.strip().lower() for k in re.split(r"[,\n]", raw_input) if k.strip())
        current = self.keywords
        removed = len(current.intersection(to_remove))
        not_found = len(to_remove) - removed

        remaining = sorted(k for k in current if k not in to_remove)
        with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
            f.write("# WTB Include Keywords\n")
            if remaining:
                f.write("\n".join(remaining))
        return removed, not_found

    def clear_keywords(self):
        """Wipe all keywords."""
        with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
            f.write("# WTB Include Keywords\n")

    def add_exclude(self, keyword: str) -> bool:
        keyword = keyword.strip().lower()
        if not keyword or keyword in self.excludes:
            return False
        with open(EXCLUDE_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n{keyword}")
        logger.info(f"Added exclude: {keyword}")
        return True

    def add_excludes_bulk(self, raw_input: str) -> Tuple[int, int]:
        """Parses comma or newline separated text and adds excludes. Returns (added_count, skipped_count)."""
        items = [k.strip().lower() for k in re.split(r"[,\n]", raw_input) if k.strip()]
        added = 0
        skipped = 0
        current = self.excludes
        new_items = []
        for item in items:
            if item and item not in current and item not in new_items:
                new_items.append(item)
                added += 1
            else:
                skipped += 1

        if new_items:
            with open(EXCLUDE_FILE, "a", encoding="utf-8") as f:
                for item in new_items:
                    f.write(f"\n{item}")
        return added, skipped

    def remove_exclude(self, keyword: str) -> bool:
        keyword = keyword.strip().lower()
        current = self.excludes
        if keyword not in current:
            return False
        remaining = sorted(k for k in current if k != keyword)
        with open(EXCLUDE_FILE, "w", encoding="utf-8") as f:
            f.write("# Exclude Keywords\n")
            f.write("\n".join(remaining))
        logger.info(f"Removed exclude: {keyword}")
        return True

    def remove_excludes_bulk(self, raw_input: str) -> Tuple[int, int]:
        """Parses comma or newline separated text and removes excludes. Returns (removed_count, not_found_count)."""
        to_remove = set(k.strip().lower() for k in re.split(r"[,\n]", raw_input) if k.strip())
        current = self.excludes
        removed = len(current.intersection(to_remove))
        not_found = len(to_remove) - removed

        remaining = sorted(k for k in current if k not in to_remove)
        with open(EXCLUDE_FILE, "w", encoding="utf-8") as f:
            f.write("# Exclude Keywords\n")
            if remaining:
                f.write("\n".join(remaining))
        return removed, not_found

    def clear_excludes(self):
        """Wipe all excludes."""
        with open(EXCLUDE_FILE, "w", encoding="utf-8") as f:
            f.write("# Exclude Keywords\n")

    def clear_channels(self):
        """Wipe all monitored channels."""
        self.load()
        self.data["channels"] = []
        self.save()

    # ─── Channel Management ────────────────────────────────────────────────────

    def add_channel(self, channel) -> bool:
        """Add a channel identifier (string or int) to the monitored list."""
        self.load()
        channel_str = str(channel)
        existing = [str(c) for c in self.data["channels"]]
        if channel_str in existing:
            return False
        self.data["channels"].append(channel)
        self.save()
        logger.info(f"Channel added to config: {channel}")
        return True

    def remove_channel(self, channel: str) -> bool:
        """Remove a channel by its identifier string."""
        self.load()
        channel_lower = channel.strip().lower()
        # Normalize: strip leading @
        channel_bare = channel_lower.lstrip("@")

        to_remove = None
        for ch in self.data["channels"]:
            ch_str = str(ch).lower().lstrip("@")
            if ch_str == channel_bare:
                to_remove = ch
                break

        if to_remove is not None:
            self.data["channels"].remove(to_remove)
            self.save()
            logger.info(f"Channel removed from config: {channel}")
            return True
        return False

    @property
    def monitored_channels(self) -> List:
        """Always returns a fresh copy from disk."""
        self.load()
        return list(self.data.get("channels", []))

    # ─── Typed Accessors ──────────────────────────────────────────────────────

    @property
    def api_id(self) -> int:
        return int(self.data["api_id"])

    @property
    def api_hash(self) -> str:
        return self.data["api_hash"]

    @property
    def bot_token(self) -> str:
        return self.data["bot_token"]

    @property
    def target_chat_id(self) -> int:
        return int(self.data["target_chat_id"])

    @property
    def cooldown_seconds(self) -> int:
        return int(self.data.get("cooldown_seconds", 30))

    @property
    def max_preview(self) -> int:
        return int(self.data.get("max_message_preview", 300))
