"""
bot_runner.py — Interactive Management Bot via Telegram Bot API.
Allows user to control channels, keywords, excludes, and view status directly in Telegram chat.
Uses lightweight asyncio HTTP polling with httpx (no external bot framework needed).
"""

import asyncio
import html
import logging
import httpx
from typing import Optional
from pyrogram import Client
from config import Config
from channel_manager import resolve_and_join_channel

logger = logging.getLogger("WTBRadar.bot_runner")


class BotRunner:
    def __init__(self, config: Config, pyrogram_client: Client):
        self.config = config
        self.app = pyrogram_client
        self.bot_token = config.bot_token
        self.target_chat_id = config.target_chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._offset = 0
        self.is_running = False
        self.pending_state: Optional[str] = None

    def get_main_keyboard(self) -> dict:
        """Returns custom persistent ReplyKeyboardMarkup for chat navigation."""
        return {
            "keyboard": [
                [{"text": "📡 List Channel"}, {"text": "➕ Tambah Channel"}, {"text": "➖ Hapus Channel"}],
                [{"text": "🔑 WTB Keywords"}, {"text": "➕ Tambah Keyword"}, {"text": "➖ Hapus Keyword"}],
                [{"text": "🚫 Exclude Filter"}, {"text": "➕ Tambah Exclude"}, {"text": "➖ Hapus Exclude"}],
                [{"text": "📊 Status Bot"}, {"text": "❓ Help / Menu"}]
            ],
            "resize_keyboard": True,
            "is_persistent": True
        }

    async def send_reply(self, text: str, reply_to_message_id: Optional[int] = None, show_keyboard: bool = True):
        """Helper to send a message back to the target user with reply markup."""
        payload = {
            "chat_id": self.target_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if show_keyboard:
            payload["reply_markup"] = self.get_main_keyboard()

        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{self.base_url}/sendMessage", json=payload)
        except Exception as e:
            logger.error(f"Failed to send bot reply: {e}")

    async def handle_update(self, update: dict):
        """Process incoming Telegram update from user."""
        message = update.get("message")
        if not message:
            return

        chat_id = message.get("chat", {}).get("id")
        if chat_id != self.target_chat_id:
            logger.warning(f"Unauthorized command attempt from chat ID {chat_id}")
            return

        text = message.get("text", "").strip()
        msg_id = message.get("message_id")
        if not text:
            return

        # ─── Navigation Button Mappings ───────────────────────────────────────

        if text == "📡 List Channel":
            self.pending_state = None
            await self.cmd_list_channels(msg_id)
            return

        elif text == "➕ Tambah Channel":
            self.pending_state = "awaiting_add_channel"
            await self.send_reply(
                "📥 <b>TAMBAH CHANNEL</b>\n\n"
                "Silakan kirimkan username channel / link invite / chat ID:\n"
                "<i>Contoh: @channelwtb atau https://t.me/+invite_link</i>",
                msg_id
            )
            return

        elif text == "➖ Hapus Channel":
            self.pending_state = "awaiting_del_channel"
            await self.send_reply(
                "📤 <b>HAPUS CHANNEL</b>\n\n"
                "Silakan kirimkan username atau chat ID channel yang mau dihapus:\n"
                "<i>Contoh: @channelwtb</i>",
                msg_id
            )
            return

        elif text in ["🔑 WTB Keywords", "🔑 Keywords WTB"]:
            self.pending_state = None
            await self.cmd_list_keywords(msg_id)
            return

        elif text == "➕ Tambah Keyword":
            self.pending_state = "awaiting_add_kw"
            await self.send_reply(
                "➕ <b>TAMBAH KEYWORD WTB</b>\n\n"
                "Silakan kirimkan kata kunci WTB baru yang ingin dicari:\n"
                "<i>Contoh: canva pro</i>",
                msg_id
            )
            return

        elif text == "➖ Hapus Keyword":
            self.pending_state = "awaiting_del_kw"
            await self.send_reply(
                "➖ <b>HAPUS KEYWORD WTB</b>\n\n"
                "Silakan kirimkan kata kunci WTB yang ingin dihapus:\n"
                "<i>Contoh: canva</i>",
                msg_id
            )
            return

        elif text == "🚫 Exclude Filter":
            self.pending_state = None
            await self.cmd_list_excludes(msg_id)
            return

        elif text == "➕ Tambah Exclude":
            self.pending_state = "awaiting_add_ex"
            await self.send_reply(
                "🚫 <b>TAMBAH EXCLUDE FILTER</b>\n\n"
                "Silakan kirimkan kata filter yang ingin DIABAIKAN (misal WTS):\n"
                "<i>Contoh: wts</i>",
                msg_id
            )
            return

        elif text == "➖ Hapus Exclude":
            self.pending_state = "awaiting_del_ex"
            await self.send_reply(
                "🗑️ <b>HAPUS EXCLUDE FILTER</b>\n\n"
                "Silakan kirimkan kata filter yang ingin dihapus dari abaikan:\n"
                "<i>Contoh: wts</i>",
                msg_id
            )
            return

        elif text in ["📊 Status Bot", "📊 Status"]:
            self.pending_state = None
            await self.cmd_status(msg_id)
            return

        elif text in ["❓ Help / Menu", "❓ Help", "/start", "/help"]:
            self.pending_state = None
            await self.cmd_help(msg_id)
            return

        # ─── Stateful Input Handling ─────────────────────────────────────────

        if self.pending_state:
            state = self.pending_state
            self.pending_state = None

            if state == "awaiting_add_channel":
                await self.cmd_add_channel(text, msg_id)
                return
            elif state == "awaiting_del_channel":
                await self.cmd_del_channel(text, msg_id)
                return
            elif state == "awaiting_add_kw":
                await self.cmd_add_kw(text, msg_id)
                return
            elif state == "awaiting_del_kw":
                await self.cmd_del_kw(text, msg_id)
                return
            elif state == "awaiting_add_ex":
                await self.cmd_add_ex(text, msg_id)
                return
            elif state == "awaiting_del_ex":
                await self.cmd_del_ex(text, msg_id)
                return

        # ─── Slashed Command Router Fallback ──────────────────────────────────

        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            command = parts[0].lower().replace("@" + self.bot_token.split(":")[0], "")
            args = parts[1].strip() if len(parts) > 1 else ""

            if command == "/status":
                await self.cmd_status(msg_id)
            elif command == "/channels":
                await self.cmd_list_channels(msg_id)
            elif command == "/addchannel":
                await self.cmd_add_channel(args, msg_id)
            elif command == "/delchannel":
                await self.cmd_del_channel(args, msg_id)
            elif command == "/keywords":
                await self.cmd_list_keywords(msg_id)
            elif command == "/addkw":
                await self.cmd_add_kw(args, msg_id)
            elif command == "/delkw":
                await self.cmd_del_kw(args, msg_id)
            elif command == "/excludes":
                await self.cmd_list_excludes(msg_id)
            elif command == "/addex":
                await self.cmd_add_ex(args, msg_id)
            elif command == "/delex":
                await self.cmd_del_ex(args, msg_id)
            return

        # Unrecognized input with no pending state
        await self.send_reply(
            "💡 Gunakan tombol navigasi di bawah untuk mengontrol bot, atau ketik <code>/help</code>.",
            msg_id
        )

    # ─── Command Implementation ───────────────────────────────────────────────

    async def cmd_help(self, msg_id: int):
        help_text = (
            "🤖 <b>WTB RADAR CONTROL PANEL</b>\n\n"
            "Gunakan **tombol navigasi di keyboard bawah** untuk kontrol instan tanpa ketik perintah!\n\n"
            "<b>📡 Channel Controls:</b>\n"
            "• 📡 List Channel | ➕ Tambah | ➖ Hapus\n\n"
            "<b>🔑 WTB Keywords:</b>\n"
            "• 🔑 WTB Keywords | ➕ Tambah | ➖ Hapus\n\n"
            "<b>🚫 Exclusion Filters (WTS/Jual):</b>\n"
            "• 🚫 Exclude Filter | ➕ Tambah | ➖ Hapus\n\n"
            "<b>📊 System:</b>\n"
            "• 📊 Status Bot — Cek RAM & koneksi"
        )
        await self.send_reply(help_text, msg_id)

    async def cmd_status(self, msg_id: int):
        channels = self.config.monitored_channels
        kws = self.config.keywords
        exs = self.config.excludes

        status_text = (
            "🟢 <b>WTB RADAR STATUS: RUNNING</b>\n\n"
            f"📡 <b>Monitored Channels:</b> {len(channels)}\n"
            f"🔑 <b>WTB Keywords:</b> {len(kws)}\n"
            f"🚫 <b>Exclusion Keywords:</b> {len(exs)}\n"
            f"⏱️ <b>Cooldown:</b> {self.config.cooldown_seconds}s\n"
            f"⚡ <b>Engine:</b> Pyrogram Async (Termux Ready)"
        )
        await self.send_reply(status_text, msg_id)

    async def cmd_list_channels(self, msg_id: int):
        channels = self.config.monitored_channels
        if not channels:
            await self.send_reply("📡 Belum ada channel yang dimonitor. Klik <b>➕ Tambah Channel</b> untuk menambah.", msg_id)
            return

        lines = ["<b>📡 Channels Dimonitor:</b>"]
        for i, ch in enumerate(channels, 1):
            lines.append(f"{i}. <code>{ch}</code>")

        await self.send_reply("\n".join(lines), msg_id)

    async def cmd_add_channel(self, args: str, msg_id: int):
        if not args:
            await self.send_reply("⚠️ Kirimkan username/link channel. Contoh: <code>@channelwtb</code>", msg_id)
            return

        await self.send_reply(f"⏳ Sedang memproses dan mengecek channel <code>{html.escape(args)}</code>...", msg_id)
        success, msg, identifier = await resolve_and_join_channel(self.app, args)

        if success:
            added = self.config.add_channel(identifier)
            if added:
                await self.send_reply(f"{msg}\n✅ Berhasil disimpan ke konfigurasi monitor!", msg_id)
            else:
                await self.send_reply(f"⚠️ Channel <code>{identifier}</code> sudah ada dalam daftar monitor.", msg_id)
        else:
            await self.send_reply(msg, msg_id)

    async def cmd_del_channel(self, args: str, msg_id: int):
        if not args:
            await self.send_reply("⚠️ Kirimkan username/ID channel. Contoh: <code>@channelwtb</code>", msg_id)
            return

        removed = self.config.remove_channel(args)
        if removed:
            await self.send_reply(f"✅ Channel <code>{html.escape(args)}</code> berhasil dihapus dari daftar monitor.", msg_id)
        else:
            await self.send_reply(f"❌ Channel <code>{html.escape(args)}</code> tidak ditemukan di daftar monitor.", msg_id)

    async def cmd_list_keywords(self, msg_id: int):
        kws = sorted(list(self.config.keywords))
        if not kws:
            await self.send_reply("🔑 Belum ada keyword WTB. Klik <b>➕ Tambah Keyword</b>.", msg_id)
            return

        kw_fmt = ", ".join(f"<code>{html.escape(k)}</code>" for k in kws)
        await self.send_reply(f"<b>🔑 Keyword WTB Aktif ({len(kws)}):</b>\n\n{kw_fmt}", msg_id)

    async def cmd_add_kw(self, args: str, msg_id: int):
        if not args:
            await self.send_reply("⚠️ Kirimkan kata kunci. Contoh: <code>canva</code>", msg_id)
            return

        added = self.config.add_keyword(args)
        if added:
            await self.send_reply(f"✅ Keyword WTB <code>{html.escape(args)}</code> berhasil ditambahkan!", msg_id)
        else:
            await self.send_reply(f"⚠️ Keyword <code>{html.escape(args)}</code> sudah ada.", msg_id)

    async def cmd_del_kw(self, args: str, msg_id: int):
        if not args:
            await self.send_reply("⚠️ Kirimkan kata kunci. Contoh: <code>canva</code>", msg_id)
            return

        removed = self.config.remove_keyword(args)
        if removed:
            await self.send_reply(f"✅ Keyword <code>{html.escape(args)}</code> berhasil dihapus!", msg_id)
        else:
            await self.send_reply(f"❌ Keyword <code>{html.escape(args)}</code> tidak ditemukan.", msg_id)

    async def cmd_list_excludes(self, msg_id: int):
        exs = sorted(list(self.config.excludes))
        if not exs:
            await self.send_reply("🚫 Belum ada exclusion keyword.", msg_id)
            return

        ex_fmt = ", ".join(f"<code>{html.escape(e)}</code>" for e in exs)
        await self.send_reply(f"<b>🚫 Exclusion Keywords ({len(exs)}):</b>\n\n{ex_fmt}", msg_id)

    async def cmd_add_ex(self, args: str, msg_id: int):
        if not args:
            await self.send_reply("⚠️ Kirimkan kata filter. Contoh: <code>wts</code>", msg_id)
            return

        added = self.config.add_exclude(args)
        if added:
            await self.send_reply(f"✅ Exclusion keyword <code>{html.escape(args)}</code> berhasil ditambahkan!", msg_id)
        else:
            await self.send_reply(f"⚠️ Exclusion keyword <code>{html.escape(args)}</code> sudah ada.", msg_id)

    async def cmd_del_ex(self, args: str, msg_id: int):
        if not args:
            await self.send_reply("⚠️ Kirimkan kata filter. Contoh: <code>wts</code>", msg_id)
            return

        removed = self.config.remove_exclude(args)
        if removed:
            await self.send_reply(f"✅ Exclusion keyword <code>{html.escape(args)}</code> berhasil dihapus!", msg_id)
        else:
            await self.send_reply(f"❌ Exclusion keyword <code>{html.escape(args)}</code> tidak ditemukan.", msg_id)

    # ─── Long Polling Loop ────────────────────────────────────────────────────

    async def start_polling(self):
        """Runs async polling for Telegram Bot API updates."""
        self.is_running = True
        logger.info("Management Bot polling started...")
        async with httpx.AsyncClient(timeout=30.0) as client:
            while self.is_running:
                try:
                    res = await client.get(
                        f"{self.base_url}/getUpdates",
                        params={"offset": self.str_offset if hasattr(self, 'str_offset') else self._offset, "timeout": 10}
                    )
                    if res.status_code == 200:
                        data = res.json()
                        if data.get("ok"):
                            for update in data.get("result", []):
                                self._offset = update["update_id"] + 1
                                asyncio.create_task(self.handle_update(update))
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"Bot polling exception (retrying...): {e}")
                    await asyncio.sleep(3)
