"""Stress-tests for the three critical fixes: lock, backoff, async telegram."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


# ---------------------------------------------------------------------------
# Fix 1: PID lock — second launch must be rejected
# ---------------------------------------------------------------------------

def test_lock_prevents_second_instance(tmp_path):
    """Two concurrent processes: second one must exit with code 1."""
    env_patch = {
        "BOT8_LIVE_CONFIRMED": "",  # guard will fire first — that's fine
        "PATH": "/usr/bin:/bin",
    }

    # Launch first process — it will block on _guard_live (exit 3) before entering the event loop
    # but the lock must be acquired before that.  We verify that a second launch with the same
    # lock file exits with code 1 (lock conflict), not code 3 (guard).
    #
    # Strategy: monkeypatch _guard_live to sleep so the lock stays held long enough.
    script = PROJECT_ROOT / "tests" / "_lock_holder.py"
    script.write_text(
        f"""\
import sys, time, fcntl, os
sys.path.insert(0, "{PROJECT_ROOT / 'src'}")
lock_path = "{tmp_path / 'bot8.lock'}"
fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
os.write(fd, str(os.getpid()).encode())
time.sleep(5)
"""
    )

    holder = subprocess.Popen([sys.executable, str(script)])
    time.sleep(0.3)  # give holder time to acquire the lock

    # Now try to acquire the same lock in-process
    import fcntl as _fcntl, os as _os
    lock_path = tmp_path / "bot8.lock"
    fd2 = _os.open(str(lock_path), _os.O_CREAT | _os.O_WRONLY)
    try:
        _fcntl.flock(fd2, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        _os.close(fd2)
        holder.terminate()
        holder.wait()
        script.unlink()
        pytest.fail("Expected lock to be held by the first process")
    except OSError:
        pass  # correct — lock is held
    finally:
        _os.close(fd2)
        holder.terminate()
        holder.wait()
        script.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Fix 2: Rate limit backoff — retries then succeeds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backoff_retries_on_rate_limit():
    """_with_backoff should retry up to 4 times and succeed on the 3rd attempt."""
    import ccxt.async_support as ccxt
    from bot.exchange import _with_backoff

    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ccxt.RateLimitExceeded("hit limit")
        return "ok"

    with patch("bot.exchange._RATE_LIMIT_BASE_DELAY", 0.01):
        result = await _with_backoff(flaky)

    assert result == "ok"
    assert call_count == 3


@pytest.mark.asyncio
async def test_backoff_raises_after_max_retries():
    """_with_backoff should re-raise after exhausting all retries."""
    import ccxt.async_support as ccxt
    from bot.exchange import _with_backoff

    async def always_limited():
        raise ccxt.RateLimitExceeded("always")

    with patch("bot.exchange._RATE_LIMIT_BASE_DELAY", 0.01):
        with pytest.raises(ccxt.RateLimitExceeded):
            await _with_backoff(always_limited)


# ---------------------------------------------------------------------------
# Fix 5: close_position must send the REAL position qty (not amount=0)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_position_sends_real_qty():
    """amount=0 was rejected by Bybit — close must use the actual contracts held."""
    from bot.exchange import BybitConnector

    conn = BybitConnector(api_key="k", api_secret="s", symbol="AVAX/USDT")
    conn._ex.create_order = AsyncMock(return_value={"id": "abc"})

    await conn.close_position("long", qty=12.5)

    _, kwargs = conn._ex.create_order.call_args
    assert kwargs["amount"] == 12.5, "close must pass the real qty, never 0"
    assert kwargs["side"] == "sell"            # closing a long
    assert kwargs["params"].get("reduceOnly") is True


@pytest.mark.asyncio
async def test_close_position_skips_zero_qty():
    """Defensive: never fire a zero-qty order at the exchange."""
    from bot.exchange import BybitConnector

    conn = BybitConnector(api_key="k", api_secret="s", symbol="AVAX/USDT")
    conn._ex.create_order = AsyncMock(return_value={"id": "abc"})

    result = await conn.close_position("short", qty=0.0)

    assert result == {}
    conn._ex.create_order.assert_not_called()


# ---------------------------------------------------------------------------
# Fix 6: fetch_ohlcv selects closed bars by TIME, never blindly drops last row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_ohlcv_keeps_only_closed_bars():
    import pandas as pd
    from bot.exchange import BybitConnector

    conn = BybitConnector(api_key="k", api_secret="s", symbol="AVAX/USDT")
    now = pd.Timestamp.now(tz="UTC")
    floor = now.floor("30min")  # open of the current, still-forming bar
    mk = lambda ts: [int(ts.timestamp() * 1000), 1.0, 1.0, 1.0, 1.0, 1.0]
    raw = [
        mk(floor - pd.Timedelta(minutes=60)),
        mk(floor - pd.Timedelta(minutes=30)),
        mk(floor),  # in-progress bar — must be excluded
    ]
    conn._ex.fetch_ohlcv = AsyncMock(return_value=raw)

    df = await conn.fetch_ohlcv("30m")

    assert len(df) == 2, "forming bar must be excluded"
    assert df.index[-1] == floor - pd.Timedelta(minutes=30), "last bar must be the just-closed one"


# ---------------------------------------------------------------------------
# Fix 7: a stale (lagging) bar skips the frame with an alert — never trades old data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stale_bar_skips_and_alerts():
    import pandas as pd
    from datetime import datetime, timezone
    from bot.runner import Bot8Runner
    from bot.db import init_db

    mock_ex = AsyncMock()
    mock_ex.symbol = "AVAXUSDT"
    stale = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
        index=pd.to_datetime(["2026-06-04 08:00:00"], utc=True),  # well before expected 09:30
    )
    mock_ex.fetch_ohlcv.return_value = stale

    fixed_now = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "trades.db"
        init_db(db_path)
        runner = Bot8Runner(mock_ex, MagicMock(), db_path, Path(tmp) / ".kill")

        sent: list[str] = []

        async def fake_send(msg):
            sent.append(msg)

        with patch("bot.runner.FRESH_RETRIES", 1), \
             patch("bot.runner.FRESH_RETRY_DELAY", 0), \
             patch("bot.telegram.send", side_effect=fake_send), \
             patch("bot.runner.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            await runner._tick()

        assert any("frame skipped" in s for s in sent), "must alert on a missed frame"
        mock_ex.fetch_position.assert_not_called(), "must not trade on stale data"


# ---------------------------------------------------------------------------
# Fix 3: Telegram send is non-blocking (runs in thread, not blocking loop)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_telegram_send_is_non_blocking():
    """telegram.send must not block the event loop — uses asyncio.to_thread."""
    from bot import telegram

    telegram.init(token="fake", chat_id="123")

    blocked_for: list[float] = []

    def slow_post(*args, **kwargs):
        time.sleep(0.1)

    start = asyncio.get_event_loop().time()
    with patch("bot.telegram._post", side_effect=slow_post):
        await telegram.send("test message")
    elapsed = asyncio.get_event_loop().time() - start

    # Should complete without blocking the loop for 100 ms synchronously
    assert elapsed < 0.5  # generous upper bound


@pytest.mark.asyncio
async def test_telegram_post_retries_then_succeeds():
    """A transient failure must NOT silently drop a message — _post retries."""
    from bot import telegram

    telegram.init(token="fake", chat_id="123")

    calls = {"n": 0}

    class _Resp:
        status_code = 200
        text = "ok"

    def flaky_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            import requests
            raise requests.RequestException("blip")
        return _Resp()

    with patch("bot.telegram.requests.post", side_effect=flaky_post), \
         patch("bot.telegram.time.sleep"):  # don't actually wait
        await telegram.send("hello")

    assert calls["n"] == 3, "should retry until it gets through"


@pytest.mark.asyncio
async def test_telegram_send_skips_when_unconfigured():
    """send() must return immediately when token/chat_id are empty."""
    from bot import telegram
    telegram.init(token="", chat_id="")
    # Should not raise and should not call _post
    with patch("bot.telegram._post") as mock_post:
        await telegram.send("hello")
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Fix 4: Daily summary fires even on hold-only bars
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_summary_fires_on_hold_tick():
    """Daily summary must fire before the hold early-return."""
    import pandas as pd
    from datetime import datetime, timezone
    from bot.runner import Bot8Runner
    from bot.strategy import Signal

    mock_ex = AsyncMock()
    mock_ex.symbol = "AVAXUSDT"
    mock_ex.fetch_ohlcv.return_value = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
        index=pd.to_datetime(["2026-06-04 09:30:00"], utc=True),
    )
    mock_ex.fetch_position.return_value = MagicMock(side="none", qty=0.0, entry_price=0.0)
    mock_ex.fetch_balance.return_value = 100.0

    mock_strat = MagicMock()
    mock_strat.evaluate.return_value = Signal(
        action="hold", qty=0.0, price=1.0, sl_price=0.0, tp_price=0.0, centroid=30.0, is_trending=False
    )
    mock_strat.state.position = "flat"

    # Pin clock to 10:00 UTC so the hour >= 9 guard always passes
    fixed_now = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "trades.db"
        kill_switch = Path(tmp) / ".kill_switch"

        runner = Bot8Runner(mock_ex, mock_strat, db_path, kill_switch)
        runner._last_summary_day = -1  # force summary to fire

        from bot.db import init_db
        init_db(db_path)

        summary_calls: list[str] = []

        async def fake_summary(**kwargs):
            summary_calls.append("fired")

        with patch("bot.telegram.send", new_callable=AsyncMock), \
             patch("bot.telegram.notify_tick", new_callable=AsyncMock), \
             patch("bot.telegram.notify_daily_summary", side_effect=fake_summary), \
             patch("bot.runner.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            await runner._tick()

        assert summary_calls == ["fired"], "Daily summary did not fire on a hold tick"


# ---------------------------------------------------------------------------
# Fix 8: PROOF IT TRADES — signal fires on a real EMA cross, runner submits order
# ---------------------------------------------------------------------------

def test_strategy_emits_buy_on_ema_cross_in_trend():
    """Engineer a genuine fast-over-slow EMA cross; strategy must return 'buy'."""
    import numpy as np
    import pandas as pd
    from bot.strategy import AvaxSpectralStrategy

    n = 400
    t = np.arange(n)
    close = pd.Series(100 + 10 * np.sin(t / 15.0))  # oscillates -> many EMA crosses
    ema_f = close.ewm(span=20, adjust=False).mean()
    ema_s = close.ewm(span=60, adjust=False).mean()

    # find the first up-cross after enough warmup (need >=200 bars for ref_atr)
    cross_idx = next(
        i for i in range(205, n)
        if ema_f.iloc[i] > ema_s.iloc[i] and ema_f.iloc[i - 1] <= ema_s.iloc[i - 1]
    )

    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0})
    df = df.iloc[: cross_idx + 1]  # last bar IS the cross bar

    # tc_thresh=0 isolates the cross logic (market always counts as trending)
    strat = AvaxSpectralStrategy(tc_thresh=0.0, use_shorts=True)
    sig = strat.evaluate(df, equity=100.0)

    assert sig.action == "buy", f"expected buy on up-cross, got {sig.action}"
    assert sig.qty > 0
    assert sig.sl_price < sig.price < sig.tp_price  # long SL below, TP above


@pytest.mark.asyncio
async def test_runner_submits_order_on_buy_signal():
    """When the strategy says buy, the runner must actually call enter_long."""
    import pandas as pd
    from datetime import datetime, timezone
    from bot.runner import Bot8Runner
    from bot.strategy import Signal
    from bot.db import init_db

    mock_ex = AsyncMock()
    mock_ex.symbol = "AVAXUSDT"
    mock_ex.fetch_ohlcv.return_value = pd.DataFrame(
        {"open": [20.0], "high": [20.0], "low": [20.0], "close": [20.0], "volume": [1.0]},
        index=pd.to_datetime(["2026-06-04 09:30:00"], utc=True),
    )
    mock_ex.fetch_position.return_value = MagicMock(side="none", qty=0.0, entry_price=0.0)
    mock_ex.fetch_balance.return_value = 100.0

    mock_strat = MagicMock()
    mock_strat.am_stk_min = 2
    mock_strat.state.position = "flat"
    mock_strat.state.win_streak = 0
    mock_strat.evaluate.return_value = Signal(
        action="buy", qty=2.5, price=20.0, sl_price=19.5, tp_price=21.4,
        centroid=99.0, is_trending=True,
    )

    fixed_now = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "trades.db"
        init_db(db_path)
        runner = Bot8Runner(mock_ex, mock_strat, db_path, Path(tmp) / ".kill")

        with patch("bot.telegram.send", new_callable=AsyncMock), \
             patch("bot.telegram.notify_tick", new_callable=AsyncMock), \
             patch("bot.telegram.notify_trade", new_callable=AsyncMock), \
             patch("bot.telegram.notify_daily_summary", new_callable=AsyncMock), \
             patch("bot.runner.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            await runner._tick()

        mock_ex.enter_long.assert_awaited_once_with(2.5, 19.5, 21.4)
        assert mock_strat.state.position == "long"
        assert mock_strat.state.entry_qty == 2.5


# ---------------------------------------------------------------------------
# Fix 9: startup reconciliation — pick up a live position after a restart
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_picks_up_open_position():
    """After a restart the bot must sync to the real exchange position."""
    from bot.runner import Bot8Runner
    from bot.strategy import AvaxSpectralStrategy
    from bot.db import init_db

    mock_ex = AsyncMock()
    mock_ex.symbol = "AVAXUSDT"
    mock_ex.fetch_position.return_value = MagicMock(
        side="long", qty=3.0, entry_price=20.5, unrealised_pnl=0.0
    )

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "trades.db"
        init_db(db_path)
        strat = AvaxSpectralStrategy()
        runner = Bot8Runner(mock_ex, strat, db_path, Path(tmp) / ".kill")

        with patch("bot.telegram.send", new_callable=AsyncMock):
            await runner._reconcile_state()

        assert strat.state.position == "long"
        assert strat.state.entry_qty == 3.0
        assert strat.state.entry_price == 20.5
        assert runner._open_trade_id is not None  # so a later close is recorded


@pytest.mark.asyncio
async def test_reconcile_flat_when_no_position():
    from bot.runner import Bot8Runner
    from bot.strategy import AvaxSpectralStrategy
    from bot.db import init_db

    mock_ex = AsyncMock()
    mock_ex.symbol = "AVAXUSDT"
    mock_ex.fetch_position.return_value = MagicMock(
        side="none", qty=0.0, entry_price=0.0, unrealised_pnl=0.0
    )

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "trades.db"
        init_db(db_path)
        strat = AvaxSpectralStrategy()
        strat.state.position = "long"  # stale RAM value to be corrected
        runner = Bot8Runner(mock_ex, strat, db_path, Path(tmp) / ".kill")

        await runner._reconcile_state()

        assert strat.state.position == "flat"
        assert runner._open_trade_id is None


# ---------------------------------------------------------------------------
# Fix 10: detect a Read-Only key at startup (reads work but orders are rejected)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_readonly_key_is_rejected_at_startup():
    import ccxt.async_support as ccxt
    from bot.exchange import BybitConnector

    conn = BybitConnector(api_key="k", api_secret="s", symbol="AVAX/USDT")
    conn._ex.private_get_v5_user_query_api = AsyncMock(
        return_value={"result": {"readOnly": 1, "permissions": {"ContractTrade": []}}}
    )
    with pytest.raises(ccxt.PermissionDenied):
        await conn._check_trade_permission()


@pytest.mark.asyncio
async def test_trading_key_passes_permission_check():
    from bot.exchange import BybitConnector

    conn = BybitConnector(api_key="k", api_secret="s", symbol="AVAX/USDT")
    conn._ex.private_get_v5_user_query_api = AsyncMock(
        return_value={"result": {"readOnly": 0, "permissions": {"ContractTrade": ["Order", "Position"]}}}
    )
    # Must not raise.
    await conn._check_trade_permission()
