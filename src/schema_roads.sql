-- Road legs we have already paid for.
--
-- Compute Route Matrix bills per element, where elements are origins times
-- destinations. The same engineer-to-site pair is asked for over and over as
-- jobs repeat at the same addresses, and buying it twice is buying a road
-- that has not moved.
--
-- Coordinates are rounded to 4 places (about 11 metres) so two calls about
-- the same building hit the same row.
CREATE TABLE IF NOT EXISTS road_legs (
    from_lat     REAL NOT NULL,
    from_lon     REAL NOT NULL,
    to_lat       REAL NOT NULL,
    to_lon       REAL NOT NULL,
    road_miles   REAL,
    road_minutes INTEGER,
    seen_at      TEXT NOT NULL,
    PRIMARY KEY (from_lat, from_lon, to_lat, to_lon)
);
