-- Vectors for phrases, so a family name is embedded once and not once a call.
--
-- Additive.
--
-- A family name does not change what it means, so its vector can be kept
-- forever. The model is stored alongside because a different embedding model
-- produces vectors that are not comparable with these, and silently mixing
-- two of them would make every distance meaningless.

CREATE TABLE IF NOT EXISTS meanings (
    phrase   TEXT PRIMARY KEY,
    model    TEXT NOT NULL,
    vector   TEXT NOT NULL,     -- json array of floats
    made_on  TEXT
);
