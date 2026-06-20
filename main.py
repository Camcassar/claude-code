"""Bot 8 entry point. Run: python main.py [config/live.yaml]"""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

load_dotenv(PROJECT_ROOT / ".env")

from bot.exchange import BybitConnector       # noqa: E402
from bot.runner import Bot8Runner             # noqa: E402
from bot.strategy import AvaxSpectralStrategy # noqa: E402
from bot import telegram                      # noqa: E402

(PROJECT_ROOT / "logs").mkdir(parents=True, exist_ok=True)
_log_handlers: list[logging.Handler] = [logging.StreamHandler()]
_log_file = PROJECT_ROOT / "logs" / "bot8.log"
try:
    _log_handlers.append(logging.FileHandler(_log_file))
except OSError:
    pass  # Railway ephemeral fs — stdout only is fine
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=_log_handlers,
)
LOG = logging.getLogger("bot8")


def _load_config(argv: list[str]) -> dict:
    p = Path(argv[1]) if len(argv) > 1 else PROJECT_ROOT / "config" / "live.yaml"
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    return yaml.safe_load(p.read_text())


_lock_fd: int | None = None


def _acquire_lock() -> None:
    """Exclusive flock so only one bot8 instance can run at a time."""
    global _lock_fd
    lock_path = PROJECT_ROOT / "logs" / "bot8.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY)
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(
            "Bot 8 is already running — only one instance allowed. "
            "Stop the existing process before starting a new one.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    os.write(_lock_fd, str(os.getpid()).encode())


def _guard_live() -> None:
    if not os.getenv("BOT8_LIVE_CONFIRMED"):
        print(
            "Set BOT8_LIVE_CONFIRMED=1 in your .env to confirm live trading.",
            file=sys.stderr,
        )
        raise SystemExit(3)


async def _run(cfg: dict) -> None:
    api_key = os.environ["BYBIT_API_KEY"]
    api_secret = os.environ["BYBIT_API_SECRET"]

    ex = BybitConnector(
        api_key=api_key,
        api_secret=api_secret,
        symbol=cfg["exchange"]["symbol"],
        leverage=cfg["exchange"].get("leverage", 3),
    )

    strat_cfg = cfg["strategy"]
    size_cfg = cfg["sizing"]
    strategy = AvaxSpectralStrategy(
        equity_pct=size_cfg["equity_pct"],
        tc_thresh=strat_cfg["tc_thresh"],
        ema_fast=strat_cfg["ema_fast"],
        ema_slow=strat_cfg["ema_slow"],
        sl_pct=strat_cfg["sl_pct"],
        tp_pct=strat_cfg["tp_pct"],
        use_shorts=strat_cfg["use_shorts"],
        am_mult_max=size_cfg["am_mult_max"],
        am_mult_fallback=size_cfg["am_mult_fallback"],
        am_stk_min=size_cfg["am_streak_min"],
        max_consec_3x=size_cfg["max_consec_3x"],
        time_exit_bars=strat_cfg.get("time_exit_bars", 24),
    )

    # DB path: BOT8_DB_PATH (absolute) lets you point at a Railway volume so the
    # trade/equity history survives redeploys. Falls back to the in-repo path.
    db_cfg = os.getenv("BOT8_DB_PATH", cfg["persistence"]["db_path"])
    db_path = Path(db_cfg)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    kill_switch = PROJECT_ROOT / "logs" / ".kill_switch"

    runner = Bot8Runner(
        exchange=ex,
        strategy=strategy,
        db_path=db_path,
        kill_switch=kill_switch,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: loop.create_task(runner.stop()))

    telegram.init(
        token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )
    print(f"Bot 8 — AVAX Spectral | {cfg['exchange']['symbol']} | LIVE MODE", flush=True)
    await runner.start()


def main() -> int:
    try:
        cfg = _load_config(sys.argv)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    _acquire_lock()
    _guard_live()

    try:
        asyncio.run(_run(cfg))
        return 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
