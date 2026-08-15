"""
handler.py — Core message inspection and filtering logic.
Matches incoming Pyrogram messages against include/exclude keywords with hot-reload and anti-duplicate cooldown.
"""

import logging
import re
from typing import Optional, Set
from pyrogram.types import Message
from config import Config
from cooldown import CooldownManager
from notifier import Notifier

logger = logging.getLogger("WTBRadar.handler")


class MessageProcessor:
    def __init__(self, config: Config, cooldown: CooldownManager, notifier: Notifier):
        self.config = config
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

        kw_lower = kw.lower()
        text_lower = text.lower()

        # For keywords made of pure word chars (letters/digits/underscore),
        # use word-boundary regex — works for ALL lengths including 'am', 'cc', 'yt'
        if re.match(r"^[\w]+$", kw, re.UNICODE):
            pattern = r"(?<![a-zA-Z0-9_])" + re.escape(kw_lower) + r"(?![a-zA-Z0-9_])"
            return bool(re.search(pattern, text_lower))

        # For multi-word phrases (e.g. "yt famp"), match whole phrase with boundaries
        if re.match(r"^[\w\s]+$", kw, re.UNICODE):
            pattern = r"(?<![a-zA-Z0-9_])" + re.escape(kw_lower) + r"(?![a-zA-Z0-9_])"
            return bool(re.search(pattern, text_lower))

        # For keywords with special symbols (#wtb, @channel), simple substring match
        return kw_lower in text_lower

    def check_match(self, text: str, include_set: Set[str], exclude_set: Set[str]) -> Optional[str]:
        """
        Returns matched include keyword if message passes all filters, else None.
        """
        # 1. Check excludes first — if ANY exclude keyword found, discard message
        for exc in exclude_set:
            if exc and self.contains_word(text, exc):
                logger.debug(f"Message excluded by keyword: '{exc}'")
                return None

        # 2. Check include keywords
        for inc in include_set:
            if inc and self.contains_word(text, inc):
                return inc

        return None

    async def process_message(self, message: Message):
        """Main handler for Pyrogram incoming messages from channels."""
        try:
            if not message.text and not message.caption:
                logger.debug("Skipped: message has no text/caption")
                return

            text = message.text or message.caption or ""
            chat = message.chat
            if not chat:
                return

            chat_id = chat.id
            msg_id = message.id
            channel_title = chat.title or str(chat_id)

            logger.info(f"🔍 Checking message from '{channel_title}': {text[:80]!r}")

            # 1. Anti-duplicate Cooldown Check
            if self.cooldown.is_on_cooldown(chat_id, msg_id):
                logger.debug(f"Skipped: cooldown active for msg {msg_id}")
                return

            # 2. Hot-Reload Keywords & Excludes
            include_keywords = self.config.keywords
            exclude_keywords = self.config.excludes

            if not include_keywords:
                logger.debug("Skipped: no active keywords configured")
                return

            # 3. Check Keyword Match
            matched_kw = self.check_match(text, include_keywords, exclude_keywords)
            if not matched_kw:
                logger.debug(f"No keyword match for: {text[:60]!r}")
                return

            # Mark in cooldown cache
            self.cooldown.mark(chat_id, msg_id)

            # 4. Construct Direct Post Link
            channel_username = chat.username
            post_url = None

            if channel_username:
                post_url = f"https://t.me/{channel_username}/{msg_id}"
            else:
                raw_id = str(chat_id)
                if raw_id.startswith("-100"):
                    clean_id = raw_id[4:]
                    post_url = f"https://t.me/c/{clean_id}/{msg_id}"

            logger.info(f"✅ MATCH! Channel: '{channel_title}' | Keyword: '{matched_kw}' | Text: {text[:60]!r}")

            # 5. Send Notification
            await self.notifier.send_match_notification(
                channel_title=channel_title,
                channel_username=channel_username,
                message_text=text,
                message_id=msg_id,
                matched_keyword=matched_kw,
                post_url=post_url
            )
        except Exception as e:
            logger.error(f"Error processing channel message: {e}", exc_info=True)
