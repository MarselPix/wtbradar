"""
main.py — Main Entry Point for WTB Radar / Notifier Bot.
Runs Pyrogram client for real-time MTProto channel listening alongside
the Async Bot Management Runner. Optimized for Termux & low RAM footprint.
"""

import asyncio
import logging
import signal
import sys

# Ensure event loop exists before importing Pyrogram (fix for Python 3.12+ / 3.14)
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client, filters
from pyrogram.types import Message

from config import Config
from cooldown import CooldownManager
from notifier import Notifier
from handler import MessageProcessor
from bot_runner import BotRunner

# ─── Logging Setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
# Suppress httpx HTTP log output to prevent raw Bot Token exposure in console logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("WTBRadar.main")

# ─── Dynamic Channel Filter ───────────────────────────────────────────────────

def is_monitored_channel(config: Config):
    """Custom Pyrogram filter that dynamically checks if incoming message belongs to monitored channels."""
    async def func(_, __, message: Message):
        if not message.chat:
            return False

        monitored = config.monitored_channels
        if not monitored:
            return False

        chat = message.chat
        chat_id = chat.id
        chat_username = f"@{chat.username}" if chat.username else None

        for target in monitored:
            target_str = str(target).strip()
            # Match numeric ID
            if target_str == str(chat_id):
                return True
            # Match username (with or without @)
            if chat_username and target_str.lstrip("@").lower() == chat_username.lstrip("@").lower():
                return True

        return False

    return filters.create(func, "DynamicMonitoredChannelFilter")


async def warm_up_peer_cache(app: Client, config: Config):
    """
    Loads initial user dialogs and pre-resolves all monitored channels
    so Pyrogram populates its local session peer DB.
    Prevents 'ValueError: Peer id invalid'.
    """
    logger.info("Warming up channel peer cache...")
    try:
        async for _ in app.get_dialogs(limit=30):
            pass
    except Exception as e:
        logger.debug(f"Dialog warm-up: {e}")

    monitored = config.monitored_channels
    for target in monitored:
        try:
            chat = await app.get_chat(target)
            logger.info(f"Resolved peer cache for: {chat.title} ({target})")
        except Exception as e:
            logger.warning(f"Could not pre-resolve channel {target}: {e}")


# ─── Application Bootstrap ────────────────────────────────────────────────────

async def main():
    logger.info("Initializing WTB Radar Bot...")

    # Load configuration
    config = Config()
    cooldown = CooldownManager(ttl_seconds=config.cooldown_seconds)
    notifier = Notifier(bot_token=config.bot_token, target_chat_id=config.target_chat_id)
    processor = MessageProcessor(config=config, cooldown=cooldown, notifier=notifier)

    # Initialize Pyrogram User Client
    app = Client(
        name="wtb_radar_session",
        api_id=config.api_id,
        api_hash=config.api_hash,
        workdir="."
    )

    # Register message handler for channel updates
    @app.on_message(is_monitored_channel(config))
    async def channel_message_handler(client: Client, message: Message):
        await processor.process_message(message)

    # Initialize Management Bot Runner
    bot_runner = BotRunner(config=config, pyrogram_client=app)

    logger.info("Starting Pyrogram client...")
    await app.start()
    me = await app.get_me()
    logger.info(f"Pyrogram logged in as User: {me.first_name} (@{me.username or me.id})")

    # Warm up peer cache to fix ValueError: Peer id invalid
    await warm_up_peer_cache(app, config)

    # Start Bot Polling Task
    bot_task = asyncio.create_task(bot_runner.start_polling())

    # Send Startup Notification with Navigation Keyboard
    startup_msg = (
        "🚀 <b>WTB RADAR AKTIF & MONITORING!</b>\n\n"
        f"👤 <b>User Account:</b> {me.first_name} (@{me.username or me.id})\n"
        f"📡 <b>Monitored Channels:</b> {len(config.monitored_channels)}\n"
        f"🔑 <b>Active Keywords:</b> {len(config.keywords)}\n\n"
        "Gunakan <b>tombol navigasi di bawah</b> untuk mengontrol bot secara langsung."
    )
    await notifier.send_system_message(startup_msg, reply_markup=bot_runner.get_main_keyboard())

    logger.info("WTB Radar is now active and listening to messages.")

    # Graceful shutdown handler
    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass  # Windows implementation fallback

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

    logger.info("Shutting down WTB Radar...")
    bot_runner.is_running = False
    bot_task.cancel()
    await app.stop()
    logger.info("WTB Radar stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot exited.")
