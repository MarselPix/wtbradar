"""
notifier.py — Sends instant notifications via Telegram Bot API using httpx.
Uses clean Compact Card format (Option A) with minimal emoji.
"""

import html
import logging
import httpx
from typing import Optional

logger = logging.getLogger("WTBRadar.notifier")


class Notifier:
    def __init__(self, bot_token: str, target_chat_id: int):
        self.bot_token = bot_token
        self.target_chat_id = target_chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    async def send_match_notification(
        self,
        channel_title: str,
        channel_username: Optional[str],
        message_text: str,
        message_id: int,
        matched_keyword: str,
        post_url: Optional[str] = None,
        time_str: Optional[str] = None,
    ) -> bool:
        """
        Sends a clean Compact Card notification to the target chat.
        Format (Option A):
            ⚡ WTB Match Ditemukan

            Channel  : BASE WIB
            Keyword  : capcut
            Waktu    : 08:42 WIB

            "cari capcut premium dong..."

            [→ Lihat Postingan]
        """
        # ── Resolve post URL ──────────────────────────────────────────────────
        if not post_url:
            if channel_username:
                post_url = f"https://t.me/{channel_username}/{message_id}"
            else:
                clean_id = str(channel_title).lstrip("-").replace("100", "", 1) \
                    if str(channel_title).startswith("-100") else str(channel_title)
                post_url = f"https://t.me/c/{clean_id}/{message_id}"

        # ── Clean & truncate message ──────────────────────────────────────────
        snippet = message_text.strip()
        if len(snippet) > 280:
            snippet = snippet[:277] + "..."

        # ── Build formatted message ───────────────────────────────────────────
        safe_title   = html.escape(str(channel_title) if channel_title else "Channel")
        safe_keyword = html.escape(matched_keyword)
        safe_snippet = html.escape(snippet)
        time_line    = f"\n<b>Waktu</b>    : {html.escape(time_str)} WIB" if time_str else ""

        body = (
            "⚡ <b>WTB Match Ditemukan</b>\n\n"
            f"<b>Channel</b>  : {safe_title}\n"
            f"<b>Keyword</b>  : <code>{safe_keyword}</code>"
            f"{time_line}\n\n"
            f"\"<i>{safe_snippet}</i>\""
        )

        payload = {
            "chat_id": self.target_chat_id,
            "text": body,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "→ Lihat Postingan", "url": post_url}
                ]]
            }
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(self.api_url, json=payload)
                if res.status_code == 200 and res.json().get("ok"):
                    logger.info(f"Notification sent: msg {message_id} in {channel_title}")
                    return True
                else:
                    logger.error(f"Failed to send notification: {res.text}")
                    return False
        except Exception as e:
            logger.error(f"Error sending notification: {e}")
            return False

    async def send_system_message(self, text: str, reply_markup: Optional[dict] = None) -> bool:
        """Sends administrative system/status message to target user chat."""
        payload = {
            "chat_id": self.target_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(self.api_url, json=payload)
                return res.status_code == 200 and res.json().get("ok")
        except Exception as e:
            logger.error(f"Error sending system message: {e}")
            return False
