-- Lexis, server side.
--
-- The shape here is the shape the app already has: cards keyed by their own id
-- and carrying the time they were last changed, reviews keyed by the instant
-- they happened. The merge that matters is the same merge the app does between
-- two phones, which is deliberate — a server that merged differently would be a
-- third opinion about what your deck says.

CREATE TABLE IF NOT EXISTS users (
  id          TEXT PRIMARY KEY,
  email       TEXT NOT NULL UNIQUE,
  pw_hash     TEXT NOT NULL,          -- base64 of PBKDF2-HMAC-SHA256
  pw_salt     TEXT NOT NULL,          -- base64, 16 random bytes, per user
  iterations  INTEGER NOT NULL,       -- recorded so the cost can be raised later
  created     INTEGER NOT NULL
);

-- Sessions are opaque random tokens, and only their hash is stored: a dump of
-- this table does not let anyone log in as anybody.
CREATE TABLE IF NOT EXISTS sessions (
  token_hash  TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  created     INTEGER NOT NULL,
  expires     INTEGER NOT NULL,
  device      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions (user_id);

CREATE TABLE IF NOT EXISTS cards (
  user_id     TEXT NOT NULL,
  id          TEXT NOT NULL,
  mod         INTEGER NOT NULL,
  json        TEXT NOT NULL,
  PRIMARY KEY (user_id, id)
);
-- "Everything changed since I last looked" is the only query sync makes.
CREATE INDEX IF NOT EXISTS ix_cards_mod ON cards (user_id, mod);

CREATE TABLE IF NOT EXISTS reviews (
  user_id     TEXT NOT NULL,
  at          TEXT NOT NULL,
  undone      INTEGER NOT NULL DEFAULT 0,
  json        TEXT NOT NULL,
  PRIMARY KEY (user_id, at)
);
CREATE INDEX IF NOT EXISTS ix_reviews_at ON reviews (user_id, at);

-- Adding a second phone without retyping a password on a phone keyboard.
CREATE TABLE IF NOT EXISTS pairings (
  code        TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  expires     INTEGER NOT NULL
);

-- Somewhere to count failed logins. Keyed by email and by address separately,
-- so one person under attack cannot lock out everyone else on their network.
CREATE TABLE IF NOT EXISTS attempts (
  key         TEXT PRIMARY KEY,
  n           INTEGER NOT NULL,
  until       INTEGER NOT NULL
);

-- One opaque blob per person, for the Add to Anki page: a GitHub token, an
-- Anthropic key, and which repository and voice to use. It is encrypted in the
-- browser with a key derived from the password, and the password never comes
-- here in a form this can reverse, so what is stored is genuinely unreadable
-- from this side. That is the point: a token that can push to somebody's
-- repository should not be sitting in a database in plain text, and the only
-- way to promise that is not to be able to read it.
CREATE TABLE IF NOT EXISTS vault (
  user_id     TEXT PRIMARY KEY,
  blob        TEXT NOT NULL,
  mod         INTEGER NOT NULL
);
