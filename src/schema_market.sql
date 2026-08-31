-- What machines actually sell for, from real listings.
--
-- Additive.
--
-- The price list this desk quoted from was invented: a dictionary of trade
-- costs chosen by hand, scaled by capacity, marked up by a constant. Every
-- figure a customer heard was made up and delivered in the same voice as the
-- EnergyStar efficiency data and the federal recalls sitting beside it.
--
-- Google Shopping was already wired up through Serper for review ratings, on
-- the same key, and those listings carry PRICES. A real number was one field
-- away the whole time.
--
-- Cached because a call must not wait on a search we already did, and because
-- the provider is a free tier. A MISS is
-- cached too, and expires sooner: a machine nobody listed today may well be
-- listed next week, and a cached nothing that lasts a week is a machine we
-- can never price again.
CREATE TABLE IF NOT EXISTS market_prices (
    manufacturer  TEXT NOT NULL,
    model_number  TEXT NOT NULL DEFAULT '',

    -- Null means we looked and found nothing worth quoting. Not the same as
    -- never having looked, which is no row at all.
    median_price  REAL,
    low_price     REAL,
    high_price    REAL,
    listings      INTEGER DEFAULT 0,

    -- Who was selling it and for how much, so a number can be checked rather
    -- than defended.
    sources       TEXT,
    fetched_at    TEXT NOT NULL,

    PRIMARY KEY (manufacturer, model_number)
);


-- Where a price on the shelf came from. Null on rows written before this
-- existed, which is honest: we do not know for those.
ALTER TABLE product_stock ADD COLUMN price_source TEXT;

-- The picture. Serper returns an imageUrl on every shopping result and
-- market.py kept the price, the title and the source and dropped it, so the
-- console could list 923 machines for sale and show none of them.
--
-- On product_stock rather than market_prices because it is a fact about the
-- product, not about one day's pricing, and it should survive a refresh.
ALTER TABLE product_stock ADD COLUMN image_url TEXT;
