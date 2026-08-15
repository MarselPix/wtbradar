"""
notifier.py — Sends instant notifications via Telegram Bot API using httpx.
Uses HTML formatting and includes an inline button linking directly to the channel post.
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
        post_url: Optional[str] = None
    ) -> bool:
        """
        Sends formatted Telegram notification via Bot API.
        """
        # Determine direct post URL
        if not post_url:
            if channel_username:
                post_url = f"https://t.me/{channel_username}/{message_id}"
            else:
                clean_id = str(channel_title).replace("-100", "").lstrip("-")
                post_url = f"https://t.me/c/{clean_id}/{message_id}"

        # Clean/truncate message text
        clean_text = message_text.strip()
        if len(clean_text) > 300:
            clean_text = clean_text[:297] + "..."
        safe_text = html.escape(clean_text)

        safe_title = html.escape(str(channel_title) if channel_title else "Channel")
        safe_keyword = html.escape(matched_keyword)

        formatted_msg = (
            f"🎯 <b>WTB RADAR MATCHED!</b>\n\n"
            f"📌 <b>Channel:</b> {safe_title}\n"
            f"🔑 <b>Matched Keyword:</b> <code>{safe_keyword}</code>\n\n"
            f"💬 <b>Message Content:</b>\n<i>\"{safe_text}\"</i>"
        )

        payload = {
            "chat_id": self.target_chat_id,
            "text": formatted_msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": "🚀 Buka Postingan / Komentar",
                            "url": post_url
                        }
                    ]
                ]
            }
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(self.api_url, json=payload)
                if res.status_code == 200 and res.json().get("ok"):
                    logger.info(f"Notification sent successfully for msg {message_id} in {channel_title}")
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
            "disable_web_page_preview": True
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
