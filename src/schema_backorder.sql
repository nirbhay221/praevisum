-- A machine somebody has bought that we do not hold.
--
-- Additive.
--
-- WHAT WAS MISSING
--
-- confirm_purchase_order set the status to 'confirmed' and stopped. It never
-- asked whether we actually had the machine, never ordered one, and never
-- told the customer the wait was because we were sourcing it. So an order
-- could be confirmed for a freezer nobody owned and nothing anywhere would
-- buy one. The customer waits for a machine that was never ordered.
--
-- The schema already anticipated whole machines: purchase_orders and
-- supply_orders both carry equipment_id. Only the link between them was
-- absent.
--
-- BACK-TO-BACK, WHICH IS WHAT DISTRIBUTORS ACTUALLY CALL THIS
--
-- A purchase order raised on a supplier on the back of a customer's order,
-- hard pegged to it. The trade practice is specific and the details matter:
--
--   ONE SUPPLY ORDER IS TIED TO ONE CUSTOMER LINE. Not pooled.
--
--   WHAT ARRIVES IS RESERVED. The whole point of the peg is that when the
--   machine reaches the warehouse it is not "inadvertently taken by another
--   order or demand". Receiving a back-to-back order onto general stock,
--   which is what receive() does today, defeats it entirely.
--
--   IT CARRIES THE CUSTOMER REFERENCE AND THE REQUIRED DATE, so a buyer
--   chasing a supplier knows who is waiting and until when.
--
-- WHY THE KIND MATTERS
--
-- Replenishment and a customer waiting are different orders that happen to
-- live in the same table, and a buyer looking at the list could not tell them
-- apart. One is "the shelf is getting low"; the other has a restaurant behind
-- it with a dinner service.

PRAGMA foreign_keys = ON;

-- Why this order exists. Replenishment is the periodic review restock_advice
-- produces. Back-to-back has a named customer waiting on it. Emergency is a
-- machine down now, which is the only one that justifies paying for freight.
ALTER TABLE supply_orders ADD COLUMN kind TEXT DEFAULT 'replenishment';

-- The customer order this is pegged to, and the exact line. Null on a
-- replenishment order, which belongs to nobody in particular.
ALTER TABLE supply_orders ADD COLUMN for_purchase_order TEXT;
ALTER TABLE supply_orders ADD COLUMN for_line INTEGER;

-- What the customer was told, so a buyer chasing a supplier knows what has
-- already been promised on their behalf.
ALTER TABLE supply_orders ADD COLUMN promised_by TEXT;

-- Whether what arrives is spoken for. A back-to-back delivery must not be
-- absorbed into general stock and sold to somebody else.
ALTER TABLE supply_orders ADD COLUMN reserved INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS ix_supply_orders_kind ON supply_orders(kind, status);
CREATE INDEX IF NOT EXISTS ix_supply_orders_peg ON supply_orders(for_purchase_order);


-- What a customer order line is waiting on, so the desk can answer "where is
-- my freezer" without a buyer reading a spreadsheet.
ALTER TABLE purchase_lines ADD COLUMN sourced_by TEXT;
