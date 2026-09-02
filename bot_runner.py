"""
bot_runner.py — Management Bot Runner.
Handles Telegram Bot API long-polling and slash commands (/start_radar, /stop_radar, /addkw, etc.).

Clean UX Architecture:
  - No intrusive Reply Keyboard (Screen is 100% clean for WTB notifications).
  - Registers official Telegram Bot Command Menu (`setMyCommands`).
  - Supports both direct argument commands (e.g. `/addkw canva, capcut`) and interactive prompt states.
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

BOT_COMMANDS = [
    {"command": "start_radar",   "description": "▶️ Aktifkan monitoring radar"},
    {"command": "stop_radar",    "description": "⏹ Hentikan / Jeda monitoring"},
    {"command": "status",        "description": "📊 Cek status bot & radar"},
    {"command": "addkw",         "description": "➕ Tambah keyword WTB baru"},
    {"command": "delkw",         "description": "➖ Hapus keyword WTB"},
    {"command": "listkw",        "description": "🔑 Lihat semua keyword aktif"},
    {"command": "addch",         "description": "➕ Tambah channel monitor"},
    {"command": "delch",         "description": "➖ Hapus channel monitor"},
    {"command": "listch",        "description": "📡 Lihat semua channel monitor"},
    {"command": "addex",         "description": "🚫 Tambah exclude filter (abaikan kata)"},
    {"command": "delex",         "description": "➖ Hapus exclude filter"},
    {"command": "listex",        "description": "🚫 Lihat exclude filter aktif"},
    {"command": "setnotifbot",   "description": "🤖 Daftarkan bot khusus notifikasi WTB"},
    {"command": "clearnotifbot", "description": "🔄 Hapus bot notif — kembali ke bot utama"},
    {"command": "test",          "description": "🔔 Test koneksi notifikasi bot"},
    {"command": "help",          "description": "❓ Panduan lengkap penggunaan"},
    {"command": "clearkw",       "description": "🗑️ Kosongkan semua keyword"},
    {"command": "clearex",       "description": "🗑️ Kosongkan semua exclude filter"},
]


class BotRunner:
    def __init__(self, config: Config, pyrogram_client: Client, notifier=None):
        self.config         = config
        self.app            = pyrogram_client
        self.notifier       = notifier  # Reference to Notifier for hot-updating notif token
        self.bot_token      = config.bot_token
        self.target_chat_id = config.target_chat_id
        self.base_url       = f"https://api.telegram.org/bot{self.bot_token}"
        self._offset        = 0
        self.is_running     = False
        self.pending_state: Optional[str] = None
        self.radar_active: bool = True

    # ── Remove Keyboard Helper ────────────────────────────────────────────────

    def get_remove_keyboard(self) -> dict:
        """Removes any persistent reply keyboard so the chat view stays 100% clean."""
        return {"remove_keyboard": True}

    # ── HTTP Helpers ───────────────────────────────────────────────────────────

    async def register_bot_commands(self) -> bool:
        """Registers slash commands to Telegram so they appear in the native Menu popup."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    f"{self.base_url}/setMyCommands",
                    json={"commands": BOT_COMMANDS}
                )
                if res.status_code == 200 and res.json().get("ok"):
                    logger.info("Successfully registered Telegram Bot Commands Menu.")
                    return True
                else:
                    logger.warning(f"Failed to set bot commands: {res.text}")
                    return False
        except Exception as e:
            logger.error(f"Error registering bot commands: {e}")
            return False

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

    # ── Update Dispatcher ──────────────────────────────────────────────────────

    async def handle_update(self, update: dict):
        """Route incoming message updates to appropriate slash command handler."""
        try:
            message = update.get("message") or update.get("edited_message")
            if not message:
                return

            msg_id = message.get("message_id")
            chat   = message.get("chat", {})

            # Security: Only respond to owner chat
            if chat.get("id") != self.target_chat_id:
                return

            raw_text = (message.get("text") or "").strip()
            if not raw_text:
                return

            # Cancel ongoing input state
            if raw_text.lower() in ["batal", "/cancel", "cancel", "❌ batal"]:
                self.pending_state = None
                await self.send_reply("❌ <b>Operasi dibatalkan.</b>", msg_id)
                return

            # ── Check Interactive Pending State ──────────────────────────────
            if self.pending_state:
                state = self.pending_state
                self.pending_state = None
                if state == "awaiting_add_channel":
                    await self.cmd_add_channel(raw_text, msg_id)
                elif state == "awaiting_del_channel":
                    await self.cmd_del_channel(raw_text, msg_id)
                elif state == "awaiting_add_kw":
                    await self.cmd_add_kw(raw_text, msg_id)
                elif state == "awaiting_del_kw":
                    await self.cmd_del_kw(raw_text, msg_id)
                elif state == "awaiting_add_ex":
                    await self.cmd_add_ex(raw_text, msg_id)
                elif state == "awaiting_del_ex":
                    await self.cmd_del_ex(raw_text, msg_id)
                return

            # ── Parse Command & Arguments ────────────────────────────────────
            # Supports both "/addkw canva, capcut" and "/addkw" (with bot username suffix stripped)
            parts = raw_text.split(maxsplit=1)
            cmd_part = parts[0].lower().split("@")[0]  # e.g., "/start_radar@bot" -> "/start_radar"
            args = parts[1].strip() if len(parts) > 1 else ""

            # ── Route Commands ───────────────────────────────────────────────

            if cmd_part in ["/start", "/menu"]:
                await self.cmd_start_intro(msg_id)
                return

            if cmd_part == "/start_radar":
                await self.cmd_start_radar(msg_id)
                return

            if cmd_part == "/stop_radar":
                await self.cmd_stop_radar(msg_id)
                return

            if cmd_part in ["/status", "/info"]:
                await self.cmd_status(msg_id)
                return

            if cmd_part in ["/test", "/test_notif"]:
                await self.cmd_test_notif(msg_id)
                return

            if cmd_part in ["/help", "/panduan"]:
                await self.cmd_help(msg_id)
                return

            # ── Notification Bot Registration ─────────────────────────────────
            if cmd_part == "/setnotifbot":
                await self.cmd_set_notif_bot(args, msg_id)
                return

            if cmd_part == "/clearnotifbot":
                await self.cmd_clear_notif_bot(msg_id)
                return

            # ── Keywords ──────────────────────────────────────────────────────
            if cmd_part == "/listkw":
                await self.cmd_list_keywords(msg_id)
                return

            if cmd_part == "/addkw":
                if args:
                    await self.cmd_add_kw(args, msg_id)
                else:
                    self.pending_state = "awaiting_add_kw"
                    await self.send_reply(
                        "➕ <b>TAMBAH KEYWORD WTB</b>\n"
                        "───────────────────────────\n"
                        "Kirimkan kata kunci WTB baru (bisa banyak sekaligus dipisah koma):\n\n"
                        "• Contoh: <code>canva, capcut, youtube, yt famp</code>\n\n"
                        "<i>Ketik <code>batal</code> untuk membatalkan.</i>",
                        msg_id
                    )
                return

            if cmd_part == "/delkw":
                if args:
                    await self.cmd_del_kw(args, msg_id)
                else:
                    self.pending_state = "awaiting_del_kw"
                    await self.send_reply(
                        "➖ <b>HAPUS KEYWORD WTB</b>\n"
                        "───────────────────────────\n"
                        "Kirimkan keyword yang ingin dihapus:\n\n"
                        "• Contoh: <code>canva, capcut</code>\n\n"
                        "<i>Ketik <code>batal</code> untuk membatalkan.</i>",
                        msg_id
                    )
                return

            if cmd_part in ["/clearkw", "/resetkw"]:
                self.config.clear_keywords()
                await self.send_reply("🗑️ <b>Semua Keyword WTB berhasil dikosongkan.</b>", msg_id)
                return

            # ── Channels ──────────────────────────────────────────────────────
            if cmd_part == "/listch":
                await self.cmd_list_channels(msg_id)
                return

            if cmd_part == "/addch":
                if args:
                    await self.cmd_add_channel(args, msg_id)
                else:
                    self.pending_state = "awaiting_add_channel"
                    await self.send_reply(
                        "📥 <b>TAMBAH CHANNEL MONITOR</b>\n"
                        "───────────────────────────\n"
                        "Kirimkan username / link invite channel (bisa banyak sekaligus dipisah koma):\n\n"
                        "• Contoh: <code>@basewtb, @BASELELANG, @basewib</code>\n\n"
                        "<i>Ketik <code>batal</code> untuk membatalkan.</i>",
                        msg_id
                    )
                return

            if cmd_part == "/delch":
                if args:
                    await self.cmd_del_channel(args, msg_id)
                else:
                    self.pending_state = "awaiting_del_channel"
                    await self.send_reply(
                        "🗑️ <b>HAPUS CHANNEL MONITOR</b>\n"
                        "───────────────────────────\n"
                        "Kirimkan username atau ID channel yang ingin dihapus:\n\n"
                        "• Contoh: <code>@basewtb, @BASELELANG</code>\n\n"
                        "<i>Ketik <code>batal</code> untuk membatalkan.</i>",
                        msg_id
                    )
                return

            # ── Excludes ──────────────────────────────────────────────────────
            if cmd_part == "/listex":
                await self.cmd_list_excludes(msg_id)
                return

            if cmd_part == "/addex":
                if args:
                    await self.cmd_add_ex(args, msg_id)
                else:
                    self.pending_state = "awaiting_add_ex"
                    await self.send_reply(
                        "🚫 <b>TAMBAH EXCLUDE FILTER</b>\n"
                        "───────────────────────────\n"
                        "Kirimkan kata yang ingin diabaikan jika muncul di pesan:\n\n"
                        "• Contoh: <code>wts, jual, ready stock, price list</code>\n\n"
                        "<i>Ketik <code>batal</code> untuk membatalkan.</i>",
                        msg_id
                    )
                return

            if cmd_part == "/delex":
                if args:
                    await self.cmd_del_ex(args, msg_id)
                else:
                    self.pending_state = "awaiting_del_ex"
                    await self.send_reply(
                        "➖ <b>HAPUS EXCLUDE FILTER</b>\n"
                        "───────────────────────────\n"
                        "Kirimkan exclude filter yang ingin dihapus:\n\n"
                        "• Contoh: <code>wts, jual</code>\n\n"
                        "<i>Ketik <code>batal</code> untuk membatalkan.</i>",
                        msg_id
                    )
                return

            if cmd_part in ["/clearex", "/resetex"]:
                self.config.clear_excludes()
                await self.send_reply("🗑️ <b>Semua Exclude Filter berhasil dikosongkan.</b>", msg_id)
                return

            # ── Fallback ───────────────────────────────────────────────────────
            await self.send_reply(
                "❓ Perintah tidak dikenali.\n\n"
                "Ketik <code>/help</code> atau klik tombol <b>Menu [/]</b> di pojok kiri bawah untuk melihat daftar perintah.",
                msg_id
            )

        except Exception as e:
            logger.error(f"handle_update error: {e}", exc_info=True)
            try:
                await self.send_reply(f"⚠️ <b>Error internal:</b> <code>{html.escape(str(e))}</code>")
            except Exception:
                pass

    # ── Command Implementations ────────────────────────────────────────────────

    async def cmd_start_intro(self, msg_id: int):
        """Intro message on /start or /menu."""
        radar_status = "🟢 <b>AKTIF &amp; MONITORING</b>" if self.radar_active else "🔴 <b>PAUSE / BERHENTI</b>"
        intro_text = (
            "⚡ <b>WTB RADAR CONTROL PANEL</b>\n"
            "───────────────────────────\n"
            f"<b>Status Radar:</b> {radar_status}\n"
            f"📡 <b>Channel:</b> {len(self.config.monitored_channels)}\n"
            f"🔑 <b>Keyword:</b> {len(self.config.keywords)}\n"
            f"🚫 <b>Exclude:</b> {len(self.config.excludes)}\n\n"
            "<b>🚀 Perintah Cepat:</b>\n"
            "• <code>/start_radar</code> — ▶️ Aktifkan Radar\n"
            "• <code>/stop_radar</code> — ⏹ Hentikan Radar\n"
            "• <code>/addkw [kata]</code> — ➕ Tambah Keyword\n"
            "• <code>/addch [channel]</code> — ➕ Tambah Channel\n"
            "• <code>/status</code> — 📊 Info Detail\n"
            "• <code>/help</code> — ❓ Panduan Lengkap\n\n"
            "<i>💡 Klik tombol <b>Menu [/]</b> di pojok kiri bawah keyboard untuk melihat semua menu.</i>"
        )
        # Ensure any old persistent keyboard is cleaned up
        await self.send_reply(intro_text, msg_id, custom_keyboard=self.get_remove_keyboard())

    async def cmd_set_notif_bot(self, args: str, msg_id: int):
        """Register a separate bot token for WTB match notifications."""
        token = args.strip()

        if not token:
            await self.send_reply(
                "🤖 <b>DAFTARKAN BOT NOTIFIKASI WTB</b>\n"
                "───────────────────────────\n\n"
                "<b>Cara mendapatkan token:</b>\n"
                "1. Buka @BotFather di Telegram\n"
                "2. Ketik <code>/newbot</code>\n"
                "3. Ikuti langkah-langkah pembuatan bot\n"
                "4. Salin token yang diberikan (contoh: <code>1234567890:AAHxxx...</code>)\n\n"
                "<b>Lalu kirim perintah:</b>\n"
                "<code>/setnotifbot TOKEN_BOT_KAMU</code>\n\n"
                "<i>Setelah terdaftar, semua notifikasi WTB Match akan dikirim melalui bot baru tersebut — "
                "bukan melalui bot manajemen ini.</i>",
                msg_id
            )
            return

        # Basic token format validation: digits:alphanum (min length 35 chars)
        import re as _re
        if not _re.match(r"^\d{8,12}:[A-Za-z0-9_-]{35,}$", token):
            await self.send_reply(
                "❌ <b>Format token tidak valid!</b>\n\n"
                "Token bot harus berformat:\n"
                "<code>1234567890:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</code>\n\n"
                "Pastikan token disalin dengan benar dari @BotFather.",
                msg_id
            )
            return

        # Verify token is actually valid by calling getMe
        try:
            async with __import__("httpx").AsyncClient(timeout=10.0) as client:
                res = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                data = res.json()
                if not data.get("ok"):
                    await self.send_reply(
                        "❌ <b>Token tidak valid atau bot tidak ditemukan!</b>\n\n"
                        "Pastikan token yang dimasukkan benar dan bot masih aktif.",
                        msg_id
                    )
                    return
                bot_info = data["result"]
                bot_name     = html.escape(bot_info.get("first_name", ""))
                bot_username = html.escape(bot_info.get("username", ""))
        except Exception as e:
            await self.send_reply(
                f"⚠️ <b>Gagal memverifikasi token:</b> <code>{html.escape(str(e))}</code>",
                msg_id
            )
            return

        # Save to config & hot-update notifier
        self.config.set_notif_bot_token(token)
        if self.notifier:
            self.notifier.update_notif_token(token)

        await self.send_reply(
            "✅ <b>Bot Notifikasi WTB berhasil didaftarkan!</b>\n\n"
            f"🤖 <b>Nama Bot :</b> {bot_name}\n"
            f"📎 <b>Username :</b> @{bot_username}\n\n"
            "Mulai sekarang, semua notifikasi <b>WTB Match</b> akan dikirim melalui "
            f"@{bot_username} — bukan melalui bot manajemen ini.\n\n"
            f"💡 <i>Buka @{bot_username} dan klik <b>START</b> untuk mulai menerima notifikasi!</i>",
            msg_id
        )

    async def cmd_clear_notif_bot(self, msg_id: int):
        """Remove the separate notification bot and revert to single-bot mode."""
        self.config.clear_notif_bot_token()
        if self.notifier:
            self.notifier.update_notif_token(self.config.bot_token)
        await self.send_reply(
            "🔄 <b>Bot notifikasi terpisah dihapus.</b>\n\n"
            "Sistem kembali ke mode bot tunggal — notifikasi WTB akan dikirim "
            "melalui bot manajemen ini kembali.",
            msg_id
        )

    async def cmd_start_radar(self, msg_id: int):
        if self.radar_active:
            await self.send_reply("ℹ️ Radar sudah dalam keadaan <b>AKTIF</b>!", msg_id)
        else:
            self.radar_active = True
            await self.send_reply(
                "▶️ <b>RADAR DIAKTIFKAN!</b>\n\n"
                "🟢 WTB Radar sekarang <b>AKTIF &amp; MONITORING</b> secara real-time.\n"
                f"📡 Channel: <b>{len(self.config.monitored_channels)}</b> | "
                f"🔑 Keyword: <b>{len(self.config.keywords)}</b>",
                msg_id
            )

    async def cmd_stop_radar(self, msg_id: int):
        if not self.radar_active:
            await self.send_reply("ℹ️ Radar sudah dalam keadaan <b>BERHENTI (PAUSE)</b>!", msg_id)
        else:
            self.radar_active = False
            await self.send_reply(
                "⏹ <b>RADAR DIHENTIKAN (PAUSE)!</b>\n\n"
                "🔴 Monitoring sementara dinonaktifkan — notifikasi WTB tidak akan dikirim.\n"
                "Ketik <code>/start_radar</code> untuk mengaktifkan kembali.",
                msg_id
            )

    async def cmd_test_notif(self, msg_id: int):
        await self.send_reply(
            "🔔 <b>TEST NOTIFIKASI — BERHASIL!</b>\n\n"
            "✅ Sistem bot berjalan normal.\n\n"
            f"📡 Channels Dimonitor : <b>{len(self.config.monitored_channels)}</b>\n"
            f"🔑 Keywords WTB Aktif : <b>{len(self.config.keywords)}</b>\n"
            f"🚫 Exclude Filter     : <b>{len(self.config.excludes)}</b>\n"
            f"Status Radar          : <b>{'🟢 AKTIF' if self.radar_active else '🔴 PAUSE'}</b>",
            msg_id
        )

    async def cmd_status(self, msg_id: int):
        channels  = self.config.monitored_channels
        kws       = self.config.keywords
        exs       = self.config.excludes
        radar_str = "🟢 <b>AKTIF / RUNNING</b>" if self.radar_active else "🔴 <b>PAUSE / BERHENTI</b>"
        await self.send_reply(
            f"<b>📊 WTB RADAR STATUS</b>\n"
            f"───────────────────────────\n"
            f"<b>Radar:</b> {radar_str}\n"
            f"📡 <b>Monitored Channels:</b> {len(channels)}\n"
            f"🔑 <b>WTB Keywords:</b> {len(kws)}\n"
            f"🚫 <b>Exclusion Keywords:</b> {len(exs)}\n"
            f"⏱️ <b>Anti-Duplicate Cooldown:</b> {self.config.cooldown_seconds}s\n"
            f"⚡ <b>Engine:</b> Pyrogram + Active Polling (3s)",
            msg_id
        )

    async def cmd_help(self, msg_id: int):
        await self.send_reply(
            "<b>❓ PANDUAN PENGGUNAAN WTB RADAR</b>\n"
            "───────────────────────────\n\n"

            "<b>📡 Channel Monitor</b>\n"
            "Channel Telegram yang dicek bot setiap 3 detik.\n"
            "• <code>/addch @basewtb, @BASELELANG</code> — Tambah channel\n"
            "• <code>/delch @basewtb</code> — Hapus channel\n"
            "• <code>/listch</code> — Lihat daftar channel\n\n"

            "<b>🔑 Keyword WTB</b>\n"
            "Kata kunci target yang dicari dalam postingan channel.\n"
            "Pencocokan berupa <b>kata utuh</b> (bukan potongan kata):\n"
            "• <code>/addkw canva, capcut, yt, am</code> — Tambah keyword\n"
            "• <code>/delkw canva, yt</code> — Hapus keyword\n"
            "• <code>/listkw</code> — Lihat daftar keyword\n"
            "• <code>/clearkw</code> — Reset semua keyword\n\n"

            "<b>🚫 Exclude Filter</b>\n"
            "Kata yang jika muncul di pesan, otomatis <b>diabaikan</b> agar tidak ada spam seller.\n"
            "• <code>/addex wts, jual, ready</code> — Tambah filter\n"
            "• <code>/delex wts</code> — Hapus filter\n"
            "• <code>/listex</code> — Lihat daftar filter\n"
            "• <code>/clearex</code> — Reset semua filter\n\n"

            "<b>⚙️ Kontrol Radar</b>\n"
            "• <code>/start_radar</code> — ▶️ Mulai monitoring\n"
            "• <code>/stop_radar</code> — ⏹ Jeda monitoring\n"
            "• <code>/status</code> — 📊 Status bot saat ini\n"
            "• <code>/test</code> — 🔔 Tes koneksi notifikasi\n\n"

            "<i>💡 Tips: Anda dapat langsung mengetik perintah beserta isinya, contoh: <code>/addkw netflix, spotify, canva</code></i>",
            msg_id
        )

    async def cmd_list_channels(self, msg_id: int):
        channels = self.config.monitored_channels
        if not channels:
            await self.send_reply(
                "<b>📡 Channel Monitor (0)</b>\n\n"
                "Belum ada channel dimonitor.\n"
                "Ketik <code>/addch @username</code> untuk menambahkan.",
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
                "<b>🔑 Keyword WTB (0)</b>\n\n"
                "Belum ada keyword WTB aktif.\n"
                "Ketik <code>/addkw canva, capcut</code> untuk menambahkan.",
                msg_id
            )
            return
        kw_fmt = " · ".join(f"<code>{html.escape(k)}</code>" for k in kws)
        await self.send_reply(
            f"<b>🔑 Keyword WTB Aktif ({len(kws)}):</b>\n\n{kw_fmt}\n\n"
            "💡 <i>Ketik <code>/clearkw</code> untuk mengosongkan semua.</i>",
            msg_id
        )

    async def cmd_list_excludes(self, msg_id: int):
        exs = sorted(list(self.config.excludes))
        if not exs:
            await self.send_reply(
                "<b>🚫 Exclude Filter (0)</b>\n\n"
                "Belum ada filter abaikan.\n"
                "Ketik <code>/addex wts, jual</code> untuk menambahkan.",
                msg_id
            )
            return
        ex_fmt = " · ".join(f"<code>{html.escape(e)}</code>" for e in exs)
        await self.send_reply(
            f"<b>🚫 Exclude Filter Aktif ({len(exs)}):</b>\n\n{ex_fmt}\n\n"
            "💡 <i>Ketik <code>/clearex</code> untuk mengosongkan semua.</i>",
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
            # Exact match
            if self.config.remove_channel(ch_raw):
                results.append(f"• <code>{ch_raw}</code> — dihapus ✅")
                continue
            # Resolve via Pyrogram
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
        # Auto-register slash commands to Telegram native menu
        await self.register_bot_commands()

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
                            "allowed_updates":  ["message"],
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
