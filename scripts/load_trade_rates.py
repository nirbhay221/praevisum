"""What each trade actually pays, from BLS.

WHY BOTH AND NOT ONE

This service answers two businesses' phones and the pricing was written for
one of them. `labour_rate` named occupation 49-9021, refrigeration mechanics,
as a constant, so an IT job was quoted at a refrigeration mechanic's wage and
said with the same confidence as everything else.

WHY THE TWO FIGURES ARE NOT THE SAME KIND OF NUMBER

BLS publishes 49-9021 for the Davenport metro, which is where this dealer
actually works. It does NOT publish 15-1232 for that metro: the series simply
does not exist. So refrigeration is local and IT is state-wide, and each row
records which, because quoting a national average as though it were local is
the same class of quiet dishonesty as inventing one.

Run: python -m scripts.load_trade_rates
"""

from __future__ import annotations

from src import db

# Verified against the BLS public API, 2025.
#
#   49-9021  Heating, Air Conditioning and Refrigeration Mechanics
#            Davenport-Moline-Rock Island metro, median $31.34/hr
#   15-1232  Computer User Support Specialists
#            no metro series exists; Iowa state median $28.27/hr
RATES = [
    ("refrigeration", "49-9021",
     "Heating, Air Conditioning and Refrigeration Mechanics and Installers",
     31.34, "OEUM001934000000049902108", "Davenport-Moline-Rock Island metro",
     2025, 2.6, 95.0),

    # A lower multiplier and a lower call-out on purpose. An IT technician
    # carries a toolkit and a bench; a refrigeration technician carries a van,
    # its stock, a recovery machine and an EPA certification. The overhead the
    # rate has to cover is genuinely different, and using one number for both
    # would be the same mistake in the opposite direction.
    ("it", "15-1232", "Computer User Support Specialists",
     28.27, "OEUS190000000000015123208", "Iowa (no metro series is published)",
     2025, 2.2, 65.0),
]


def load() -> dict:
    db.init()
    with db.txn() as c:
        for row in RATES:
            c.execute(
                """INSERT OR REPLACE INTO trade_rates
                   (trade, occupation, occupation_name, hourly_wage, series_id,
                    geography, year, multiplier, call_out)
                   VALUES (?,?,?,?,?,?,?,?,?)""", row)

    with db.connect() as c:
        rows = c.execute("SELECT * FROM trade_rates ORDER BY trade").fetchall()
    return {"trades": len(rows), "rows": [dict(r) for r in rows]}


if __name__ == "__main__":
    out = load()
    for r in out["rows"]:
        print(f"  {r['trade']:<15} {r['occupation']}  ${r['hourly_wage']}/hr "
              f"x{r['multiplier']}  = ${round(r['hourly_wage']*r['multiplier'],2)}/hr "
              f"charged   ({r['geography']})")
