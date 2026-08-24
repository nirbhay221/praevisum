-- Which chat belongs to which person, on channels that do not carry a number.
--
-- Additive. WhatsApp and SMS both arrive with a phone number, so a technician
-- is recognised as themselves without anything here. Telegram gives a chat id
-- and nothing else, so a technician replying there cannot close a job until
-- somebody has said which number that chat belongs to.
--
-- The alternative was matching on a display name, and it loses badly: a wrong
-- match writes a repair against another technician's visit, silently, into the
-- corpus that every future briefing is built from. A link somebody actually
-- made is the only evidence worth trusting here.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS channel_links (
    channel    TEXT NOT NULL,          -- 'telegram'
    handle     TEXT NOT NULL,          -- chat id, as that channel gives it
    phone      TEXT NOT NULL,          -- E.164, as the rest of the system stores it
    linked_at  TEXT NOT NULL DEFAULT (datetime('now')),

    PRIMARY KEY (channel, handle)
);

CREATE INDEX IF NOT EXISTS ix_channel_links_phone ON channel_links(phone);
