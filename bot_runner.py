"""
bot_runner.py — Interactive Management Bot via Telegram Bot API.
Allows user to control channels, keywords, excludes, and view status directly in Telegram chat.
Uses lightweight asyncio HTTP polling with httpx (no external bot framework needed).
"""

import asyncio
import html
import logging
import re
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
        """Mobile-friendly grid navigation keyboard."""
        return {
            "keyboard": [
                [{"text": "📡 Channel Monitor"}, {"text": "🔑 WTB Keywords"}],
                [{"text": "➕ Tambah Channel"}, {"text": "➕ Tambah Keyword"}],
                [{"text": "➖ Hapus Channel"}, {"text": "➖ Hapus Keyword"}],
                [{"text": "🚫 Exclude Filter"}, {"text": "➕ Exclude"}, {"text": "➖ Exclude"}],
                [{"text": "📊 Status Bot"}, {"text": "❓ Help & Petunjuk"}]
            ],
            "resize_keyboard": True,
            "is_persistent": True
        }

    def get_cancel_keyboard(self) -> dict:
        """Cancel keyboard presented during input states."""
        return {
            "keyboard": [
                [{"text": "❌ Batal / Kembali ke Menu"}]
            ],
            "resize_keyboard": True
        }

    async def send_reply(self, text: str, reply_to_message_id: Optional[int] = None, custom_keyboard: Optional[dict] = None):
        """Helper to send a message back to the target user with specified reply markup."""
        payload = {
            "chat_id": self.target_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if custom_keyboard is not None:
            payload["reply_markup"] = custom_keyboard
        else:
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
        try:
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
        except Exception as e:
            logger.error(f"Error reading update: {e}")
            return

        try:

        # ─── Global Cancel Button Listener ────────────────────────────────────

        if text in ["❌ Batal / Kembali ke Menu", "❌ Batal", "Batal", "batal"]:
            self.pending_state = None
            await self.send_reply(
                "❌ <b>Operasi Dibatalkan</b>\n\nKembali ke menu utama control panel.",
                msg_id
            )
            return

        # ─── Clear All Commands ───────────────────────────────────────────────

        if text.lower() in ["/clearkw", "/resetkw", "reset keyword"]:
            self.config.clear_keywords()
            await self.send_reply("🗑️ <b>Semua Keyword WTB berhasil dikosongkan/direset!</b>", msg_id)
            return

        if text.lower() in ["/clearex", "/resetex", "reset exclude"]:
            self.config.clear_excludes()
            await self.send_reply("🗑️ <b>Semua Exclude Filter berhasil dikosongkan/direset!</b>", msg_id)
            return

        # ─── Navigation Button Mappings ───────────────────────────────────────

        if text in ["📡 Channel Monitor", "📡 List Channel"]:
            self.pending_state = None
            await self.cmd_list_channels(msg_id)
            return

        elif text == "➕ Tambah Channel":
            self.pending_state = "awaiting_add_channel"
            await self.send_reply(
                "📥 <b>TAMBAH CHANNEL MONITOR (BISA BULK)</b>\n"
                "───────────────────────────\n"
                "Silakan kirimkan username / link invite channel (bisa banyak sekaligus dipisah koma atau baris baru):\n\n"
                "• <i>Contoh 1:</i> <code>@channelwtb1, @channelwtb2</code>\n"
                "• <i>Contoh 2:</i> <code>https://t.me/+invite_link</code>",
                msg_id,
                custom_keyboard=self.get_cancel_keyboard()
            )
            return

        elif text == "➖ Hapus Channel":
            self.pending_state = "awaiting_del_channel"
            await self.send_reply(
                "📤 <b>HAPUS CHANNEL MONITOR (BISA BULK)</b>\n"
                "───────────────────────────\n"
                "Silakan kirimkan username atau ID channel yang ingin dihapus (bisa dipisah koma):\n\n"
                "• <i>Contoh:</i> <code>@channelwtb1, @channelwtb2</code>",
                msg_id,
                custom_keyboard=self.get_cancel_keyboard()
            )
            return

        elif text in ["🔑 WTB Keywords", "🔑 Keywords WTB"]:
            self.pending_state = None
            await self.cmd_list_keywords(msg_id)
            return

        elif text == "➕ Tambah Keyword":
            self.pending_state = "awaiting_add_kw"
            await self.send_reply(
                "➕ <b>TAMBAH KEYWORD WTB (SUPPORT BULK / BANYAK)</b>\n"
                "───────────────────────────\n"
                "Kirimkan kata kunci WTB baru. Bisa langsung banyak sekaligus dipisah <b>koma</b> atau <b>baris baru</b>:\n\n"
                "• <i>Contoh:</i> <code>canva, capcut, youtube premium, netflix</code>\n\n"
                "💡 <i>Ketik <code>/clearkw</code> untuk mengosongkan semua keyword secara instan.</i>",
                msg_id,
                custom_keyboard=self.get_cancel_keyboard()
            )
            return

        elif text == "➖ Hapus Keyword":
            self.pending_state = "awaiting_del_kw"
            await self.send_reply(
                "➖ <b>HAPUS KEYWORD WTB (SUPPORT BULK)</b>\n"
                "───────────────────────────\n"
                "Kirimkan kata kunci yang ingin dihapus (pisah dengan koma jika lebih dari satu):\n\n"
                "• <i>Contoh:</i> <code>canva, capcut</code>",
                msg_id,
                custom_keyboard=self.get_cancel_keyboard()
            )
            return

        elif text in ["🚫 Exclude Filter", "🚫 Exclude"]:
            self.pending_state = None
            await self.cmd_list_excludes(msg_id)
            return

        elif text == "➕ Exclude":
            self.pending_state = "awaiting_add_ex"
            await self.send_reply(
                "🚫 <b>TAMBAH EXCLUDE FILTER (SUPPORT BULK)</b>\n"
                "───────────────────────────\n"
                "Kirimkan kata filter yang ingin DIABAIKAN (bisa dipisah koma):\n\n"
                "• <i>Contoh:</i> <code>wts, jual, stok, ready, promo</code>\n\n"
                "💡 <i>Ketik <code>/clearex</code> untuk mengosongkan semua filter abaikan.</i>",
                msg_id,
                custom_keyboard=self.get_cancel_keyboard()
            )
            return

        elif text == "➖ Exclude":
            self.pending_state = "awaiting_del_ex"
            await self.send_reply(
                "🗑️ <b>HAPUS EXCLUDE FILTER (SUPPORT BULK)</b>\n"
                "───────────────────────────\n"
                "Kirimkan kata filter yang ingin dihapus (bisa dipisah koma):\n\n"
                "• <i>Contoh:</i> <code>wts, jual</code>",
                msg_id,
                custom_keyboard=self.get_cancel_keyboard()
            )
            return

        elif text in ["📊 Status Bot", "📊 Status"]:
            self.pending_state = None
            await self.cmd_status(msg_id)
            return

        elif text in ["❓ Help & Petunjuk", "❓ Help", "/start", "/help"]:
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
            elif command == "/clearkw":
                self.config.clear_keywords()
                await self.send_reply("🗑️ Semua keyword WTB berhasil dikosongkan!", msg_id)
            elif command == "/clearex":
                self.config.clear_excludes()
                await self.send_reply("🗑️ Semua exclude filter berhasil dikosongkan!", msg_id)
            elif command == "/excludes":
                await self.cmd_list_excludes(msg_id)
            elif command == "/addex":
                await self.cmd_add_ex(args, msg_id)
            elif command == "/delex":
                await self.cmd_del_ex(args, msg_id)
            return

        # Unrecognized input with no pending state
        await self.send_reply(
            "💡 Gunakan tombol navigasi di bawah untuk mengontrol bot, atau klik <b>❓ Help & Petunjuk</b>.",
            msg_id
        )
        except Exception as e:
            logger.error(f"Error handling bot update command: {e}")
            await self.send_reply(f"⚠️ Terjadi kesalahan saat memproses perintah: <code>{html.escape(str(e))}</code>", msg_id)

    # ─── Command Implementation ───────────────────────────────────────────────

    async def cmd_help(self, msg_id: int):
        help_text = (
            "🤖 <b>WTB RADAR CONTROL PANEL</b>\n"
            "───────────────────────────\n"
            "Gunakan **tombol navigasi di keyboard bawah** untuk kontrol instan!\n\n"
            "<b>💡 Fitur Tambah Bulk (Banyak Sekaligus):</b>\n"
            "Bisa memasukkan banyak keyword/channel sekaligus dipisah <b>koma</b> (cth: <code>canva, capcut, youtube</code>).\n\n"
            "<b>🧹 Fitur Reset/Clear:</b>\n"
            "• <code>/clearkw</code> — Reset & kosongkan semua keyword WTB\n"
            "• <code>/clearex</code> — Reset & kosongkan semua exclude filter"
        )
        await self.send_reply(help_text, msg_id)

    async def cmd_status(self, msg_id: int):
        channels = self.config.monitored_channels
        kws = self.config.keywords
        exs = self.config.excludes

        status_text = (
            "🟢 <b>WTB RADAR STATUS: RUNNING</b>\n"
            "───────────────────────────\n"
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
            await self.send_reply("📡 <b>Channel Monitor (0):</b>\n\nBelum ada channel. Klik <b>➕ Tambah Channel</b>.", msg_id)
            return

        lines = [f"<b>📡 Channels Dimonitor ({len(channels)}):</b>"]
        for i, ch in enumerate(channels, 1):
            lines.append(f"{i}. <code>{ch}</code>")

        await self.send_reply("\n".join(lines), msg_id)

    async def cmd_add_channel(self, args: str, msg_id: int):
        if not args:
            await self.send_reply("⚠️ Kirimkan username/link channel. Contoh: <code>@channelwtb1, @channelwtb2</code>", msg_id)
            return

        # Split input by comma or newline for bulk support
        raw_items = [ch.strip() for ch in re.split(r"[,\n]", args) if ch.strip()]
        if not raw_items:
            return

        await self.send_reply(f"⏳ Sedang memproses {len(raw_items)} channel...", msg_id)
        results = []
        for ch_raw in raw_items:
            success, msg, identifier = await resolve_and_join_channel(self.app, ch_raw)
            if success:
                added = self.config.add_channel(identifier)
                if added:
                    results.append(f"✅ <code>{identifier}</code> disimpan")
                else:
                    results.append(f"⚠️ <code>{identifier}</code> (sudah ada)")
            else:
                results.append(f"❌ <code>{ch_raw}</code> (gagal)")

        await self.send_reply("<b>📋 Hasil Tambah Channel:</b>\n\n" + "\n".join(results), msg_id)

    async def cmd_del_channel(self, args: str, msg_id: int):
        if not args:
            await self.send_reply("⚠️ Kirimkan username/ID channel. Contoh: <code>@channelwtb</code>", msg_id)
            return

        raw_items = [ch.strip() for ch in re.split(r"[,\n]", args) if ch.strip()]
        results = []
        for ch_raw in raw_items:
            removed = self.config.remove_channel(ch_raw)
            if removed:
                results.append(f"✅ <code>{ch_raw}</code> dihapus")
            else:
                results.append(f"❌ <code>{ch_raw}</code> tidak ditemukan")

        await self.send_reply("<b>📋 Hasil Hapus Channel:</b>\n\n" + "\n".join(results), msg_id)

    async def cmd_list_keywords(self, msg_id: int):
        kws = sorted(list(self.config.keywords))
        if not kws:
            await self.send_reply("🔑 <b>Keyword WTB Aktif (0):</b>\n\nBelum ada keyword. Klik <b>➕ Tambah Keyword</b>.", msg_id)
            return

        kw_fmt = "\n".join(f"• <code>{html.escape(k)}</code>" for k in kws)
        await self.send_reply(
            f"<b>🔑 Keyword WTB Aktif ({len(kws)}):</b>\n\n{kw_fmt}\n\n"
            "💡 <i>Ketik <code>/clearkw</code> untuk mengosongkan semua.</i>",
            msg_id
        )

    async def cmd_add_kw(self, args: str, msg_id: int):
        if not args:
            await self.send_reply("⚠️ Kirimkan kata kunci (bisa dipisah koma). Contoh: <code>canva, capcut, netflix</code>", msg_id)
            return

        added, skipped = self.config.add_keywords_bulk(args)
        msg = f"✅ <b>Berhasil menambahkan {added} keyword WTB baru!</b>"
        if skipped > 0:
            msg += f"\n<i>({skipped} keyword dilewati karena sudah ada atau kosong).</i>"

        await self.send_reply(msg, msg_id)

    async def cmd_del_kw(self, args: str, msg_id: int):
        if not args:
            await self.send_reply("⚠️ Kirimkan kata kunci yang ingin dihapus. Contoh: <code>canva, capcut</code>", msg_id)
            return

        removed, not_found = self.config.remove_keywords_bulk(args)
        msg = f"✅ <b>Berhasil menghapus {removed} keyword WTB!</b>"
        if not_found > 0:
            msg += f"\n<i>({not_found} keyword tidak ditemukan).</i>"

        await self.send_reply(msg, msg_id)

    async def cmd_list_excludes(self, msg_id: int):
        exs = sorted(list(self.config.excludes))
        if not exs:
            await self.send_reply("🚫 <b>Exclusion Keywords (0):</b>\n\nBelum ada filter abaikan. Klik <b>➕ Exclude</b>.", msg_id)
            return

        ex_fmt = "\n".join(f"• <code>{html.escape(e)}</code>" for e in exs)
        await self.send_reply(
            f"<b>🚫 Exclusion Keywords ({len(exs)}):</b>\n\n{ex_fmt}\n\n"
            "💡 <i>Ketik <code>/clearex</code> untuk mengosongkan semua filter abaikan.</i>",
            msg_id
        )

    async def cmd_add_ex(self, args: str, msg_id: int):
        if not args:
            await self.send_reply("⚠️ Kirimkan kata filter (bisa dipisah koma). Contoh: <code>wts, jual, ready</code>", msg_id)
            return

        added, skipped = self.config.add_excludes_bulk(args)
        msg = f"✅ <b>Berhasil menambahkan {added} exclude filter baru!</b>"
        if skipped > 0:
            msg += f"\n<i>({skipped} filter dilewati).</i>"

        await self.send_reply(msg, msg_id)

    async def cmd_del_ex(self, args: str, msg_id: int):
        if not args:
            await self.send_reply("⚠️ Kirimkan kata filter yang ingin dihapus. Contoh: <code>wts, jual</code>", msg_id)
            return

        removed, not_found = self.config.remove_excludes_bulk(args)
        msg = f"✅ <b>Berhasil menghapus {removed} exclude filter!</b>"
        if not_found > 0:
            msg += f"\n<i>({not_found} filter tidak ditemukan).</i>"

        await self.send_reply(msg, msg_id)

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
