-- Addresses turned into points, remembered.
--
-- Additive. `next_available_slot` orders technicians by drive time and refuses
-- a site with no coordinates. Every seeded site has them because the seed
-- wrote them; every site created by a real phone call had none, because
-- confirm_details stored the address as text and nothing turned it into a
-- point. So no first-time caller could ever be given an appointment, and it
-- took a real call to find out.
--
-- Cached because OpenStreetMap's usage policy asks for one request a second
-- and because the same restaurant's address should be looked up once ever.
-- A miss is cached too, with a null point: an address that does not resolve
-- will not resolve on the fourth attempt either, and re-asking a public
-- service the same unanswerable question is what gets an application blocked.
-- A network FAILURE is deliberately not cached: that is a bad minute, not a
-- bad address, and caching it would make a customer permanently unbookable.
CREATE TABLE IF NOT EXISTS geocodes (
    query        TEXT PRIMARY KEY,     -- lowercased, as sent
    lat          REAL,                 -- null where it did not resolve
    lon          REAL,
    note         TEXT,                 -- what it matched, or why it did not
    looked_up_at TEXT NOT NULL
);
