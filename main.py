"""Bot 8 entry point. Run: python main.py [config/live.yaml]"""
from __future__ import annotations

import asyncio
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(PROJECT_ROOT / "logs" / "bot8.log"),
    ],
)
LOG = logging.getLogger("bot8")


def _load_config(argv: list[str]) -> dict:
    p = Path(argv[1]) if len(argv) > 1 else PROJECT_ROOT / "config" / "live.yaml"
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    return yaml.safe_load(p.read_text())


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
    )

    db_path = PROJECT_ROOT / cfg["persistence"]["db_path"]
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
    telegram.send("🚀 <b>Bot 8 — AVAX Spectral started</b>\nRunning on AVAX/USDT Perp | 3x | 30m bars")
    print(f"Bot 8 — AVAX Spectral | {cfg['exchange']['symbol']} | LIVE MODE", flush=True)
    await runner.start()


def main() -> int:
    try:
        cfg = _load_config(sys.argv)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    _guard_live()

    try:
        asyncio.run(_run(cfg))
        return 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
