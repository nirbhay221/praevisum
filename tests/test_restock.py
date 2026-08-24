"""What to put on the shelf, which is the van-loading decision slowed down.

Carrying a part for one job and stocking it for the month are the same
trade-off at different timescales, so they use the same two numbers: what a
wasted trip costs, and what it costs to have money sitting on a shelf. If they
disagreed, the desk would refuse to stock a part it would happily send out.

The bug this file mostly exists for was a modelling error rather than a coding
one. The first version reordered when stock fell below the demand during the
LEAD TIME, which is right only if somebody watches the shelf every day. Nobody
does, which is why there is a review period. With a monthly review and a
lead-time-only trigger, a part is reordered at about one unit left and then
runs dry for three weeks before anyone looks. Every number was correct and the
model was answering a question nobody had asked.
"""

from __future__ import annotations

from conftest import IT, REF


def _consume(db, sku, times, dealer=REF, asset="AS-FREEZER"):
    """Close `times` jobs that each used this part."""
    from datetime import date, timedelta

    with db.txn() as c:
        for i in range(times):
            when = (date.today() - timedelta(days=3 * i + 1)).isoformat()
            c.execute(
                """INSERT INTO repairs
                   (id,dealer_id,asset_id,manufacturer,model_number,
                    reported_symptom,found_cause,parts_consumed,closed_on)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (f"RU-{sku}-{i}", dealer, asset, "Traulsen", "G12010",
                 "not holding temp", "part replaced", sku, when))


def _set_stock(db, sku, qty, location="L-REF-WH"):
    with db.txn() as c:
        c.execute("INSERT OR REPLACE INTO stock (sku,location_id,on_hand) "
                  "VALUES (?,?,?)", (sku, location, qty))


def test_a_fast_moving_part_running_low_is_flagged(dbfile):
    from src import db, ops

    _consume(db, "P-DEFROSTTHE", 40)
    _set_stock(db, "P-DEFROSTTHE", 2)

    r = ops.restock_advice(REF)
    line = next((x for x in r["order"] if x["sku"] == "P-DEFROSTTHE"), None)
    assert line is not None, "a part used 40 times with 2 left was not flagged"
    assert line["order_qty"] >= 1
    assert line["used_in_last_year"] == 40


def test_the_review_period_is_in_the_reorder_point(dbfile):
    """The modelling error.

    A part with a one day lead time, used steadily, must still be reordered
    well before it hits one unit, because nobody looks again for a month.
    """
    from src import db, ops

    _consume(db, "P-DEFROSTTHE", 36)      # about 3 a month
    _set_stock(db, "P-DEFROSTTHE", 4)

    r = ops.restock_advice(REF)
    line = next(x for x in r["order"] if x["sku"] == "P-DEFROSTTHE")

    # lead time alone would put the trigger near zero; the review period must
    # push it up to roughly a month of demand plus a safety margin
    assert line["reorder_at"] > 3, \
        f"reorder point {line['reorder_at']} only covers the lead time"


def test_a_well_stocked_part_is_left_alone(dbfile):
    from src import db, ops

    _consume(db, "P-EVAPFAN", 6)
    _set_stock(db, "P-EVAPFAN", 40)

    r = ops.restock_advice(REF)
    assert all(x["sku"] != "P-EVAPFAN" for x in r["order"])
    assert any(x["sku"] == "P-EVAPFAN" for x in r["no_action"])


def test_a_part_nobody_uses_is_never_reordered(dbfile):
    """Zero stock is not a reason to buy something nothing needs."""
    from src import db, ops

    _set_stock(db, "P-CONTROLBOA", 0)

    r = ops.restock_advice(REF)
    assert all(x["sku"] != "P-CONTROLBOA" for x in r["order"])
    dormant = next(x for x in r["no_action"] if x["sku"] == "P-CONTROLBOA")
    assert "not used once" in dormant["note"]


def test_a_long_lead_time_raises_the_trigger(dbfile):
    """Two parts used identically, one slow to replace, must not tie."""
    from src import db, ops

    _consume(db, "P-DEFROSTTHE", 24)      # 2 day lead
    _consume(db, "P-CONTROLBOA", 24)      # 9 day lead
    _set_stock(db, "P-DEFROSTTHE", 3)
    _set_stock(db, "P-CONTROLBOA", 3)

    r = ops.restock_advice(REF)
    by_sku = {x["sku"]: x for x in r["order"]}
    assert by_sku["P-CONTROLBOA"]["reorder_at"] > by_sku["P-DEFROSTTHE"]["reorder_at"]


def test_stock_already_on_order_is_not_ordered_twice(dbfile):
    """Running the advice twice in a week must not double the shelf."""
    from src import db, ops

    _consume(db, "P-DEFROSTTHE", 40)
    _set_stock(db, "P-DEFROSTTHE", 2)

    first = ops.restock_advice(REF)
    qty = next(x for x in first["order"] if x["sku"] == "P-DEFROSTTHE")["order_qty"]

    po = ops.create_purchase_order("A-1", ["P-DEFROSTTHE"], "S-1")
    with db.txn() as c:
        c.execute("UPDATE purchase_lines SET qty=? WHERE po_id=?",
                  (qty, po["purchase_order"]))

    second = ops.restock_advice(REF)
    again = next((x for x in second["order"] if x["sku"] == "P-DEFROSTTHE"), None)
    assert again is None or again["order_qty"] < qty, \
        "ordered the same shortfall twice, ignoring what is already coming"


def test_being_short_is_priced_as_a_wasted_trip(dbfile):
    """Not the price of the part. The technician who cannot finish the job."""
    from src import db, ops
    from src.reason import TRUCK_ROLL

    _consume(db, "P-DEFROSTTHE", 40)
    _set_stock(db, "P-DEFROSTTHE", 1)

    r = ops.restock_advice(REF)
    line = next(x for x in r["order"] if x["sku"] == "P-DEFROSTTHE")
    assert line["cost_of_being_short"] >= TRUCK_ROLL
    assert line["cost_of_being_short"] > line["cost_of_holding_it"]


def test_restock_does_not_cross_dealers(dbfile):
    """One company's shelf is not another company's shelf."""
    from src import db, ops

    _consume(db, "P-DEFROSTTHE", 40)
    _set_stock(db, "P-DEFROSTTHE", 1)

    it = ops.restock_advice(IT)
    assert all(not x["sku"].startswith("P-") for x in it["order"])
    assert all(not x["sku"].startswith("P-") for x in it["no_action"])


def test_the_arithmetic_is_shown(dbfile):
    """The owner is spending money, so the working has to be visible."""
    from src import db, ops

    _consume(db, "P-DEFROSTTHE", 40)
    _set_stock(db, "P-DEFROSTTHE", 1)

    r = ops.restock_advice(REF)
    how = r["how_it_was_worked_out"]
    assert "review" in how["reorder_point"]
    line = next(x for x in r["order"] if x["sku"] == "P-DEFROSTTHE")
    for field in ("per_month", "lead_time_days", "in_stock", "reorder_at",
                  "cost_of_being_short", "cost_of_holding_it", "why"):
        assert field in line, field


def test_restock_is_an_owner_tool_not_a_phone_tool(dbfile):
    """Deciding what to buy is the owner's job.

    The phone agent's job is to be honest about what is on the shelf right
    now, not to commit the business to spending money.
    """
    from src import agents
    from src.console_agent import console_agent

    phone = {getattr(t, "__name__", getattr(t, "name", ""))
             for t in agents.front_agent.tools}
    owner = {getattr(t, "__name__", getattr(t, "name", ""))
             for t in console_agent.tools}

    assert "what_to_reorder" in owner
    assert "what_to_reorder" not in phone
    assert "restock_advice" not in phone


# --------------------------------------------------------------------------
# complaints as a leading indicator
# --------------------------------------------------------------------------

def _complain_recent(db, text, n, days_ago=10, dealer=REF, asset="AS-FREEZER"):
    from datetime import date, timedelta

    with db.txn() as c:
        for i in range(n):
            when = (date.today() - timedelta(days=days_ago)).isoformat()
            c.execute(
                """INSERT INTO complaints
                   (id,dealer_id,asset_id,manufacturer,model_number,family,
                    what,category,severity,raised_at,status,predicted_repair)
                   VALUES (?,?,?,?,?,?,?,'reliability','minor',?,'open',?)""",
                (f"CW-{days_ago}-{i}", dealer, asset, "Traulsen", "G12010",
                 "reach-in freezer", text, when,
                 "R-1" if i % 2 == 0 else None))


def test_a_cluster_of_recent_complaints_raises_demand(corpus):
    """The whole point of recording complaints.

    Customers notice weeks before they ring. A run of people describing the
    same early symptom is demand that has not reached the repair history yet.
    """
    from src import db, ops

    _consume(db, "P-DEFROSTTHE", 20)
    _set_stock(db, "P-DEFROSTTHE", 6)

    before = ops.restock_advice(REF)
    base = next((x for x in before["order"] if x["sku"] == "P-DEFROSTTHE"), None)

    _complain_recent(db, "frost building on the coil, temp climbing at night", 12)

    after = ops.restock_advice(REF)
    now = next((x for x in after["order"] if x["sku"] == "P-DEFROSTTHE"), None)

    assert now is not None, "a cluster of warnings produced no reorder at all"
    if base is not None:
        assert now["reorder_at"] >= base["reorder_at"]


def test_old_complaints_do_not_drive_orders(corpus):
    """The bug that produced a $4,487 order off grumbles going back years.

    A complaint from two years ago is not a warning, it is history. It has
    either already become the repair it predicted or it never will.
    """
    from src import db, ops

    _consume(db, "P-DEFROSTTHE", 20)
    _set_stock(db, "P-DEFROSTTHE", 6)

    before = ops.restock_advice(REF)
    _complain_recent(db, "frost building on the coil, temp climbing at night",
                     40, days_ago=700)
    after = ops.restock_advice(REF)

    def qty(r):
        line = next((x for x in r["order"] if x["sku"] == "P-DEFROSTTHE"), None)
        return line["order_qty"] if line else 0

    assert qty(after) == qty(before), \
        "complaints from two years ago changed what we buy today"


def test_the_signal_is_discounted_not_believed(corpus):
    """Three honest discounts, not one confident number.

    A complaint is weighted by the chance that description means this part,
    times the chance the complaint becomes a job at all, times how often the
    retrieval names the right part. Counting each grumble as a certain part
    sale is what made the first version order seven times too much.
    """
    from src import db, ops

    _consume(db, "P-DEFROSTTHE", 20)
    _set_stock(db, "P-DEFROSTTHE", 6)
    _complain_recent(db, "frost building on the coil, temp climbing at night", 20)

    r = ops.restock_advice(REF)
    line = next(x for x in r["order"] if x["sku"] == "P-DEFROSTTHE")
    warned = line.get("warned_by_complaints", 0)

    assert warned < 20, \
        f"20 complaints counted as {warned} units, i.e. taken at face value"


def test_the_complaint_signal_is_reported_separately(corpus):
    """Never folded silently into the demand history.

    It is 66% accurate, not 100%, so the owner has to be able to see how much
    of the order came from history and how much from a forecast.
    """
    from src import db, ops

    _consume(db, "P-DEFROSTTHE", 20)
    _set_stock(db, "P-DEFROSTTHE", 6)
    _complain_recent(db, "frost building on the coil, temp climbing at night", 20)

    r = ops.restock_advice(REF)
    assert "complaint_signal" in r["how_it_was_worked_out"]
    line = next(x for x in r["order"] if x["sku"] == "P-DEFROSTTHE")
    if "warned_by_complaints" in line:
        assert "complaint_note" in line
