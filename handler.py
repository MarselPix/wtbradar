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
        Check if text contains keyword using regex word boundary or substring match.
        Case-insensitive.
        """
        pattern = r"\b" + re.escape(keyword) + r"\b"
        return bool(re.search(pattern, text, re.IGNORECASE))

    def check_match(self, text: str, include_set: Set[str], exclude_set: Set[str]) -> Optional[str]:
        """
        Returns the matched include keyword if message is a valid WTB request,
        or None if message doesn't match or contains an exclude keyword.
        """
        text_lower = text.lower()

        # First check exclusions: if ANY exclude keyword matches, reject message
        for exc in exclude_set:
            if self.contains_word(text_lower, exc) or exc in text_lower:
                return None

        # Next check include keywords
        for inc in include_set:
            if self.contains_word(text_lower, inc) or inc in text_lower:
                return inc

        return None

    async def process_message(self, message: Message):
        """Main handler for Pyrogram incoming messages from channels."""
        if not message.text and not message.caption:
            return

        text = message.text or message.caption or ""
        chat = message.chat
        chat_id = chat.id
        msg_id = message.id

        # 1. Anti-duplicate Cooldown Check
        if self.cooldown.is_on_cooldown(chat_id, msg_id):
            return

        # 2. Hot-Reload Keywords & Excludes
        include_keywords = self.config.keywords
        exclude_keywords = self.config.excludes

        if not include_keywords:
            return

        # 3. Check Keyword Match
        matched_kw = self.check_match(text, include_keywords, exclude_keywords)
        if not matched_kw:
            return

        # Mark in cooldown cache
        self.cooldown.mark(chat_id, msg_id)

        # 4. Construct Direct Post Link
        channel_username = chat.username
        channel_title = chat.title or str(chat_id)
        post_url = None

        if channel_username:
            post_url = f"https://t.me/{channel_username}/{msg_id}"
        else:
            # For private channels (ID format -1001234567890 -> 1234567890)
            raw_id = str(chat_id)
            if raw_id.startswith("-100"):
                clean_id = raw_id[4:]
                post_url = f"https://t.me/c/{clean_id}/{msg_id}"

        logger.info(f"MATCH FOUND in {channel_title} (kw: '{matched_kw}'): {text[:50]}...")

        # 5. Send Notification
        await self.notifier.send_match_notification(
            channel_title=channel_title,
            channel_username=channel_username,
            message_text=text,
            message_id=msg_id,
            matched_keyword=matched_kw,
            post_url=post_url
        )
