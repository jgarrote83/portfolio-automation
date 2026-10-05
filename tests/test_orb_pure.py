"""src/orb is PURE: stdlib + shared only, no I/O, no registration -- and its rules are right."""
import ast
import subprocess
import sys
from pathlib import Path

import pytest
from orb import signals, sizing, universe
from orb.config import OrbConfig, hhmm_to_minutes
from orb.signals import LONG, SHORT

SRC = Path(__file__).resolve().parents[1] / "src"
ORB = SRC / "orb"
CFG = OrbConfig()


# ====================================================================================== purity
def _imported_roots(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            out.add((node.module or "").split(".")[0])
    return out


def test_orb_imports_only_the_standard_library_and_shared():
    allowed_extra = {"shared"}
    for f in sorted(ORB.glob("*.py")):
        roots = _imported_roots(f)
        bad = {r for r in roots if r not in sys.stdlib_module_names and r not in allowed_extra}
        assert not bad, f"{f.name} imports non-stdlib modules {bad}"


def test_only_config_reads_the_environment_and_nothing_does_file_or_network_io():
    for f in sorted(ORB.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "open" not in names, f.name
        assert not ({"sleep", "urlopen", "connect"} & attrs), f.name
        roots = _imported_roots(f)
        assert not ({"socket", "subprocess", "requests", "urllib", "http", "pathlib", "time",
                     "datetime", "threading", "asyncio", "random"} & roots), f.name
        if f.name != "config.py":
            assert "os" not in roots, f"{f.name} must not touch the environment"


def test_orb_imports_without_pandas_numpy_or_azure_installed():
    code = (
        "import sys\n"
        "for m in ('pandas','numpy','pyarrow','requests','azure','azure.functions'):\n"
        "    sys.modules[m] = None\n"                       # make any import of them fail
        f"sys.path.insert(0, {str(SRC)!r})\n"
        "import orb, orb.config, orb.universe, orb.signals, orb.sizing\n"
        "assert orb.universe.core_roster()\n"
        "print('ok')\n")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0 and "ok" in res.stdout, res.stderr


def test_nothing_in_the_function_app_registers_or_imports_orb():
    for f in [SRC / "function_app.py", *sorted(SRC.glob("*/handler.py"))]:
        text = f.read_text(encoding="utf-8")
        assert not any(line.strip().startswith(("import orb", "from orb")) for line in text.splitlines()), f.name


# ===================================================================================== config
def test_env_overrides_parse_by_type_and_spec_version_is_not_overridable():
    cfg = OrbConfig.from_env({"ORB_TOP_N": "10", "ORB_MIN_PRICE": "7.5", "ORB_ALLOW_SHORTS": "false",
                              "ORB_ENTRY_START": "09:40", "ORB_SLIPPAGE_CENTS_PER_SIDE": "1"})
    assert (cfg.top_n, cfg.min_price, cfg.allow_shorts, cfg.entry_start) == (10, 7.5, False, "09:40")
    assert cfg.slippage_cents_per_side == 1.0 and isinstance(cfg.slippage_cents_per_side, float)
    assert cfg.diff_from_default() == {"top_n": 10, "min_price": 7.5, "allow_shorts": False,
                                       "entry_start": "09:40", "slippage_cents_per_side": 1.0}
    assert OrbConfig.from_env({}) == OrbConfig() and OrbConfig().diff_from_default() == {}
    with pytest.raises(ValueError, match="cannot be overridden"):
        OrbConfig.from_env({"ORB_SPEC_VERSION": "orb-9"})
    with pytest.raises(ValueError, match="ORB_TOP_N"):
        OrbConfig.from_env({"ORB_TOP_N": "many"})
    with pytest.raises(ValueError, match="ORB_ALLOW_SHORTS"):
        OrbConfig.from_env({"ORB_ALLOW_SHORTS": "maybe"})


def test_derived_values_and_session_minutes():
    assert hhmm_to_minutes("09:35") == 575 and CFG.entry_start_minute == 575
    assert CFG.opening_window_minutes == (570, 575)
    assert CFG.last_entry_minute() == 15 * 60 + 45 and CFG.exit_minute() == 15 * 60 + 59
    assert CFG.last_entry_minute(13 * 60) == 12 * 60 + 45 and CFG.exit_minute(13 * 60) == 12 * 60 + 59
    assert CFG.risk_budget_usd == 250.0 and CFG.max_position_usd == 1250.0
    assert CFG.daily_loss_limit_usd == 750.0 and CFG.slippage_per_share == 0.02


# ==================================================================================== universe
def _hist(n=14, high=11.0, low=9.0, close=10.0, volume=2_000_000):
    return [high] * n, [low] * n, [close] * n, [volume] * n


def test_atr_is_the_simple_mean_of_the_prior_true_ranges_hand_computed():
    cfg = CFG.replace(lookback_days=3)
    # bars (oldest first): H/L/C = 12/10/11, 14/11/13, 13/12/12.5 ; prior close 10.5
    # TR1 = max(2, |12-10.5|, |10-10.5|) = 2 ; TR2 = max(3, |14-11|, |11-11|) = 3 ;
    # TR3 = max(1, |13-13|, |12-13|) = 1  -> simple mean 2.0 (a Wilder-smoothed ATR would differ)
    atr = universe.atr_simple([12, 14, 13], [10, 11, 12], [11, 13, 12.5], 10.5, cfg)
    assert atr == pytest.approx(2.0)
    # a gap-up from the prior close dominates the range: TR = |high - prev_close|
    assert universe.true_range(20.0, 19.5, 15.0) == 5.0
    assert universe.true_range(20.0, 19.5, 25.0) == 5.5          # gap down: |low - prev_close|


def test_atr_tolerates_a_missing_oldest_prior_close_but_no_missing_bar():
    cfg = CFG.replace(lookback_days=2)
    assert universe.atr_simple([12, 14], [10, 11], [11, 13], None, cfg) == pytest.approx((2 + 3) / 2)
    assert universe.atr_simple([12, 14], [10, 11], [11, 13], float("nan"), cfg) == pytest.approx(2.5)
    assert universe.atr_simple([12, None], [10, 11], [11, 13], 10.0, cfg) is None      # a missing bar
    assert universe.atr_simple([12], [10], [11], 10.0, cfg) is None                    # wrong length
    assert universe.average_volume([1, 2, float("nan")], CFG.replace(lookback_days=3)) is None


def test_universe_thresholds_are_strict_where_the_rule_says_over_and_inclusive_where_it_says_at_least():
    h, lo, c, v = _hist()
    base = dict(highs=h, lows=lo, closes=c, volumes=v, prior_close=10.0)
    assert universe.check_universe(5.01, **base, cfg=CFG).ok
    assert not universe.check_universe(5.00, **base, cfg=CFG).ok                      # open must EXCEED $5
    assert universe.check_universe(10.0, [11.0] * 14, [9.0] * 14, c, [1_000_000] * 14, 10.0, CFG).ok       # >= 1M
    assert not universe.check_universe(10.0, [11.0] * 14, [9.0] * 14, c, [999_999] * 14, 10.0, CFG).ok
    flat = ([10.25] * 14, [9.75] * 14, [10.0] * 14, v)                                  # ATR exactly 0.50
    assert not universe.check_universe(10.0, *flat, 10.0, CFG).ok                      # ATR must EXCEED 0.50
    assert universe.check_universe(10.0, [10.26] * 14, [9.75] * 14, [10.0] * 14, v, 10.0, CFG).ok
    assert not universe.check_universe(None, **base, cfg=CFG).ok
    assert universe.check_universe(10.0, *_hist(), 10.0, CFG).atr == pytest.approx(2.0)


def test_relative_volume_zero_fills_missing_bars_and_never_includes_today_in_the_average():
    prior = [100.0] * 14
    assert universe.relative_volume(250.0, prior, CFG) == pytest.approx(2.5)
    assert universe.relative_volume(100.0, prior, CFG) == pytest.approx(1.0)                # exactly 100%
    assert universe.relative_volume(None, prior, CFG) == 0.0                               # today missing = 0
    holes = [100.0] * 7 + [None] * 7                                                        # missing = zero
    assert universe.relative_volume(100.0, holes, CFG) == pytest.approx(100.0 / 50.0)
    assert universe.relative_volume(100.0, [0.0] * 14, CFG) is None                        # avg must be > 0
    assert universe.relative_volume(100.0, [None] * 14, CFG) is None
    assert universe.relative_volume(100.0, prior[:13], CFG) is None                        # needs 14 days


def test_stocks_in_play_are_the_top_20_at_100_percent_ties_broken_by_symbol():
    rv = {f"S{i:02d}": 1.0 + i / 100 for i in range(30)}
    rv.update({"AAA": 1.29, "ZZZ": 1.29, "LOW": 0.99, "NONE": None})
    got = universe.rank_stocks_in_play(rv, CFG)
    assert len(got) == 20 and got[:3] == ["AAA", "S29", "ZZZ"]                 # three-way tie at 1.29: by symbol
    assert "LOW" not in got and "NONE" not in got
    assert universe.rank_stocks_in_play({"A": 1.0, "B": 0.999}, CFG) == ["A"]
    assert universe.rank_stocks_in_play({}, CFG) == []
    assert universe.rank_stocks_in_play(rv, CFG.replace(top_n=2)) == ["AAA", "S29"]


# ===================================================================================== signals
def test_direction_from_the_first_five_minute_bar():
    assert signals.direction_from_bar(10.0, 10.01) == LONG
    assert signals.direction_from_bar(10.0, 9.99) == SHORT
    assert signals.direction_from_bar(10.0, 10.0) is None                      # doji


def test_stop_rounds_away_from_the_entry_never_tighter_than_the_rule():
    assert signals.round_stop(100.801, LONG, 0.01) == 100.80                    # long: down
    assert signals.round_stop(100.799, LONG, 0.01) == 100.79
    assert signals.round_stop(99.201, SHORT, 0.01) == 99.21                     # short: up
    assert signals.round_stop(100.8, LONG, 0.01) == 100.80                      # float noise does not push it
    assert signals.round_stop(10079.999999999998 / 100, LONG, 0.01) == 100.80
    plan = signals.entry_plan(LONG, 101.0, 99.5, 2.0, CFG)
    assert plan == signals.EntryPlan(LONG, 101.0, 100.80, 0.2)
    plan = signals.entry_plan(SHORT, 100.5, 99.0, 2.0, CFG)
    assert plan == signals.EntryPlan(SHORT, 99.0, 99.20, 0.2)
    odd = signals.entry_plan(LONG, 50.0, 49.0, 1.234, CFG)                     # raw stop 49.8766 -> 49.87
    assert odd.stop == 49.87 and odd.stop_distance == 0.13 and odd.stop <= 50.0 - 0.1 * 1.234
    with pytest.raises(ValueError):
        signals.entry_plan("flat", 1, 1, 1, CFG)


def test_entry_fills_at_the_level_or_at_the_open_on_a_gap_through_it():
    assert signals.trigger_fill(LONG, 100.9, 101.0, 100.8, 101.0) == 101.0       # touches the level
    assert signals.trigger_fill(LONG, 100.9, 100.99, 100.8, 101.0) is None
    assert signals.trigger_fill(LONG, 101.5, 101.6, 101.4, 101.0) == 101.5       # gaps through: open
    assert signals.trigger_fill(SHORT, 99.1, 99.2, 99.0, 99.0) == 99.0
    assert signals.trigger_fill(SHORT, 99.1, 99.2, 99.01, 99.0) is None
    assert signals.trigger_fill(SHORT, 98.5, 98.6, 98.4, 99.0) == 98.5           # gaps down through it


def test_stop_fills_at_the_level_or_at_the_open_on_a_gap_through_it():
    assert signals.stop_fill(LONG, 100.9, 101.0, 100.80, 100.80) == 100.80
    assert signals.stop_fill(LONG, 100.9, 101.0, 100.81, 100.80) is None
    assert signals.stop_fill(LONG, 100.5, 100.6, 100.3, 100.80) == 100.5          # gap: the open
    assert signals.stop_fill(SHORT, 99.1, 99.20, 99.0, 99.20) == 99.20
    assert signals.stop_fill(SHORT, 99.5, 99.6, 99.4, 99.20) == 99.5
    assert signals.stop_hit_in_entry_bar(LONG, 101.1, 100.8, 100.8)
    assert not signals.stop_hit_in_entry_bar(LONG, 101.1, 100.81, 100.8)
    assert signals.stop_hit_in_entry_bar(SHORT, 99.2, 99.0, 99.2)


# ====================================================================================== sizing
def test_the_position_cap_binds_for_a_normal_stock():
    # $101 stock, ATR $2 -> R $0.20: risk rule says 1250 shares, the sleeve/20 cap says 12
    r = sizing.position_size(101.0, 0.20, CFG)
    assert (r.shares, r.binding) == (12, "cap")
    assert r.risk_shares == pytest.approx(1250.0) and r.cap_shares == pytest.approx(1250 / 101)


def test_the_risk_rule_binds_when_the_stop_is_wide_relative_to_price():
    r = sizing.position_size(10.0, 3.0, CFG)                  # risk 250/3 = 83.3 ; cap 125
    assert (r.shares, r.binding) == (83, "risk")
    # arithmetic fact the module documents: with R = 0.1 x ATR, risk binds only if price <= 0.5 x ATR
    atr = 40.0
    assert sizing.position_size(10.0, 0.1 * atr, CFG).binding == "risk"
    assert sizing.position_size(100.0, 0.1 * 3.0, CFG).binding == "cap"


def test_shares_are_whole_and_float_noise_never_drops_a_share():
    assert sizing.position_size(126.0, 0.2, CFG).shares == 9                    # 1250/126 = 9.92 -> 9
    assert sizing.position_size(125.0, 0.2, CFG).shares == 10                   # exactly 10.0
    cfg = CFG.replace(sleeve_capital_usd=30.0, position_slots=1)
    assert 30.0 * 1.0 / 100.0 / 0.1 < 3                                         # raw float is 2.9999999999999996
    r = sizing.position_size(0.5, 0.1, cfg)
    assert (r.shares, r.binding) == (3, "risk")
    assert sizing.position_size(1500.0, 0.2, CFG).shares == 0                   # < 1 share: skip
    assert sizing.position_size(10.0, 5.0, CFG, sleeve_capital=5.0).shares == 0


def test_a_tie_and_invalid_inputs():
    # risk 250/0.2 = 1250 ; cap 1250/price: price 1.0 makes them equal
    assert sizing.position_size(1.0, 0.2, CFG).binding == "tie"
    for args in ((0.0, 0.2), (10.0, 0.0), (-5.0, 0.2), (10.0, -0.1)):
        r = sizing.position_size(*args, CFG)
        assert (r.shares, r.binding) == (0, "invalid")


def test_the_sleeve_is_never_levered_even_with_every_slot_filled():
    price, stop = 37.0, 0.18
    r = sizing.position_size(price, stop, CFG)
    assert r.shares * price * CFG.position_slots <= CFG.sleeve_capital_usd
