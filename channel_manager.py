"""
channel_manager.py — Utilities for parsing, adding, and resolving Telegram channels using Pyrogram.
Supports public usernames (@channel), private invite links (t.me/+...), and channel IDs.
"""

import logging
import re
from typing import Optional, Union, Tuple
from pyrogram import Client
from pyrogram.errors import UserAlreadyParticipant, InviteHashExpired, InviteHashInvalid

logger = logging.getLogger("WTBRadar.channel_manager")


def parse_channel_input(raw_input: str) -> Tuple[str, Optional[str]]:
    """
    Parses user input into (type, identifier).
    Types:
    - 'username': @username or t.me/username
    - 'invite_link': t.me/+hash or t.me/joinchat/hash
    - 'id': numeric chat ID (-100...)
    """
    raw = raw_input.strip()

    # Numeric ID
    if re.match(r"^-?\d+$", raw):
        return ("id", raw)

    # Private invite link
    invite_match = re.search(r"t\.me\/(?:\+|\+?joinchat\/)([a-zA-Z0-9_-]+)", raw)
    if invite_match:
        return ("invite_link", invite_match.group(1))

    # Public username / link
    username_match = re.search(r"(?:t\.me\/|@)?([a-zA-Z0-9_]{4,})", raw)
    if username_match:
        username = username_match.group(1)
        # Exclude reserved words
        if username.lower() not in ["joinchat", "addlist"]:
            return ("username", f"@{username}")

    return ("unknown", raw)


async def resolve_and_join_channel(app: Client, raw_input: str) -> Tuple[bool, str, Union[str, int]]:
    """
    Resolves a channel from user input and joins it if necessary.
    Returns: (success: bool, status_message: str, channel_identifier_for_config)
    """
    input_type, parsed = parse_channel_input(raw_input)

    if input_type == "unknown":
        return False, "❌ Format channel/link tidak valid.", raw_input

    try:
        if input_type == "username":
            # Public username
            chat = await app.get_chat(parsed)
            # Try joining to ensure updates are received smoothly
            try:
                await app.join_chat(parsed)
            except UserAlreadyParticipant:
                pass
            except Exception as e:
                logger.debug(f"Join chat info: {e}")

            identifier = f"@{chat.username}" if chat.username else chat.id
            return True, f"✅ Berhasil menambahkan channel: <b>{chat.title}</b> ({identifier})", identifier

        elif input_type == "invite_link":
            # Private invite hash
            try:
                chat = await app.join_chat(parsed)
                return True, f"✅ Berhasil bergabung ke channel private: <b>{chat.title}</b>", chat.id
            except UserAlreadyParticipant:
                chat = await app.get_chat(parsed)
                return True, f"✅ Sudah ada di channel private: <b>{chat.title}</b>", chat.id
            except (InviteHashExpired, InviteHashInvalid):
                return False, "❌ Link invite private sudah kadaluarsa atau tidak valid.", raw_input

        elif input_type == "id":
            chat_id = int(parsed)
            chat = await app.get_chat(chat_id)
            return True, f"✅ Berhasil menemukan channel ID: <b>{chat.title}</b> ({chat_id})", chat_id

    except Exception as e:
        logger.error(f"Failed to resolve channel {raw_input}: {e}")
        return False, f"❌ Gagal memproses channel: {str(e)}", raw_input

    return False, "❌ Gagal memproses channel.", raw_input
