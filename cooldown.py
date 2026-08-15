"""
cooldown.py — In-memory anti-duplicate notification cache.
Uses (chat_id, message_id) as key with a configurable TTL.
Automatically cleans up expired entries to prevent memory leaks.
"""

import time
from typing import Dict, Tuple


class CooldownManager:
    """
    Prevents the same Telegram message from triggering multiple notifications.

    Key   : (chat_id, message_id)
    Value : timestamp of last notification (Unix float)
    """

    def __init__(self, ttl_seconds: int = 30):
        self.ttl = ttl_seconds
        self._cache: Dict[Tuple[int, int], float] = {}
        self._cleanup_counter = 0

    def is_on_cooldown(self, chat_id: int, message_id: int) -> bool:
        """Return True if this message was already notified within the TTL window."""
        key = (chat_id, message_id)
        ts = self._cache.get(key)
        if ts is not None and (time.time() - ts) < self.ttl:
            return True
        return False

    def mark(self, chat_id: int, message_id: int):
        """Mark a message as notified. Auto-cleans expired entries every 100 marks."""
        self._cache[(chat_id, message_id)] = time.time()
        self._cleanup_counter += 1
        if self._cleanup_counter >= 100:
            self._cleanup()
            self._cleanup_counter = 0

    def _cleanup(self):
        """Remove entries that are older than 2× TTL."""
        cutoff = time.time() - (self.ttl * 2)
        expired_keys = [k for k, v in self._cache.items() if v < cutoff]
        for k in expired_keys:
            del self._cache[k]
