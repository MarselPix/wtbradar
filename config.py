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
        if keyword in self.keywords:
            return False
        with open(KEYWORDS_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n{keyword}")
        logger.info(f"Added keyword: {keyword}")
        return True

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

    def add_exclude(self, keyword: str) -> bool:
        keyword = keyword.strip().lower()
        if keyword in self.excludes:
            return False
        with open(EXCLUDE_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n{keyword}")
        logger.info(f"Added exclude: {keyword}")
        return True

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
