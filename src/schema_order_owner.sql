-- Which of the four businesses actually sold this.
--
-- An order carried no company of its own: it was inferred from the account,
-- and an account belongs to whichever business the caller first rang. So on a
-- live call somebody bought an HP laptop from the IT company and it was
-- recorded as a refrigeration sale, because that is where the conversation
-- started.
--
-- Every other owned table carries dealer_id. This one did not, which made the
-- sale the one thing in the system that could not say whose it was.
ALTER TABLE purchase_orders ADD COLUMN dealer_id TEXT REFERENCES dealers(id);
CREATE INDEX IF NOT EXISTS ix_po_dealer ON purchase_orders(dealer_id);
