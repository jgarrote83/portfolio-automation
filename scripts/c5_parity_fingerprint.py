"""Fingerprint of C5's primary runs (2005-2025 and 1989-2004), offline from the caches: every decision and every daily equity value."""
import hashlib
import json
import sys

sys.path[:0] = ["src", "."]

from research.core_candidates import engine as E  # noqa: E402
from research.core_candidates import oos as O  # noqa: E402
from research.core_candidates import oos_data as OD  # noqa: E402
from research.core_candidates import run as R  # noqa: E402

def dump(res):
    dec = [(str(x["decision_date"]), str(x["execution_date"]), x["label"], x.get("trend"), x.get("credit")) for x in res.executions]
    eq = [float(v).hex() for v in res.equity]                        # exact bits
    return {"decisions": dec, "equity_hex": eq}

m = R.load_market()
a = dump(E.simulate(m, E.DEFAULTS["C5"]))
mo = O.build_oos_market(OD.read("yahoo", OD.INDEX), OD.read("fred", "DTB3"), OD.read("fred", "BAA10Y"))
b = dump(O.simulate(mo, "C5"))
out = {"2005-2025": a, "1989-2004": b}
blob = json.dumps(out, sort_keys=True).encode()
print(sys.argv[1], "sha256", hashlib.sha256(blob).hexdigest(), "decisions", len(a["decisions"]), len(b["decisions"]), "days", len(a["equity_hex"]), len(b["equity_hex"]))
open(sys.argv[1], "wb").write(blob)
