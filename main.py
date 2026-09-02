"""
main.py — Main Entry Point for WTB Radar / Notifier Bot.
ARCHITECTURE: Dual-mode detection:
  1. Active Polling (PRIMARY) — app.get_chat_history() every N seconds — 100% reliable
  2. on_message handler (BONUS) — real-time push if Pyrogram delivers it
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

from pyrogram import Client
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
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("WTBRadar.main")

# ─── Poll Interval ────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 1    # ⚡ Reduced from 3s → 1s for near-instant detection
POLL_HISTORY_LIMIT   = 10   # How many recent messages to fetch per channel per poll


async def warm_up_peer_cache(app: Client, config: Config):
    """Pre-resolve all monitored channels into Pyrogram's peer cache."""
    logger.info("Warming up peer cache & joining channels...")
    try:
        async for _ in app.get_dialogs(limit=100):
            pass
        logger.info("Dialogs loaded.")
    except Exception as e:
        logger.debug(f"Dialog warm-up: {e}")

    for target in config.monitored_channels:
        try:
            chat = await app.get_chat(target)
            logger.info(f"Peer resolved: {chat.title} ({target})")
            try:
                await app.join_chat(target)
            except Exception:
                pass  # Already joined — ok
        except Exception as e:
            logger.warning(f"Could not resolve {target}: {e}")


async def poll_single_channel(
    app: Client,
    target: str,
    last_seen_id: dict,
    processor: "MessageProcessor",
) -> None:
    """
    Poll a single channel for new messages since last_seen_id.
    Runs concurrently alongside other channels via asyncio.gather().
    """
    key = str(target)

    # First-time baseline for newly added channels — skip processing, just record
    if key not in last_seen_id:
        try:
            async for msg in app.get_chat_history(target, limit=1):
                last_seen_id[key] = msg.id
                logger.info(f"Baseline set [{target}]: msg_id={msg.id}")
        except Exception:
            last_seen_id[key] = 0
        return

    baseline = last_seen_id[key]

    try:
        new_msgs = []
        async for msg in app.get_chat_history(target, limit=POLL_HISTORY_LIMIT):
            if msg.id <= baseline:
                break
            new_msgs.append(msg)

        if new_msgs:
            # Update baseline to the highest (newest) message ID seen
            last_seen_id[key] = new_msgs[0].id
            logger.info(f"📥 {len(new_msgs)} new msg(s) in [{target}]")

            # Process chronologically — oldest first
            for msg in reversed(new_msgs):
                await processor.process_message(msg)

    except Exception as e:
        err_name = type(e).__name__
        if "FloodWait" in err_name:
            wait_seconds = getattr(e, "value", getattr(e, "x", 10))
            logger.warning(f"FloodWait on [{target}] — sleeping {wait_seconds}s")
            await asyncio.sleep(wait_seconds)
        else:
            logger.debug(f"Poll error [{target}]: {e}")


async def polling_loop(app: Client, config: Config, processor: "MessageProcessor",
                       bot_runner: "BotRunner"):
    """
    PRIMARY detection engine — PARALLEL edition.

    All monitored channels are polled SIMULTANEOUSLY every POLL_INTERVAL_SECONDS.
    This eliminates the sequential bottleneck (old: N×0.3s, new: ~0.3s regardless of N).
    """
    last_seen_id: dict = {}

    # ── Set baselines (sequential, one-time at startup) ───────────────────────
    logger.info("Polling: setting baselines for all channels...")
    for target in config.monitored_channels:
        key = str(target)
        try:
            async for msg in app.get_chat_history(target, limit=1):
                last_seen_id[key] = msg.id
                logger.info(f"Baseline [{target}]: msg_id={msg.id}")
        except Exception as e:
            last_seen_id[key] = 0
            logger.warning(f"Baseline failed for {target}: {e}")

    logger.info(f"⚡ Parallel polling started — interval={POLL_INTERVAL_SECONDS}s")

    while True:
        try:
            if not bot_runner.radar_active:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            monitored = list(config.monitored_channels)
            if monitored:
                # ── Fire all channel polls SIMULTANEOUSLY ─────────────────────
                await asyncio.gather(
                    *[
                        poll_single_channel(app, target, last_seen_id, processor)
                        for target in monitored
                    ],
                    return_exceptions=True  # Never let one failed channel kill the whole loop
                )

        except Exception as e:
            logger.error(f"Polling loop error: {e}")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ─── Application Bootstrap ────────────────────────────────────────────────────

async def main():
    logger.info("Initializing WTB Radar Bot...")

    config    = Config()
    cooldown  = CooldownManager(ttl_seconds=config.cooldown_seconds)
    notifier  = Notifier(
        bot_token       = config.bot_token,
        target_chat_id  = config.target_chat_id,
        notif_bot_token = config.notif_bot_token,  # separate notif bot (fallback to bot_token if not set)
    )
    processor = MessageProcessor(config=config, cooldown=cooldown, notifier=notifier)

    app = Client(
        name="wtb_radar_session",
        api_id=config.api_id,
        api_hash=config.api_hash,
        workdir="."
    )

    bot_runner = BotRunner(config=config, pyrogram_client=app, notifier=notifier)

    # ── BONUS: real-time push handler (fires when Pyrogram delivers it) ───────
    @app.on_message()
    async def push_handler(client: Client, message: Message):
        try:
            if not bot_runner.radar_active or not message.chat:
                return
            if "channel" not in str(message.chat.type).lower():
                return

            chat_id  = message.chat.id
            username = message.chat.username.lower() if message.chat.username else None

            for target in config.monitored_channels:
                t = str(target).strip()
                if t == str(chat_id) or (username and t.lstrip("@").lower() == username):
                    logger.info(f"⚡ Push-update from: {message.chat.title}")
                    await processor.process_message(message)
                    break
        except Exception as e:
            logger.debug(f"push_handler error: {e}")

    logger.info("Starting Pyrogram client...")
    await app.start()
    me = await app.get_me()
    logger.info(f"Logged in as: {me.first_name} (@{me.username or me.id})")

    await warm_up_peer_cache(app, config)

    # Start all background tasks
    bot_task  = asyncio.create_task(bot_runner.start_polling())
    poll_task = asyncio.create_task(
        polling_loop(app, config, processor, bot_runner)
    )

    startup_msg = (
        "🚀 <b>WTB RADAR AKTIF &amp; MONITORING!</b>\n\n"
        f"👤 <b>Akun:</b> {me.first_name} (@{me.username or me.id})\n"
        f"📡 <b>Channels:</b> {len(config.monitored_channels)}\n"
        f"🔑 <b>Keywords:</b> {len(config.keywords)}\n"
        f"⏱️ <b>Interval:</b> {POLL_INTERVAL_SECONDS} detik (Realtime)\n\n"
        "💡 <i>Gunakan tombol <b>Menu [/]</b> di pojok kiri bawah atau ketik <code>/help</code> untuk melihat daftar perintah.</i>"
    )
    await notifier.send_system_message(startup_msg, reply_markup=bot_runner.get_remove_keyboard())
    logger.info("WTB Radar active — clean slash command mode.")

    stop_event = asyncio.Event()

    def _sig():
        logger.info("Shutdown signal.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sig)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

    logger.info("Shutting down...")
    bot_runner.is_running = False
    bot_task.cancel()
    poll_task.cancel()
    await app.stop()
    logger.info("WTB Radar stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot exited.")
