-- KAISPOT Voucher Management v3
--
-- Money is always an INTEGER of whole Uganda Shillings. Never a float:
-- vouchers and cash have to reconcile exactly.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    full_name     TEXT    NOT NULL DEFAULT '',
    role          TEXT    NOT NULL DEFAULT 'agent',   -- admin | manager | agent
    agent_id      INTEGER REFERENCES agents(id) ON DELETE CASCADE,
    active        INTEGER NOT NULL DEFAULT 1,
    must_change   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- The sales people.
CREATE TABLE IF NOT EXISTS agents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    name            TEXT    NOT NULL,
    phone           TEXT    NOT NULL DEFAULT '',
    station         TEXT    NOT NULL DEFAULT '',
    nin             TEXT    NOT NULL DEFAULT '',
    next_of_kin     TEXT    NOT NULL DEFAULT '',
    commission_rate INTEGER NOT NULL DEFAULT 1000,  -- basis points, 1000 = 10%
    monthly_target  INTEGER NOT NULL DEFAULT 0,
    daily_target    INTEGER NOT NULL DEFAULT 0,
    credit_limit    INTEGER NOT NULL DEFAULT 0,     -- 0 = no ceiling on debt
    notes           TEXT    NOT NULL DEFAULT '',
    joined_on       TEXT    NOT NULL DEFAULT (date('now')),
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Voucher packages: the denominations on sale. Editable and addable at will.
-- The price is copied onto every voucher at intake, so changing a package
-- price later never revalues stock already in the field.
CREATE TABLE IF NOT EXISTS packages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    price      INTEGER NOT NULL CHECK (price > 0),
    validity   TEXT    NOT NULL DEFAULT '',
    prefix     TEXT    NOT NULL DEFAULT '',
    colour     TEXT    NOT NULL DEFAULT '#0b6e4f',
    sort_order INTEGER NOT NULL DEFAULT 100,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- One delivery of vouchers into the business, by either route.
--   pdf     an uploaded sheet whose codes were read and counted automatically
--   manual  numbers typed in on the Add record page for printed paper sheets
CREATE TABLE IF NOT EXISTS intakes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL,
    package_id  INTEGER NOT NULL REFERENCES packages(id) ON DELETE RESTRICT,
    reference   TEXT    NOT NULL DEFAULT '',
    filename    TEXT    NOT NULL DEFAULT '',
    stored_name TEXT    NOT NULL DEFAULT '',
    pages       INTEGER NOT NULL DEFAULT 0,
    accepted    INTEGER NOT NULL DEFAULT 0,   -- codes taken in
    duplicates  INTEGER NOT NULL DEFAULT 0,   -- codes already known, skipped
    note        TEXT    NOT NULL DEFAULT '',
    received_on TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    created_by  INTEGER REFERENCES users(id)
);

-- Every voucher, individually. The unique code is what makes it impossible
-- to take the same voucher in twice, whichever route it arrived by.
CREATE TABLE IF NOT EXISTS vouchers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id   INTEGER NOT NULL REFERENCES packages(id) ON DELETE RESTRICT,
    intake_id    INTEGER REFERENCES intakes(id) ON DELETE SET NULL,
    code         TEXT    NOT NULL COLLATE NOCASE,
    secret       TEXT    NOT NULL DEFAULT '',
    price        INTEGER NOT NULL,             -- frozen at intake
    status       TEXT    NOT NULL DEFAULT 'office',  -- office|agent|sold|dead
    agent_id     INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    assigned_on  TEXT,
    sold_on      TEXT,
    sold_at      TEXT,
    client_phone TEXT    NOT NULL DEFAULT '',
    channel      TEXT    NOT NULL DEFAULT '',  -- whatsapp|telegram|counter|sms
    sold_by      INTEGER REFERENCES users(id),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (code)
);
CREATE INDEX IF NOT EXISTS ix_vouchers_agent  ON vouchers(agent_id, status);
CREATE INDEX IF NOT EXISTS ix_vouchers_pack   ON vouchers(package_id, status);
CREATE INDEX IF NOT EXISTS ix_vouchers_soldon ON vouchers(sold_on);

-- Handing stock to an agent. The top-up column of the daily table.
CREATE TABLE IF NOT EXISTS assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    package_id  INTEGER NOT NULL REFERENCES packages(id) ON DELETE RESTRICT,
    qty         INTEGER NOT NULL,
    value       INTEGER NOT NULL,
    assigned_on TEXT    NOT NULL,
    note        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    created_by  INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS ix_assign ON assignments(agent_id, assigned_on);

-- One row per agent per day: the daily operations table.
--     closing = opening + topup - collection
--     shortage = closing - stock actually still held
CREATE TABLE IF NOT EXISTS daily_rows (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    day        TEXT    NOT NULL,
    opening    INTEGER NOT NULL DEFAULT 0,
    topup      INTEGER NOT NULL DEFAULT 0,
    sold       INTEGER NOT NULL DEFAULT 0,   -- value of vouchers marked sold
    collection INTEGER NOT NULL DEFAULT 0,   -- cash handed in
    closing    INTEGER NOT NULL DEFAULT 0,   -- opening + topup - collection
    on_hand    INTEGER NOT NULL DEFAULT 0,   -- stock actually still held
    shortage   INTEGER NOT NULL DEFAULT 0,   -- closing - on_hand
    commission INTEGER NOT NULL DEFAULT 0,
    status     TEXT    NOT NULL DEFAULT 'open',   -- open | closed
    note       TEXT    NOT NULL DEFAULT '',
    closed_at  TEXT,
    closed_by  INTEGER REFERENCES users(id),
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (agent_id, day)
);
CREATE INDEX IF NOT EXISTS ix_daily ON daily_rows(day);

-- Debt and commission in one place, so a payment moves every figure at once.
--   positive amount = the agent owes KAISPOT
--   negative amount = KAISPOT owes the agent
CREATE TABLE IF NOT EXISTS ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    entry_date TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    amount     INTEGER NOT NULL,
    ref_type   TEXT    NOT NULL DEFAULT '',
    ref_id     INTEGER,
    note       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    created_by INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS ix_ledger ON ledger(agent_id, entry_date);

-- Regular customers, so an agent can send a voucher without retyping a number.
CREATE TABLE IF NOT EXISTS clients (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   INTEGER REFERENCES agents(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    phone      TEXT    NOT NULL,
    note       TEXT    NOT NULL DEFAULT '',
    last_sold  TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_clients ON clients(agent_id);

-- PDF sheets the office puts in front of an agent to work from.
CREATE TABLE IF NOT EXISTS sheets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    agent_id    INTEGER REFERENCES agents(id) ON DELETE CASCADE,  -- null = everyone
    intake_id   INTEGER REFERENCES intakes(id) ON DELETE SET NULL,
    filename    TEXT    NOT NULL,
    stored_name TEXT    NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    uploaded_at TEXT    NOT NULL DEFAULT (datetime('now')),
    uploaded_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id),
    action     TEXT    NOT NULL,
    detail     TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
