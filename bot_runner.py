"""
bot_runner.py — Management Bot Runner.
Handles Telegram Bot API long-polling, inline keyboard callbacks, and
all management commands (add/remove channel, keyword, exclude).

Keyboard architecture:
  - Persistent Reply Keyboard: 1 row only (☰ Menu | Start/Stop | Test)
  - Management actions: Inline Keyboard inside a menu message
"""

import asyncio
import html
import logging
import re
from typing import Optional

import httpx
from pyrogram import Client

from channel_manager import resolve_and_join_channel
from config import Config

logger = logging.getLogger("WTBRadar.bot_runner")


class BotRunner:
    def __init__(self, config: Config, pyrogram_client: Client):
        self.config         = config
        self.app            = pyrogram_client
        self.bot_token      = config.bot_token
        self.target_chat_id = config.target_chat_id
        self.base_url       = f"https://api.telegram.org/bot{self.bot_token}"
        self._offset        = 0
        self.is_running     = False
        self.pending_state: Optional[str] = None
        # Controls whether radar actively monitors channels
        self.radar_active: bool = True

    # ── Keyboards ──────────────────────────────────────────────────────────────

    def get_main_keyboard(self) -> dict:
        """Compact 1-row persistent Reply Keyboard."""
        stop_btn = "⏹ Stop Radar" if self.radar_active else "▶️ Start Radar"
        return {
            "keyboard": [[
                {"text": "☰ Buka Menu"},
                {"text": stop_btn},
                {"text": "🔔 Test Notif"},
            ]],
            "resize_keyboard": True,
            "is_persistent": True,
        }

    def get_menu_inline_keyboard(self) -> dict:
        """Full management Inline Keyboard, sent inside a message."""
        return {
            "inline_keyboard": [
                [
                    {"text": "📡 List Channel",    "callback_data": "list_ch"},
                    {"text": "🔑 List Keyword",    "callback_data": "list_kw"},
                ],
                [
                    {"text": "➕ Tambah Channel",  "callback_data": "add_ch"},
                    {"text": "➕ Tambah Keyword",  "callback_data": "add_kw"},
                ],
                [
                    {"text": "➖ Hapus Channel",   "callback_data": "del_ch"},
                    {"text": "➖ Hapus Keyword",   "callback_data": "del_kw"},
                ],
                [
                    {"text": "🚫 List Exclude",    "callback_data": "list_ex"},
                    {"text": "➕ Exclude",          "callback_data": "add_ex"},
                    {"text": "➖ Exclude",          "callback_data": "del_ex"},
                ],
                [
                    {"text": "📊 Status Bot",      "callback_data": "status"},
                    {"text": "❓ Help & Petunjuk", "callback_data": "help"},
                ],
            ]
        }

    # ── HTTP Helpers ───────────────────────────────────────────────────────────

    async def send_reply(self, text: str, reply_to_msg_id: int = None,
                         custom_keyboard=None) -> bool:
        """Send a message to target_chat_id."""
        payload = {
            "chat_id":                  self.target_chat_id,
            "text":                     text,
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        }
        if reply_to_msg_id:
            payload["reply_to_message_id"] = reply_to_msg_id
        if custom_keyboard:
            payload["reply_markup"] = custom_keyboard

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(f"{self.base_url}/sendMessage", json=payload)
                return res.status_code == 200 and res.json().get("ok")
        except Exception as e:
            logger.error(f"send_reply error: {e}")
            return False

    async def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        """Answer a callback query to remove the loading spinner on the button."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{self.base_url}/answerCallbackQuery",
                    json={"callback_query_id": callback_query_id, "text": text}
                )
        except Exception:
            pass

    # ── Update Dispatcher ──────────────────────────────────────────────────────

    async def handle_update(self, update: dict):
        """Route incoming update to the correct handler."""
        try:
            # ── Inline Button Callback ─────────────────────────────────────────
            if "callback_query" in update:
                await self.handle_callback_query(update["callback_query"])
                return

            # ── Text Message ───────────────────────────────────────────────────
            message = update.get("message") or update.get("edited_message")
            if not message:
                return

            msg_id = message.get("message_id")
            chat   = message.get("chat", {})

            # Only respond to the owner's chat
            if chat.get("id") != self.target_chat_id:
                return

            text = (message.get("text") or "").strip()
            if not text:
                return

            # ── Cancel ────────────────────────────────────────────────────────
            if text.lower() in ["batal", "/cancel", "❌ batal"]:
                self.pending_state = None
                await self.send_reply("❌ <b>Operasi dibatalkan.</b>", msg_id)
                return

            # ── Pending State: waiting for user text input ─────────────────────
            if self.pending_state:
                state = self.pending_state
                self.pending_state = None
                if state == "awaiting_add_channel":
                    await self.cmd_add_channel(text, msg_id)
                elif state == "awaiting_del_channel":
                    await self.cmd_del_channel(text, msg_id)
                elif state == "awaiting_add_kw":
                    await self.cmd_add_kw(text, msg_id)
                elif state == "awaiting_del_kw":
                    await self.cmd_del_kw(text, msg_id)
                elif state == "awaiting_add_ex":
                    await self.cmd_add_ex(text, msg_id)
                elif state == "awaiting_del_ex":
                    await self.cmd_del_ex(text, msg_id)
                return

            # ── Utility text commands ──────────────────────────────────────────
            if text.lower() in ["/clearkw", "/resetkw"]:
                self.config.clear_keywords()
                await self.send_reply("🗑️ <b>Semua Keyword WTB dikosongkan.</b>", msg_id)
                return

            if text.lower() in ["/clearex", "/resetex"]:
                self.config.clear_excludes()
                await self.send_reply("🗑️ <b>Semua Exclude Filter dikosongkan.</b>", msg_id)
                return

            # ── Persistent Keyboard Buttons ────────────────────────────────────

            if text == "☰ Buka Menu":
                await self.cmd_open_menu(msg_id)
                return

            if text in ["▶️ Start Radar", "/start_radar"]:
                await self.cmd_start_radar(msg_id)
                return

            if text in ["⏹ Stop Radar", "/stop_radar"]:
                await self.cmd_stop_radar(msg_id)
                return

            if text in ["🔔 Test Notif", "/test"]:
                await self.cmd_test_notif(msg_id)
                return

            # ── Fallback ───────────────────────────────────────────────────────
            await self.send_reply(
                "❓ Perintah tidak dikenali.\n"
                "Klik <b>☰ Buka Menu</b> untuk melihat semua fitur yang tersedia.",
                msg_id
            )

        except Exception as e:
            logger.error(f"handle_update error: {e}", exc_info=True)
            try:
                await self.send_reply(f"⚠️ <b>Error internal:</b> <code>{html.escape(str(e))}</code>")
            except Exception:
                pass

    async def handle_callback_query(self, cq: dict):
        """Handle inline keyboard button presses (callback queries)."""
        cq_id  = cq["id"]
        data   = cq.get("data", "")
        msg_id = cq.get("message", {}).get("message_id")

        # Dismiss loading spinner immediately
        await self.answer_callback(cq_id)

        # Direct action handlers
        direct = {
            "list_ch": self.cmd_list_channels,
            "list_kw": self.cmd_list_keywords,
            "list_ex": self.cmd_list_excludes,
            "status":  self.cmd_status,
            "help":    self.cmd_help,
        }
        if data in direct:
            await direct[data](msg_id)
            return

        # Actions that require follow-up text input
        prompts = {
            "add_ch": (
                "awaiting_add_channel",
                "📥 <b>TAMBAH CHANNEL MONITOR</b>\n"
                "───────────────────────────\n"
                "Kirimkan username / link channel.\n"
                "Bisa banyak sekaligus dipisah koma:\n\n"
                "• Contoh: <code>@channelwtb1, @channelwtb2</code>\n\n"
                "Ketik <code>batal</code> untuk membatalkan."
            ),
            "del_ch": (
                "awaiting_del_channel",
                "🗑️ <b>HAPUS CHANNEL MONITOR</b>\n"
                "───────────────────────────\n"
                "Kirimkan username atau ID channel yang ingin dihapus.\n"
                "Bisa banyak sekaligus dipisah koma:\n\n"
                "• Contoh: <code>@channelwtb1, @channelwtb2</code>\n\n"
                "Ketik <code>batal</code> untuk membatalkan."
            ),
            "add_kw": (
                "awaiting_add_kw",
                "➕ <b>TAMBAH KEYWORD WTB</b>\n"
                "───────────────────────────\n"
                "Kirimkan kata kunci baru.\n"
                "Bisa banyak sekaligus dipisah koma:\n\n"
                "• Contoh: <code>canva, capcut, youtube</code>\n\n"
                "Ketik <code>batal</code> untuk membatalkan."
            ),
            "del_kw": (
                "awaiting_del_kw",
                "➖ <b>HAPUS KEYWORD WTB</b>\n"
                "───────────────────────────\n"
                "Kirimkan keyword yang ingin dihapus:\n\n"
                "• Contoh: <code>canva, capcut</code>\n\n"
                "Ketik <code>batal</code> untuk membatalkan."
            ),
            "add_ex": (
                "awaiting_add_ex",
                "🚫 <b>TAMBAH EXCLUDE FILTER</b>\n"
                "───────────────────────────\n"
                "Kirimkan kata yang jika ditemukan dalam pesan,\n"
                "pesan tersebut akan diabaikan meski ada keyword cocok.\n\n"
                "• Contoh: <code>wts, jual, ready stock</code>\n\n"
                "Ketik <code>batal</code> untuk membatalkan."
            ),
            "del_ex": (
                "awaiting_del_ex",
                "➖ <b>HAPUS EXCLUDE FILTER</b>\n"
                "───────────────────────────\n"
                "Kirimkan filter yang ingin dihapus:\n\n"
                "• Contoh: <code>wts, jual</code>\n\n"
                "Ketik <code>batal</code> untuk membatalkan."
            ),
        }
        if data in prompts:
            state, prompt_text = prompts[data]
            self.pending_state = state
            await self.send_reply(prompt_text, msg_id)

    # ── Persistent Keyboard Handlers ───────────────────────────────────────────

    async def cmd_open_menu(self, msg_id: int):
        """Send full inline management menu."""
        radar_status = "🟢 Radar: AKTIF" if self.radar_active else "🔴 Radar: PAUSE"
        await self.send_reply(
            f"<b>⚙️ WTB RADAR — MENU UTAMA</b>\n"
            f"───────────────────────────\n"
            f"{radar_status}  ·  "
            f"📡 {len(self.config.monitored_channels)} Channel  ·  "
            f"🔑 {len(self.config.keywords)} Keyword\n\n"
            "Pilih aksi di bawah:",
            msg_id,
            custom_keyboard=self.get_menu_inline_keyboard()
        )

    async def cmd_start_radar(self, msg_id: int):
        if self.radar_active:
            await self.send_reply("ℹ️ Radar sudah dalam keadaan <b>AKTIF</b>.", msg_id)
        else:
            self.radar_active = True
            await self.send_reply(
                "▶️ <b>Radar dinyalakan!</b>\n\n"
                "🟢 WTB Radar sekarang <b>AKTIF &amp; MONITORING</b>.",
                msg_id,
                custom_keyboard=self.get_main_keyboard()
            )

    async def cmd_stop_radar(self, msg_id: int):
        if not self.radar_active:
            await self.send_reply("ℹ️ Radar sudah dalam keadaan <b>PAUSE</b>.", msg_id)
        else:
            self.radar_active = False
            await self.send_reply(
                "⏹ <b>Radar dihentikan!</b>\n\n"
                "🔴 Monitoring dimatikan — notif WTB tidak akan dikirim.\n"
                "Klik <b>▶️ Start Radar</b> untuk mengaktifkan kembali.",
                msg_id,
                custom_keyboard=self.get_main_keyboard()
            )

    async def cmd_test_notif(self, msg_id: int):
        await self.send_reply(
            "🔔 <b>TEST NOTIFIKASI — OK!</b>\n\n"
            "✅ Sistem notifikasi <b>berfungsi normal</b>.\n\n"
            f"📡 Channels dimonitor : <b>{len(self.config.monitored_channels)}</b>\n"
            f"🔑 Keywords aktif     : <b>{len(self.config.keywords)}</b>\n"
            f"🚫 Exclude filter     : <b>{len(self.config.excludes)}</b>\n"
            f"Radar                 : <b>{'AKTIF ✅' if self.radar_active else 'PAUSE ❌'}</b>",
            msg_id
        )

    # ── Inline Menu Handlers ───────────────────────────────────────────────────

    async def cmd_status(self, msg_id: int):
        channels  = self.config.monitored_channels
        kws       = self.config.keywords
        exs       = self.config.excludes
        radar_str = "🟢 <b>AKTIF / RUNNING</b>" if self.radar_active else "🔴 <b>PAUSE / BERHENTI</b>"
        await self.send_reply(
            f"<b>📊 WTB RADAR STATUS</b>\n"
            f"───────────────────────────\n"
            f"Radar    : {radar_str}\n"
            f"Channels : <b>{len(channels)}</b>\n"
            f"Keywords : <b>{len(kws)}</b>\n"
            f"Excludes : <b>{len(exs)}</b>\n"
            f"Cooldown : <b>{self.config.cooldown_seconds}s</b>\n"
            f"Engine   : <b>Pyrogram + Active Polling (3s)</b>",
            msg_id
        )

    async def cmd_help(self, msg_id: int):
        await self.send_reply(
            "<b>❓ PANDUAN WTB RADAR</b>\n"
            "───────────────────────────\n\n"

            "<b>📡 Channel Monitor</b>\n"
            "Daftar channel Telegram yang dipantau bot secara aktif. "
            "Bot mengecek pesan baru setiap beberapa detik. "
            "Tambahkan channel dengan <code>@username</code> atau link invite.\n\n"

            "<b>🔑 WTB Keywords</b>\n"
            "Kata kunci yang dicari di setiap pesan channel. "
            "Bot hanya mencocokkan <b>kata utuh</b> — bukan huruf yang nyempil di tengah kata lain.\n"
            "• <code>am</code> cocok dengan \"cari <b>am</b> dong\" ✅\n"
            "• <code>am</code> <b>tidak</b> cocok dengan \"desk<b>cam</b>\" ❌\n\n"

            "<b>🚫 Exclude Filter</b>\n"
            "Jika kata ini ditemukan dalam pesan, pesan tersebut <b>diabaikan</b> "
            "meski ada keyword yang cocok. Berguna untuk filter \"jual\", \"wts\", \"ready\" "
            "agar seller mode tidak masuk notif.\n\n"

            "<b>➕ / ➖ Tambah &amp; Hapus</b>\n"
            "Bisa input banyak sekaligus dipisah koma:\n"
            "• <code>canva, capcut, yt</code>\n\n"

            "<b>▶️ Start / ⏹ Stop Radar</b>\n"
            "Aktifkan atau jeda monitoring. Bot tetap bisa digunakan, "
            "hanya notif WTB yang dihentikan.\n\n"

            "<b>🔔 Test Notif</b>\n"
            "Verifikasi bot bisa kirim pesan. Jika Test OK tapi notif WTB tidak muncul, "
            "periksa apakah channel dan keyword sudah ditambahkan dengan benar.\n\n"

            "<b>⌨️ Command Teks</b>\n"
            "• <code>/clearkw</code> — Hapus <b>semua</b> keyword\n"
            "• <code>/clearex</code> — Hapus <b>semua</b> exclude filter\n"
            "• <code>batal</code> — Batalkan operasi yang sedang berjalan",
            msg_id
        )

    async def cmd_list_channels(self, msg_id: int):
        channels = self.config.monitored_channels
        if not channels:
            await self.send_reply(
                "<b>📡 Channel Monitor (0)</b>\n\n"
                "Belum ada channel. Klik <b>➕ Tambah Channel</b> di menu.",
                msg_id
            )
            return
        lines = [f"<b>📡 Channels Dimonitor ({len(channels)}):</b>"]
        for i, ch in enumerate(channels, 1):
            lines.append(f"{i}. <code>{ch}</code>")
        await self.send_reply("\n".join(lines), msg_id)

    async def cmd_list_keywords(self, msg_id: int):
        kws = sorted(list(self.config.keywords))
        if not kws:
            await self.send_reply(
                "<b>🔑 Keyword WTB (0)</b>\n\nBelum ada keyword. Klik <b>➕ Tambah Keyword</b>.",
                msg_id
            )
            return
        kw_fmt = " · ".join(f"<code>{html.escape(k)}</code>" for k in kws)
        await self.send_reply(
            f"<b>🔑 Keyword WTB Aktif ({len(kws)}):</b>\n\n{kw_fmt}\n\n"
            "Ketik <code>/clearkw</code> untuk hapus semua.",
            msg_id
        )

    async def cmd_list_excludes(self, msg_id: int):
        exs = sorted(list(self.config.excludes))
        if not exs:
            await self.send_reply(
                "<b>🚫 Exclude Filter (0)</b>\n\nBelum ada filter. Klik <b>➕ Exclude</b>.",
                msg_id
            )
            return
        ex_fmt = " · ".join(f"<code>{html.escape(e)}</code>" for e in exs)
        await self.send_reply(
            f"<b>🚫 Exclude Filter ({len(exs)}):</b>\n\n{ex_fmt}\n\n"
            "Ketik <code>/clearex</code> untuk hapus semua.",
            msg_id
        )

    async def cmd_add_channel(self, args: str, msg_id: int):
        raw_items = [ch.strip() for ch in re.split(r"[,\n]", args) if ch.strip()]
        if not raw_items:
            return
        await self.send_reply(f"⏳ Memproses {len(raw_items)} channel...", msg_id)
        results = []
        for ch_raw in raw_items:
            success, msg, identifier = await resolve_and_join_channel(self.app, ch_raw)
            if success:
                added = self.config.add_channel(identifier)
                status = "disimpan ✅" if added else "sudah ada ⚠️"
                results.append(f"• <code>{identifier}</code> — {status}")
            else:
                results.append(f"• <code>{ch_raw}</code> — gagal ❌")
        await self.send_reply(
            "<b>📋 Hasil Tambah Channel:</b>\n\n" + "\n".join(results), msg_id
        )

    async def cmd_del_channel(self, args: str, msg_id: int):
        raw_items = [ch.strip() for ch in re.split(r"[,\n]", args) if ch.strip()]
        results   = []
        for ch_raw in raw_items:
            # Try exact match first
            if self.config.remove_channel(ch_raw):
                results.append(f"• <code>{ch_raw}</code> — dihapus ✅")
                continue
            # Resolve via Pyrogram (e.g. @basewtb → -1001525948158)
            try:
                chat    = await self.app.get_chat(ch_raw)
                removed = self.config.remove_channel(str(chat.id))
                if not removed and chat.username:
                    removed = self.config.remove_channel(f"@{chat.username}")
                if removed:
                    results.append(f"• <code>{ch_raw}</code> ({chat.id}) — dihapus ✅")
                else:
                    results.append(f"• <code>{ch_raw}</code> — tidak ditemukan ❌")
            except Exception:
                results.append(f"• <code>{ch_raw}</code> — tidak ditemukan ❌")
        await self.send_reply(
            "<b>📋 Hasil Hapus Channel:</b>\n\n" + "\n".join(results), msg_id
        )

    async def cmd_add_kw(self, args: str, msg_id: int):
        added, skipped = self.config.add_keywords_bulk(args)
        msg = f"✅ <b>Berhasil menambahkan {added} keyword WTB baru!</b>"
        if skipped:
            msg += f"\n<i>({skipped} keyword dilewati — sudah ada atau kosong.)</i>"
        await self.send_reply(msg, msg_id)

    async def cmd_del_kw(self, args: str, msg_id: int):
        removed, not_found = self.config.remove_keywords_bulk(args)
        msg = f"✅ <b>Berhasil menghapus {removed} keyword WTB!</b>"
        if not_found:
            msg += f"\n<i>({not_found} keyword tidak ditemukan.)</i>"
        await self.send_reply(msg, msg_id)

    async def cmd_add_ex(self, args: str, msg_id: int):
        added, skipped = self.config.add_excludes_bulk(args)
        msg = f"✅ <b>Berhasil menambahkan {added} exclude filter baru!</b>"
        if skipped:
            msg += f"\n<i>({skipped} filter dilewati.)</i>"
        await self.send_reply(msg, msg_id)

    async def cmd_del_ex(self, args: str, msg_id: int):
        removed, not_found = self.config.remove_excludes_bulk(args)
        msg = f"✅ <b>Berhasil menghapus {removed} exclude filter!</b>"
        if not_found:
            msg += f"\n<i>({not_found} filter tidak ditemukan.)</i>"
        await self.send_reply(msg, msg_id)

    # ── Long Polling Loop ──────────────────────────────────────────────────────

    async def start_polling(self):
        """Runs async long-polling for Telegram Bot API updates."""
        self.is_running = True
        logger.info("Management Bot polling started...")
        async with httpx.AsyncClient(timeout=30.0) as client:
            while self.is_running:
                try:
                    res = await client.get(
                        f"{self.base_url}/getUpdates",
                        params={
                            "offset":           self._offset,
                            "timeout":          10,
                            "allowed_updates":  ["message", "callback_query"],
                        }
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
                    logger.debug(f"Bot polling error (retrying): {e}")
                    await asyncio.sleep(3)
