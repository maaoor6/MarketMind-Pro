"""MarketMind-Pro — Application entry point."""

import asyncio
import signal
import sys

from src.agents.notifier import Notifier
from src.agents.quant_engine import QuantEngine
from src.agents.telegram_dispatcher import TelegramDispatcher
from src.database.cache import cache
from src.trading.orchestrator import Orchestrator
from src.utils.logger import get_logger
from src.utils.watchdog import Watchdog

logger = get_logger(__name__)


async def run() -> None:
    """Bootstrap and run all agents concurrently."""
    logger.info("marketmind_pro_starting")

    # Connect cache
    await cache.connect()

    # Boot readiness self-check (logs a redacted report; never hard-fails).
    from src.utils.preflight import run_preflight

    await run_preflight()

    quant = QuantEngine()
    telegram = TelegramDispatcher()
    trading = Orchestrator(quant)
    watchdog = Watchdog(notifier=Notifier())

    # Graceful shutdown handler
    shutdown_event = asyncio.Event()

    def handle_signal(sig: int) -> None:
        logger.info("shutdown_signal_received", signal=sig)
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))

    try:
        await telegram.start()

        tasks = [
            asyncio.create_task(quant.run_loop(), name="quant-engine"),
            asyncio.create_task(trading.run_loop(), name="orchestrator"),
            asyncio.create_task(watchdog.run(), name="watchdog"),
            asyncio.create_task(shutdown_event.wait(), name="shutdown-watcher"),
        ]

        logger.info("all_agents_running")
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        for task in pending:
            task.cancel()

    finally:
        quant.stop()
        trading.stop()
        watchdog.stop()
        await trading.close()
        await telegram.stop()
        await cache.disconnect()
        logger.info("marketmind_pro_shutdown_complete")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
        sys.exit(0)


if __name__ == "__main__":
    main()
