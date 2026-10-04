"""Explicit SQLite storage versions; game event/state semantics are unchanged."""

STORAGE_VERSION = 2

TOOL_SCHEMA = (
    """CREATE TABLE tool_events (
        world_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        idempotency_key TEXT,
        event_json TEXT NOT NULL,
        state_hash_before TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending', 'committed')),
        PRIMARY KEY (world_id, branch_id, event_id),
        UNIQUE (world_id, branch_id, idempotency_key),
        FOREIGN KEY (world_id, branch_id) REFERENCES branches(world_id, branch_id)
    )""",
    """CREATE UNIQUE INDEX one_pending_tool_event ON tool_events(world_id, branch_id)
       WHERE status = 'pending'""",
    """CREATE TABLE tool_executions (
        world_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        tool_call_key TEXT NOT NULL,
        request_json TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        arguments_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'committed')),
        result_json TEXT,
        PRIMARY KEY (world_id, branch_id, event_id, tool_call_key),
        FOREIGN KEY (world_id, branch_id) REFERENCES branches(world_id, branch_id),
        CHECK((status = 'pending' AND result_json IS NULL) OR
              (status IN ('completed', 'committed') AND result_json IS NOT NULL))
    )""",
)

BASE_SCHEMA = (
    """CREATE TABLE worlds (
        world_id TEXT PRIMARY KEY,
        initial_state_json TEXT NOT NULL,
        seed TEXT NOT NULL,
        ruleset TEXT NOT NULL,
        ruleset_version TEXT NOT NULL,
        runtime_version TEXT NOT NULL
    )""",
    """CREATE TABLE branches (
        world_id TEXT NOT NULL REFERENCES worlds(world_id),
        branch_id TEXT NOT NULL,
        parent_branch_id TEXT,
        fork_event_id TEXT,
        fork_seq INTEGER NOT NULL CHECK(fork_seq >= 0),
        PRIMARY KEY (world_id, branch_id),
        FOREIGN KEY (world_id, parent_branch_id) REFERENCES branches(world_id, branch_id)
    )""",
    """CREATE TABLE entries (
        world_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        seq INTEGER NOT NULL CHECK(seq > 0),
        event_id TEXT NOT NULL,
        idempotency_key TEXT,
        entry_json TEXT NOT NULL,
        PRIMARY KEY (world_id, branch_id, seq),
        UNIQUE (world_id, branch_id, event_id),
        UNIQUE (world_id, branch_id, idempotency_key),
        FOREIGN KEY (world_id, branch_id) REFERENCES branches(world_id, branch_id)
    )""",
    """CREATE TABLE snapshots (
        world_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        seq INTEGER NOT NULL CHECK(seq >= 0),
        state_json TEXT NOT NULL,
        state_hash TEXT NOT NULL,
        PRIMARY KEY (world_id, branch_id, seq),
        FOREIGN KEY (world_id, branch_id) REFERENCES branches(world_id, branch_id)
    )""",
)
