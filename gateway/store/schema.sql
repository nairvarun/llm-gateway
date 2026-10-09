PRAGMA journal_mode = WAL;          -- readers never block the single writer
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,               -- "key_" + 16 hex
  hash TEXT UNIQUE NOT NULL,         -- sha256 hex of the plaintext key
  name TEXT NOT NULL,
  allowed_models TEXT,               -- JSON array of aliases, NULL = all
  rpm_limit INTEGER,
  monthly_budget_usd REAL,
  created_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY,
  request_id TEXT UNIQUE NOT NULL,
  key_id TEXT NOT NULL REFERENCES api_keys(id),
  ts TEXT NOT NULL,
  model_alias TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  input_tokens INTEGER,
  output_tokens INTEGER,
  usage_estimated INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  latency_ms INTEGER,
  ttft_ms INTEGER,
  status INTEGER NOT NULL,
  cache_hit INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS usage_key_ts ON usage (key_id, ts);
PRAGMA user_version = 1;
