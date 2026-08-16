"""
handler.py — Core message inspection and filtering logic.
Matches incoming Pyrogram messages against include/exclude keywords.
Extracts WIB timestamp and passes to Notifier for clean notification.
"""

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Set

from pyrogram.types import Message

from config import Config
from cooldown import CooldownManager
from notifier import Notifier

logger = logging.getLogger("WTBRadar.handler")

WIB = timezone(timedelta(hours=7))


class MessageProcessor:
    def __init__(self, config: Config, cooldown: CooldownManager, notifier: Notifier):
        self.config   = config
        self.cooldown = cooldown
        self.notifier = notifier

    def contains_word(self, text: str, keyword: str) -> bool:
        """
        Checks if text contains keyword as a WHOLE WORD (not embedded inside another word).
        Case-insensitive.

        Examples:
          keyword='am'  → matches "am bagus"   ✅
          keyword='am'  → NO match "deskcam"   ❌ (embedded inside word)
          keyword='cc'  → matches "cc dong"    ✅
          keyword='cc'  → NO match "acc"       ❌ (embedded inside word)
        """
        kw = keyword.strip()
        if not kw:
            return False

        kw_lower   = kw.lower()
        text_lower = text.lower()

        # Pure word chars (letters / digits / underscore) → strict boundary check
        if re.match(r"^[\w]+$", kw, re.UNICODE):
            pattern = r"(?<![a-zA-Z0-9_])" + re.escape(kw_lower) + r"(?![a-zA-Z0-9_])"
            return bool(re.search(pattern, text_lower))

        # Multi-word phrases like "yt famp" → boundary on phrase edges
        if re.match(r"^[\w\s]+$", kw, re.UNICODE):
            pattern = r"(?<![a-zA-Z0-9_])" + re.escape(kw_lower) + r"(?![a-zA-Z0-9_])"
            return bool(re.search(pattern, text_lower))

        # Keywords with special symbols (#wtb, @channel) → simple substring
        return kw_lower in text_lower

    def check_match(self, text: str, include_set: Set[str], exclude_set: Set[str]) -> Optional[str]:
        """
        Returns matched include keyword if message passes all filters, else None.
        Exclusions are checked first.
        """
        for exc in exclude_set:
            if exc and self.contains_word(text, exc):
                logger.debug(f"Message excluded by keyword: '{exc}'")
                return None

        for inc in include_set:
            if inc and self.contains_word(text, inc):
                return inc

        return None

    async def process_message(self, message: Message):
        """Main handler for incoming channel messages."""
        try:
            if not message.text and not message.caption:
                return

            text = message.text or message.caption or ""
            chat = message.chat
            if not chat:
                return

            chat_id       = chat.id
            msg_id        = message.id
            channel_title = chat.title or str(chat_id)

            logger.info(f"🔍 Checking [{channel_title}]: {text[:80]!r}")

            # 1. Anti-duplicate cooldown
            if self.cooldown.is_on_cooldown(chat_id, msg_id):
                return

            # 2. Hot-reload keywords & excludes
            include_keywords = self.config.keywords
            exclude_keywords = self.config.excludes

            if not include_keywords:
                return

            # 3. Keyword match
            matched_kw = self.check_match(text, include_keywords, exclude_keywords)
            if not matched_kw:
                return

            self.cooldown.mark(chat_id, msg_id)

            # 4. Build post URL
            channel_username = chat.username
            post_url = None
            if channel_username:
                post_url = f"https://t.me/{channel_username}/{msg_id}"
            else:
                raw_id = str(chat_id)
                if raw_id.startswith("-100"):
                    post_url = f"https://t.me/c/{raw_id[4:]}/{msg_id}"

            # 5. Extract WIB timestamp from message
            time_str = None
            try:
                msg_date = message.date  # UTC datetime or Unix int
                if isinstance(msg_date, int):
                    msg_date = datetime.fromtimestamp(msg_date, tz=timezone.utc)
                elif msg_date.tzinfo is None:
                    msg_date = msg_date.replace(tzinfo=timezone.utc)
                wib_time = msg_date.astimezone(WIB)
                time_str = wib_time.strftime("%H:%M")
            except Exception:
                pass  # time_str stays None — notifier handles gracefully

            logger.info(f"✅ MATCH! Channel: '{channel_title}' | KW: '{matched_kw}' | {text[:60]!r}")

            # 6. Send notification
            await self.notifier.send_match_notification(
                channel_title    = channel_title,
                channel_username = channel_username,
                message_text     = text,
                message_id       = msg_id,
                matched_keyword  = matched_kw,
                post_url         = post_url,
                time_str         = time_str,
            )
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
