-- Us buying, rather than a customer buying from us.
--
-- Additive.
--
-- THE DIRECTION THAT WAS MISSING
--
-- `purchase_orders` is the customer's side: `account_id` is who is buying,
-- `from_call` is the call they rang in on. There was no table anywhere for
-- this dealer ordering from a supplier, so `restock_advice` worked out that
-- four defrost heaters were needed, priced a stockout at a truck roll, and
-- then handed the doing to a person with no record of whether it happened.
--
-- The consequence is that "we knew and did not order" and "we ordered and it
-- is late" looked identical from inside the system, and they are completely
-- different problems.
--
-- WHY THE RECOMMENDATION IS STORED ALONGSIDE THE ORDER
--
-- `advised_qty` is what the arithmetic said. `qty` is what somebody actually
-- ordered. Keeping both is what makes the advice checkable later: a buyer who
-- consistently halves the recommendation is either wiser than the model or
-- costing the company truck rolls, and there is no way to tell which without
-- the two numbers side by side.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS supply_orders (
    id           TEXT PRIMARY KEY,
    dealer_id    TEXT REFERENCES dealers(id),
    supplier_id  TEXT REFERENCES suppliers(id),

    sku          TEXT REFERENCES parts(sku),
    equipment_id INTEGER REFERENCES equipment(id),   -- a whole machine

    advised_qty  INTEGER,        -- what restock_advice said
    qty          INTEGER NOT NULL,
    unit_cost    REAL,

    -- Why it was ordered, in the same money the van loading uses, so a buyer
    -- can disagree with the arithmetic rather than with a hunch.
    reason       TEXT,
    stockout_cost REAL,

    status       TEXT NOT NULL DEFAULT 'placed'
                 CHECK (status IN ('placed','acknowledged','shipped',
                                   'received','cancelled')),
    placed_at    TEXT NOT NULL,
    expected_at  TEXT,
    received_at  TEXT,
    note         TEXT
);

CREATE INDEX IF NOT EXISTS ix_supply_orders_open
    ON supply_orders(dealer_id, status, expected_at);
CREATE INDEX IF NOT EXISTS ix_supply_orders_sku ON supply_orders(sku);


-- Whole machines on the shelf, as against parts.
--
-- `stock` holds parts. `restock_advice` reads `parts` and nothing else. So the
-- desk would recommend a Traulsen over a Beverage-Air, weigh their running
-- costs from federal data, quote the delivery, and have no idea whether one
-- was in the building.
--
-- That is a gap a CUSTOMER can feel, which makes it worse than an internal
-- one: the hard rule is that the desk never says something is available
-- unless a tool said so, and for machines no tool could say anything.
--
-- Separate from `stock` on purpose. A part and a machine are stocked for
-- opposite reasons. Parts are held because a missing one fails a service call,
-- so availability beats cost efficiency. A machine is held at real capital
-- cost against a sale that may not come, so cost efficiency beats availability.
-- One table with one policy would force the wrong answer on one of them.
CREATE TABLE IF NOT EXISTS product_stock (
    dealer_id    TEXT NOT NULL REFERENCES dealers(id),
    manufacturer TEXT NOT NULL,
    model_number TEXT NOT NULL,
    family       TEXT,

    on_hand      INTEGER NOT NULL DEFAULT 0,
    on_order     INTEGER NOT NULL DEFAULT 0,
    unit_cost    REAL,
    list_price   REAL,
    lead_time_days INTEGER DEFAULT 0,
    supplier_id  TEXT REFERENCES suppliers(id),

    updated_at   TEXT,

    PRIMARY KEY (dealer_id, manufacturer, model_number)
);

CREATE INDEX IF NOT EXISTS ix_product_stock_family
    ON product_stock(dealer_id, family);
