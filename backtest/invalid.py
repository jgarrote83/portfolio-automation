"""Report on the symbols the bars endpoint rejected as invalid (HTTP 400 `invalid symbol: X`).

The inactive-assets list carries placeholders (CUSIP-like codes such as `0029900E0`, `*_DELISTED`)
that the endpoint will not serve. The data layer drops them and retries; this module turns the
persisted list into a statement that can be checked: how many, which ones (all if fewer than 100,
otherwise an evenly spaced sample of 20), and whether any of them ever returned a daily bar or
passed the universe filter on any day. A symbol the API cannot serve has no bars, so it cannot --
the check is computed from the run's own data, not assumed.
"""
from __future__ import annotations

from collections.abc import Iterable

LIST_MAX = 100          # list every symbol when there are fewer than this many
SAMPLE_N = 20           # otherwise show this many, evenly spaced through the sorted list


def invalid_summary(rejected: Iterable[str], considered: Iterable[str], daily_symbols: Iterable[str],
                    eligible_union: Iterable[str]) -> dict:
    """`rejected`: every symbol recorded as rejected (any run). `considered`: this run's symbol
    universe. `daily_symbols`: symbols that returned at least one daily bar. `eligible_union`: every
    symbol that passed the universe filter on any day."""
    rejected = set(rejected)
    mine = sorted(rejected & set(considered))
    n = len(mine)
    if n < LIST_MAX:
        listing = {"mode": "full", "symbols": mine}
    else:
        step = n / SAMPLE_N
        listing = {"mode": "sample", "symbols": [mine[int(i * step)] for i in range(SAMPLE_N)]}
    return {
        "count": n,
        "count_recorded_in_cache": len(rejected),
        "listing": listing,
        "returned_a_daily_bar": sorted(set(mine) & set(daily_symbols)),
        "passed_universe_filter": sorted(set(mine) & set(eligible_union)),
    }


def render_invalid_md(d: dict | None) -> str:
    if not d:
        return ""
    lst = d["listing"]
    shown = ", ".join(f"`{s}`" for s in lst["symbols"]) or "none"
    head = (f"The bars endpoint rejected **{d['count']:,}** of this run's symbols as invalid "
            f"(HTTP 400 `invalid symbol`); the data layer dropped each one and retried the rest of the request. "
            f"These are placeholders from the inactive-assets list, not tradable tickers.")
    if d["count_recorded_in_cache"] != d["count"]:
        head += (f" ({d['count_recorded_in_cache']:,} rejected symbols are recorded in the cache in all; "
                 f"{d['count']:,} belong to this run's universe.)")
    listing = (f"Full list ({d['count']:,}): {shown}." if lst["mode"] == "full" else
               f"Sample of {len(lst['symbols'])} (evenly spaced through the sorted list of {d['count']:,}): {shown}.")
    bars, passed = d["returned_a_daily_bar"], d["passed_universe_filter"]
    if not bars and not passed:
        check = ("**Check, computed from this run's own data:** none of them returned a single daily bar, and none "
                 "passed the universe filter on any day (the intersection of the rejected set with every day's "
                 "eligible set is empty).")
    else:
        check = (f"**UNEXPECTED — investigate before trusting these numbers:** {len(bars)} rejected symbol(s) returned a "
                 f"daily bar ({', '.join(bars[:10])}) and {len(passed)} passed the universe filter "
                 f"({', '.join(passed[:10])}).")
    return f"## Symbols the bars endpoint rejected\n\n{head}\n\n{listing}\n\n{check}\n"
