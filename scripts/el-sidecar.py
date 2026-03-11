"""el-sidecar — Plugin-aware event hub for Claude Code agents.

Version: 1.1.2

Source-agnostic event hub with self-registering plugin system. Plugins declare
themselves via sidecar/plugin.py with a register(api) function. The sidecar
discovers installed plugins from the Claude Code plugin manifest and loads them
automatically.

Features:
    - SQLite event storage with plugin-driven enrichment
    - Long-poll drain (GET /events?wait=true — blocks forever, never returns [])
    - Response routing (POST /respond, GET /responses?wait=true)
    - Colony ledger (POST /ledger, GET /ledger?wait=true)
    - Health endpoint (GET /health)
    - Plugin-registered webhook routes (POST /slack, etc.)
    - Plugin-registered background pollers
    - Runtime watch API for PR-scoped plugins (POST /watch, GET /watches)
    - Runtime Source API (POST /source, DELETE /source, GET /sources)
    - Dynamic event sources: poll, heartbeat, command, watch, tail, ci, webhook
    - Auto-port (port 0) with metadata written to .claude/sidecar.json
    - Per-project DB isolation (/tmp/el-sidecar-{hash}.db)
    - Plugin discovery from ~/.claude/plugins/installed_plugins.json

Usage: python3 el-sidecar.py [port] [--project-root PATH]

Env vars:
    SIDECAR_PORT           — Port (default: 0 = auto-assign)
    SIDECAR_DB_PATH        — SQLite path override (default: auto per-project)
    SIDECAR_PROJECT_ROOT   — Project root for metadata (default: cwd)
"""

__version__ = '1.1.2'

import hashlib
import heapq
import importlib.util
import json
import os
import select
import socket
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import unquote


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _parse_args():
    """Parse CLI args: el-sidecar.py [port] [--project-root PATH]"""
    port = 0
    project_root = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--project-root' and i + 1 < len(args):
            project_root = args[i + 1]
            i += 2
        elif args[i].isdigit():
            port = int(args[i])
            i += 1
        else:
            i += 1

    project_root = project_root or os.environ.get('SIDECAR_PROJECT_ROOT', os.getcwd())
    if not port:
        env_port = os.environ.get('SIDECAR_PORT', '')
        if env_port:
            port = int(env_port)
        else:
            # Deterministic port from project root hash (ephemeral range 49152-65535)
            h = hashlib.sha256(os.path.abspath(project_root).encode()).hexdigest()
            port = 49152 + (int(h[:8], 16) % (65535 - 49152))

    return port, project_root


_requested_port, _project_root = _parse_args()


def _db_path_for_project(project_root):
    """Deterministic DB path based on project root hash."""
    override = os.environ.get('SIDECAR_DB_PATH')
    if override:
        return override
    h = hashlib.sha256(os.path.abspath(project_root).encode()).hexdigest()[:12]
    return f'/tmp/el-sidecar-{h}.db'


DB_PATH = _db_path_for_project(_project_root)

# ---------------------------------------------------------------------------
# Plugin registry
# ---------------------------------------------------------------------------

_plugin_routes = {}       # {('POST', '/slack'): handler_func, ...}
_plugin_pollers = []      # [(name, func), ...]
_plugin_inits = []        # [(name, func), ...]
_plugin_on_pick = []      # [(name, func), ...] — called after events are picked
_plugin_enrichments = []  # [(name, func), ...] — called during insert_event
_plugin_watch_handlers = {}  # {plugin_name: {'add': func, 'remove': func}, ...}
_loaded_plugins = []      # [(name, path), ...] — for /health reporting


def _register_route(method, path, handler):
    _plugin_routes[(method.upper(), path)] = handler
    sys.stderr.write(f"[sidecar] Registered route: {method.upper()} {path}\n")


def _register_poller(name, func):
    _plugin_pollers.append((name, func))


def _register_init(name, func):
    _plugin_inits.append((name, func))


def _register_on_pick(name, func):
    _plugin_on_pick.append((name, func))


def _register_enrichment(name, func):
    _plugin_enrichments.append((name, func))
    sys.stderr.write(f"[sidecar] Registered enrichment: {name}\n")


def _register_watch_handler(plugin_name, add_func, remove_func):
    _plugin_watch_handlers[plugin_name] = {'add': add_func, 'remove': remove_func}
    sys.stderr.write(f"[sidecar] Registered watch handler: {plugin_name}\n")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

# RLock: reentrant so on_pick callbacks can safely call insert_event()
_db_lock = threading.RLock()

# DDL runs once — skip on subsequent _get_db() calls to reduce _db_lock
# hold time. Without this, every operation creates a connection and runs
# 4x CREATE TABLE IF NOT EXISTS, inflating lock contention.
_db_initialized = False


def _get_db():
    """Create a new DB connection for the calling thread.
    DDL runs once globally, not on every call."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    global _db_initialized
    if not _db_initialized:
        _create_tables(conn)
        _db_initialized = True
    return conn


def _create_tables(conn):
    """Create core tables. Called once during first _get_db()."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,
            ts          TEXT,
            user_id     TEXT,
            text        TEXT,
            channel     TEXT,
            type        TEXT,
            thread_ts   TEXT,
            bot_id      TEXT,
            metadata    TEXT,
            associations TEXT,
            picked_up   INTEGER DEFAULT 0,
            received_at REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS responses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    INTEGER,
            source      TEXT NOT NULL,
            text        TEXT NOT NULL,
            picked_up   INTEGER DEFAULT 0,
            created_at  REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ledger (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id    TEXT NOT NULL,
            did         TEXT,
            entry_type  TEXT NOT NULL DEFAULT 'memory',
            content     TEXT NOT NULL,
            tags        TEXT,
            signature   TEXT,
            created_at  REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watches (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            plugin      TEXT NOT NULL,
            url         TEXT NOT NULL,
            created_at  REAL NOT NULL,
            UNIQUE(plugin, url)
        )
    """)
    conn.commit()


def _init_db():
    """Initialize the database and run migrations."""
    conn = _get_db()
    # Migrate: add associations column if missing
    try:
        conn.execute("SELECT associations FROM events LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE events ADD COLUMN associations TEXT")
        conn.commit()
        sys.stderr.write("[sidecar] Migrated events table: added associations column\n")
    conn.close()


def _ensure_indexes():
    """Create indexes for efficient queries."""
    with _db_lock:
        conn = _get_db()
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_source_ts "
                "ON events(source, ts) WHERE ts IS NOT NULL AND ts != ''"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Generic event insertion (used by plugins)
# ---------------------------------------------------------------------------

def insert_event(source, ts='', user_id='', text='', channel='',
                 type='', thread_ts='', bot_id='', metadata=None):
    """Insert an event from any source. Runs enrichment hooks. Returns True if inserted."""
    # Run registered enrichment hooks
    enrichments = {}
    for name, enrich_func in _plugin_enrichments:
        try:
            result = enrich_func(text, source=source)
            if result is not None:
                enrichments[name] = result
        except Exception as e:
            sys.stderr.write(f"[sidecar] enrichment '{name}' error: {e}\n")

    associations_json = enrichments.get('associations')

    with _db_lock:
        conn = _get_db()
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO events "
                "(source, ts, user_id, text, channel, type, thread_ts, bot_id, "
                "metadata, associations, picked_up, received_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (source, ts, user_id, text, channel, type, thread_ts, bot_id,
                 json.dumps(metadata) if metadata else None,
                 associations_json, time.time()),
            )
            inserted = cursor.rowcount > 0
            conn.commit()
            return inserted
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Legacy raw event insertion (for backward compat with simple POST /<source>)
# ---------------------------------------------------------------------------

def _insert_raw_event(source, headers, body):
    """Insert a raw HTTP event (headers + body). Returns the new row id."""
    with _db_lock:
        conn = _get_db()
        try:
            cursor = conn.execute(
                "INSERT INTO events (source, text, metadata, picked_up, received_at) "
                "VALUES (?, ?, ?, 0, ?)",
                (source, body, json.dumps(headers) if headers else None, time.time()),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Event retrieval
# ---------------------------------------------------------------------------

def _pick_events(source=None, sender_exclude=None, return_ids=False):
    """Return all unpicked events, mark them picked up.

    Filters:
        source         — exact match on source field
        sender_exclude — exclude events where metadata.sender matches

    If return_ids=True, returns (events, picked_ids) tuple for rollback support.
    """
    with _db_lock:
        conn = _get_db()
        try:
            if source:
                rows = conn.execute(
                    "SELECT id, source, ts, user_id, text, channel, type, thread_ts, bot_id, "
                    "associations, metadata, received_at "
                    "FROM events WHERE picked_up = 0 AND source = ? "
                    "ORDER BY received_at ASC, id ASC",
                    (source,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, source, ts, user_id, text, channel, type, thread_ts, bot_id, "
                    "associations, metadata, received_at "
                    "FROM events WHERE picked_up = 0 "
                    "ORDER BY received_at ASC, id ASC"
                ).fetchall()
            if not rows:
                return []
            events = []
            pick_ids = []
            for row in rows:
                (eid, src, ts, user_id, text, channel, etype,
                 thread_ts, bot_id, assoc_json, meta_json, received_at) = row
                evt = {'source': src}
                if ts:
                    evt['ts'] = ts
                if user_id:
                    evt['user'] = user_id
                if text:
                    evt['text'] = text
                if channel:
                    evt['channel'] = channel
                if etype:
                    evt['type'] = etype
                if thread_ts:
                    evt['thread_ts'] = thread_ts
                if bot_id:
                    evt['bot_id'] = bot_id
                if not ts:
                    evt['id'] = eid
                    evt['created_at'] = received_at
                if assoc_json:
                    try:
                        evt['associations'] = json.loads(assoc_json)
                    except (json.JSONDecodeError, ValueError):
                        pass
                if meta_json:
                    try:
                        evt['metadata'] = json.loads(meta_json)
                    except (json.JSONDecodeError, ValueError):
                        pass
                # Filter out events from excluded sender
                if sender_exclude and evt.get('metadata', {}).get('sender') == sender_exclude:
                    pick_ids.append(eid)  # still mark picked so it doesn't reappear
                    continue
                events.append(evt)
                pick_ids.append(eid)
            # Mark picked up
            if pick_ids:
                placeholders = ','.join('?' for _ in pick_ids)
                conn.execute(
                    f"UPDATE events SET picked_up = 1 WHERE id IN ({placeholders})",
                    pick_ids,
                )
            conn.commit()
        finally:
            conn.close()
    # on_pick callbacks run OUTSIDE _db_lock — safe for callbacks that
    # need DB access (e.g., insert_event). Previously ran inside the lock,
    # which was a latent deadlock with non-reentrant Lock.
    if events:
        for name, callback in _plugin_on_pick:
            try:
                callback(events)
            except Exception as e:
                sys.stderr.write(f"[sidecar] on_pick callback '{name}' error: {e}\n")
    if return_ids:
        return events, pick_ids
    return events


def _peek_events(source=None, sender_exclude=None):
    """Return unpicked events WITHOUT marking them picked. Returns (events, all_ids).

    all_ids includes both delivered and filtered event IDs (filtered ones should
    still be marked picked to avoid reprocessing).
    """
    with _db_lock:
        conn = _get_db()
        try:
            if source:
                rows = conn.execute(
                    "SELECT id, source, ts, user_id, text, channel, type, thread_ts, bot_id, "
                    "associations, metadata, received_at "
                    "FROM events WHERE picked_up = 0 AND source = ? "
                    "ORDER BY received_at ASC, id ASC",
                    (source,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, source, ts, user_id, text, channel, type, thread_ts, bot_id, "
                    "associations, metadata, received_at "
                    "FROM events WHERE picked_up = 0 "
                    "ORDER BY received_at ASC, id ASC"
                ).fetchall()
            if not rows:
                return [], []
            events = []
            all_ids = []
            for row in rows:
                (eid, src, ts, user_id, text, channel, etype,
                 thread_ts, bot_id, assoc_json, meta_json, received_at) = row
                evt = {'source': src}
                if ts:
                    evt['ts'] = ts
                if user_id:
                    evt['user'] = user_id
                if text:
                    evt['text'] = text
                if channel:
                    evt['channel'] = channel
                if etype:
                    evt['type'] = etype
                if thread_ts:
                    evt['thread_ts'] = thread_ts
                if bot_id:
                    evt['bot_id'] = bot_id
                if not ts:
                    evt['id'] = eid
                    evt['created_at'] = received_at
                if assoc_json:
                    try:
                        evt['associations'] = json.loads(assoc_json)
                    except (json.JSONDecodeError, ValueError):
                        pass
                if meta_json:
                    try:
                        evt['metadata'] = json.loads(meta_json)
                    except (json.JSONDecodeError, ValueError):
                        pass
                all_ids.append(eid)
                # Filter out events from excluded sender
                if sender_exclude and evt.get('metadata', {}).get('sender') == sender_exclude:
                    continue
                events.append(evt)
        finally:
            conn.close()
    return events, all_ids


def _mark_events_picked(event_ids):
    """Mark specific event IDs as picked up."""
    if not event_ids:
        return
    with _db_lock:
        conn = _get_db()
        try:
            placeholders = ','.join('?' for _ in event_ids)
            conn.execute(
                f"UPDATE events SET picked_up = 1 WHERE id IN ({placeholders})",
                event_ids,
            )
            conn.commit()
        finally:
            conn.close()
    # Notify on_pick callbacks
    # (events list not available here — skip callbacks for peek/commit path)


def _unpick_events(event_ids):
    """Restore events to unpicked state (e.g. after failed send to dead client)."""
    ids = [eid for eid in event_ids if isinstance(eid, int)]
    if not ids:
        return
    with _db_lock:
        conn = _get_db()
        try:
            placeholders = ','.join('?' for _ in ids)
            conn.execute(
                f"UPDATE events SET picked_up = 0 WHERE id IN ({placeholders})",
                ids,
            )
            conn.commit()
        finally:
            conn.close()
    # Wake any waiting drains so they can pick up the restored events
    with _event_cond:
        _event_cond.notify_all()


def _pending_count(source=None):
    """Count unpicked events, optionally filtered by source."""
    with _db_lock:
        conn = _get_db()
        try:
            if source:
                row = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE picked_up = 0 AND source = ?",
                    (source,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE picked_up = 0"
                ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()


def _pending_counts():
    """Return a dict of pending counts per source."""
    with _db_lock:
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT source, COUNT(*) FROM events WHERE picked_up = 0 GROUP BY source"
            ).fetchall()
            counts = {row[0]: row[1] for row in rows}
            total = sum(counts.values())
            counts['total'] = total
            return counts
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Response storage and retrieval
# ---------------------------------------------------------------------------

def _insert_response(event_id, text, source='voice'):
    """Insert a response for pickup by the appropriate client."""
    with _db_lock:
        conn = _get_db()
        try:
            conn.execute(
                "INSERT INTO responses (event_id, source, text, picked_up, created_at) "
                "VALUES (?, ?, ?, 0, ?)",
                (event_id, source, text, time.time()),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def _pick_responses(source=None):
    """Return unpicked responses, optionally filtered by source."""
    with _db_lock:
        conn = _get_db()
        try:
            if source:
                rows = conn.execute(
                    "SELECT id, event_id, source, text, created_at "
                    "FROM responses WHERE picked_up = 0 AND source = ? ORDER BY id",
                    (source,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, event_id, source, text, created_at "
                    "FROM responses WHERE picked_up = 0 ORDER BY id"
                ).fetchall()
            if rows:
                ids = [r[0] for r in rows]
                conn.execute(
                    f"UPDATE responses SET picked_up = 1 "
                    f"WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )
                conn.commit()
            return [
                {"id": r[0], "event_id": r[1], "source": r[2], "text": r[3], "created_at": r[4]}
                for r in rows
            ]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Ledger storage and retrieval
# ---------------------------------------------------------------------------

def _insert_ledger_entry(agent_id, content, entry_type='memory', did=None, tags=None, signature=None):
    """Insert a new entry into the shared ledger. Returns the entry ID."""
    with _db_lock:
        conn = _get_db()
        try:
            cursor = conn.execute(
                "INSERT INTO ledger (agent_id, did, entry_type, content, tags, signature, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (agent_id, did, entry_type, content, tags, signature, time.time()),
            )
            entry_id = cursor.lastrowid
            conn.commit()
            return entry_id
        finally:
            conn.close()


def _query_ledger(since_id=0, agent_id=None, entry_type=None, tag=None, limit=100):
    """Query ledger entries after since_id, with optional filters."""
    with _db_lock:
        conn = _get_db()
        try:
            conditions = ["id > ?"]
            params = [since_id]

            if agent_id:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if entry_type:
                conditions.append("entry_type = ?")
                params.append(entry_type)
            if tag:
                conditions.append("(',' || tags || ',') LIKE ?")
                params.append(f'%,{tag},%')

            where = " AND ".join(conditions)
            params.append(limit)

            rows = conn.execute(
                f"SELECT id, agent_id, did, entry_type, content, tags, signature, created_at "
                f"FROM ledger WHERE {where} ORDER BY id ASC LIMIT ?",
                params,
            ).fetchall()

            return [
                {
                    "id": r[0], "agent_id": r[1], "did": r[2], "entry_type": r[3],
                    "content": r[4], "tags": r[5], "signature": r[6], "created_at": r[7],
                }
                for r in rows
            ]
        finally:
            conn.close()


def _ledger_max_id():
    """Return the current max ledger ID."""
    with _db_lock:
        conn = _get_db()
        try:
            row = conn.execute("SELECT MAX(id) FROM ledger").fetchone()
            return row[0] or 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Watch management
# ---------------------------------------------------------------------------

def _add_watch(plugin, url):
    """Add a watch for a plugin. Returns True if newly added."""
    with _db_lock:
        conn = _get_db()
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO watches (plugin, url, created_at) VALUES (?, ?, ?)",
                (plugin, url, time.time()),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


def _remove_watch(plugin, url):
    """Remove a watch. Returns True if removed."""
    with _db_lock:
        conn = _get_db()
        try:
            cursor = conn.execute(
                "DELETE FROM watches WHERE plugin = ? AND url = ?",
                (plugin, url),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


def _list_watches(plugin=None):
    """List active watches, optionally filtered by plugin."""
    with _db_lock:
        conn = _get_db()
        try:
            if plugin:
                rows = conn.execute(
                    "SELECT plugin, url, created_at FROM watches WHERE plugin = ? ORDER BY id",
                    (plugin,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT plugin, url, created_at FROM watches ORDER BY plugin, id"
                ).fetchall()
            return [{"plugin": r[0], "url": r[1], "created_at": r[2]} for r in rows]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Long-poll notification
# ---------------------------------------------------------------------------

# Condition variables for long-poll notification.
# Using Condition (not Event) to prevent thundering herd: Event.set() wakes
# ALL waiting threads, causing them all to contend for _db_lock. Condition
# .notify_all() also wakes all, but the Condition's internal lock serializes
# the wait/notify race that Event has (set between clear and wait = lost signal).
_event_cond = threading.Condition()
_response_cond = threading.Condition()
_ledger_cond = threading.Condition()


def _notify_event_waiters():
    with _event_cond:
        _event_cond.notify_all()


def _notify_response_waiters():
    with _response_cond:
        _response_cond.notify_all()


def _notify_ledger_waiters():
    with _ledger_cond:
        _ledger_cond.notify_all()


# ---------------------------------------------------------------------------
# Runtime Sources — agent-registered event sources via HTTP API
# ---------------------------------------------------------------------------

_runtime_sources = {}           # {name: source_dict}
_runtime_sources_lock = threading.Lock()
_scheduler_heap = []            # [(next_fire_time, source_name, generation)]
_source_generation = {}         # {source_name: int} — incremented on each start
_scheduler_event = threading.Event()


def _init_sources_table():
    """Create the sources table if it doesn't exist."""
    with _db_lock:
        conn = _get_db()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sources (
                    name        TEXT PRIMARY KEY,
                    type        TEXT NOT NULL,
                    config      TEXT NOT NULL,
                    last_output TEXT,
                    active      INTEGER DEFAULT 1,
                    created_at  REAL
                )
            """)
            conn.commit()
        finally:
            conn.close()


def _save_source_to_db(name, stype, config, last_output=None, active=True):
    """Insert or replace a source in the DB."""
    with _db_lock:
        conn = _get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO sources (name, type, config, last_output, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, stype, json.dumps(config), last_output, 1 if active else 0, time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def _update_source_output_db(name, output):
    """Update last_output for a source."""
    with _db_lock:
        conn = _get_db()
        try:
            conn.execute("UPDATE sources SET last_output = ? WHERE name = ?", (output, name))
            conn.commit()
        finally:
            conn.close()


def _deactivate_source_db(name):
    """Mark a source as inactive in the DB."""
    with _db_lock:
        conn = _get_db()
        try:
            conn.execute("UPDATE sources SET active = 0 WHERE name = ?", (name,))
            conn.commit()
        finally:
            conn.close()


def _delete_source_db(name):
    """Remove a source from the DB."""
    with _db_lock:
        conn = _get_db()
        try:
            conn.execute("DELETE FROM sources WHERE name = ?", (name,))
            conn.commit()
        finally:
            conn.close()


def _load_sources_from_db():
    """Load all active sources from the DB."""
    with _db_lock:
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT name, type, config, last_output, active FROM sources WHERE active = 1"
            ).fetchall()
            return [
                {
                    'name': r[0], 'type': r[1],
                    'config': json.loads(r[2]),
                    'last_output': r[3], 'active': bool(r[4]),
                }
                for r in rows
            ]
        finally:
            conn.close()


# --- Scheduler for interval-based sources (poll, heartbeat) ---

def _scheduler_loop():
    """Single scheduler thread manages all interval-based sources.

    Uses a min-heap for O(log n) per tick. Wakes when new sources are added.
    """
    while True:
        next_delay = None

        with _runtime_sources_lock:
            # Fire all expired items
            while _scheduler_heap and _scheduler_heap[0][0] <= time.time():
                _, name, gen = heapq.heappop(_scheduler_heap)
                # Skip stale entries from previous generations
                if gen != _source_generation.get(name):
                    continue
                source = _runtime_sources.get(name)
                if source and source['active']:
                    threading.Thread(
                        target=_run_source_tick, args=(source,),
                        daemon=True, name=f"tick-{name}"
                    ).start()
                    interval = source['config'].get('interval', 30)
                    heapq.heappush(_scheduler_heap, (time.time() + interval, name, gen))

            if _scheduler_heap:
                next_delay = max(0.1, _scheduler_heap[0][0] - time.time())

        if next_delay is not None:
            _scheduler_event.wait(timeout=next_delay)
        else:
            _scheduler_event.wait()
        _scheduler_event.clear()


def _run_source_tick(source):
    """Execute a single tick for an interval source."""
    stype = source['type']
    name = source['name']

    if stype == 'poll':
        _run_poll_tick(source)
    elif stype == 'heartbeat':
        insert_event(
            source=f"runtime:{name}",
            type='heartbeat',
            text='tick',
            metadata={'source_type': 'heartbeat', 'name': name}
        )
        _notify_event_waiters()


def _run_poll_tick(source):
    """Run poll command and fire event if output changed."""
    name = source['name']
    config = source['config']
    command = config['command']

    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = result.stdout.strip()
    except subprocess.TimeoutExpired:
        output = 'ERROR: command timed out'
    except Exception as e:
        output = f'ERROR: {e}'

    if config.get('diff', True):
        if output == source.get('last_output'):
            return  # No change

    source['last_output'] = output
    _update_source_output_db(name, output)

    insert_event(
        source=f"runtime:{name}",
        type='poll_changed',
        text=output,
        metadata={'source_type': 'poll', 'name': name}
    )
    _notify_event_waiters()


# --- Blocking source runners (command, watch, tail, ci) ---

def _start_blocking_source(source):
    """Start a blocking source in its own daemon thread."""
    runners = {
        'command': _run_command_source,
        'watch': _run_watch_source,
        'tail': _run_tail_source,
        'ci': _run_ci_source,
    }
    runner = runners.get(source['type'])
    if runner:
        t = threading.Thread(
            target=runner, args=(source,),
            daemon=True, name=f"source-{source['name']}"
        )
        source['thread'] = t
        t.start()


def _run_command_source(source):
    """Run a blocking command and fire event when it exits."""
    name = source['name']
    command = source['config']['command']

    try:
        proc = subprocess.Popen(
            command, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        source['process'] = proc
        output, _ = proc.communicate()
        output = (output or '').strip()
        exit_code = proc.returncode
    except Exception as e:
        output = f'ERROR: {e}'
        exit_code = -1

    if not source['active']:
        return

    insert_event(
        source=f"runtime:{name}",
        type='command_completed',
        text=output,
        metadata={'source_type': 'command', 'name': name, 'exit_code': exit_code}
    )
    _notify_event_waiters()
    source['active'] = False
    _deactivate_source_db(name)


def _run_watch_source(source):
    """Watch files for changes. Re-arms automatically for continuous monitoring."""
    name = source['name']
    config = source['config']
    paths = config['paths']
    root = config.get('root', _project_root)

    while source['active']:
        try:
            output = _watch_files_once(paths, root, source)
        except Exception as e:
            if source['active']:
                sys.stderr.write(f"[sidecar] watch source '{name}' error: {e}\n")
                time.sleep(5)
            continue

        if not source['active']:
            return

        insert_event(
            source=f"runtime:{name}",
            type='file_changed',
            text=output,
            metadata={'source_type': 'watch', 'name': name}
        )
        _notify_event_waiters()


def _glob_to_regex(pattern):
    """Convert shell glob to regex (matches file-change.sh logic)."""
    result = pattern
    result = result.replace('.', '\\.')
    result = result.replace('**/', '___GS___')
    result = result.replace('**', '___G___')
    result = result.replace('*', '[^/]*')
    result = result.replace('?', '[^/]')
    result = result.replace('___GS___', '(.*/)?')
    result = result.replace('___G___', '.*')
    return result


def _is_glob(path):
    """Check if a path contains glob characters."""
    return any(c in path for c in ('*', '?', '['))


def _watch_files_once(paths, root, source):
    """Block until a file in paths changes. Returns changed file path."""
    has_globs = any(_is_glob(p) for p in paths)

    if shutil.which('fswatch'):
        args = ['fswatch', '-1']
        if has_globs or len(paths) > 1:
            args.append('-E')
            abs_root = os.path.realpath(root)
            escaped_root = abs_root.replace('.', '\\.')
            for p in paths:
                if _is_glob(p):
                    regex = _glob_to_regex(p)
                    args.extend(['--include', f'^{escaped_root}/{regex}$'])
                else:
                    abs_path = os.path.join(abs_root, p)
                    if os.path.exists(abs_path):
                        abs_path = os.path.realpath(abs_path)
                    escaped = abs_path.replace('.', '\\.')
                    args.extend(['--include', f'^{escaped}$'])
            args.extend(['--exclude', '.*'])
            args.append(root)
        else:
            target = paths[0]
            if not os.path.isabs(target):
                target = os.path.join(root, target)
            args.append(target)

        proc = subprocess.Popen(args, stdout=subprocess.PIPE, text=True)
        source['process'] = proc
        output = proc.stdout.read().strip()
        proc.wait()
        return output

    elif shutil.which('inotifywait'):
        args = ['inotifywait', '-r', '-e', 'modify', '-q']
        if has_globs or len(paths) > 1:
            abs_root = os.path.realpath(root)
            escaped_root = abs_root.replace('.', '\\.')
            regex_parts = []
            for p in paths:
                if _is_glob(p):
                    regex_parts.append(f'^{escaped_root}/{_glob_to_regex(p)}$')
                else:
                    abs_path = os.path.join(abs_root, p)
                    escaped = abs_path.replace('.', '\\.')
                    regex_parts.append(f'^{escaped}$')
            combined = '|'.join(regex_parts)
            args.extend(['--include', combined])
        args.append(root)

        proc = subprocess.Popen(args, stdout=subprocess.PIPE, text=True)
        source['process'] = proc
        output = proc.stdout.read().strip()
        proc.wait()
        return output

    else:
        # Stat-polling fallback — direct files only
        if has_globs:
            raise RuntimeError("Glob patterns require fswatch or inotifywait")

        mtimes = {}
        for p in paths:
            full = os.path.join(root, p) if not os.path.isabs(p) else p
            try:
                mtimes[full] = os.stat(full).st_mtime
            except OSError:
                mtimes[full] = 0

        while source['active']:
            time.sleep(1)
            for full, prev_mtime in list(mtimes.items()):
                try:
                    current = os.stat(full).st_mtime
                except OSError:
                    current = 0
                if current != prev_mtime:
                    return full

        return ''


def _run_tail_source(source):
    """Tail a file, collecting line chunks as events."""
    name = source['name']
    config = source['config']
    filepath = config['file']
    timeout = config.get('timeout', 10)
    max_lines = config.get('max_lines', 100)

    while source['active']:
        try:
            proc = subprocess.Popen(
                ['tail', '-f', filepath],
                stdout=subprocess.PIPE, text=True
            )
            source['process'] = proc

            lines = []
            while source['active'] and len(lines) < max_lines:
                ready, _, _ = select.select([proc.stdout], [], [], timeout)
                if not ready:
                    break  # Timeout — emit what we have
                line = proc.stdout.readline()
                if not line:
                    break
                lines.append(line.rstrip('\n'))

            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

            if lines and source['active']:
                insert_event(
                    source=f"runtime:{name}",
                    type='log_lines',
                    text='\n'.join(lines),
                    metadata={
                        'source_type': 'tail', 'name': name,
                        'line_count': len(lines), 'file': filepath
                    }
                )
                _notify_event_waiters()
            elif not lines:
                time.sleep(1)

        except Exception as e:
            if source['active']:
                sys.stderr.write(f"[sidecar] tail source '{name}' error: {e}\n")
                time.sleep(5)


def _run_ci_source(source):
    """Watch a CI run until completion."""
    name = source['name']
    config = source['config']
    run_id = None

    arg = config.get('run_id') or config.get('branch', '')
    if not arg:
        source['active'] = False
        return

    try:
        if str(arg).isdigit():
            run_id = str(arg)
        else:
            result = subprocess.run(
                ['gh', 'run', 'list', '--branch', str(arg), '--limit', '1',
                 '--json', 'databaseId', '-q', '.[0].databaseId'],
                capture_output=True, text=True, timeout=30
            )
            run_id = result.stdout.strip()
            if not run_id:
                insert_event(
                    source=f"runtime:{name}",
                    type='ci_error',
                    text=f'No runs found for branch: {arg}',
                    metadata={'source_type': 'ci', 'name': name}
                )
                _notify_event_waiters()
                source['active'] = False
                _deactivate_source_db(name)
                return

        proc = subprocess.Popen(
            ['gh', 'run', 'watch', run_id, '--exit-status'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        source['process'] = proc
        output, _ = proc.communicate()
        output = (output or '').strip()
        exit_code = proc.returncode

    except Exception as e:
        output = f'ERROR: {e}'
        exit_code = -1

    if not source['active']:
        return

    insert_event(
        source=f"runtime:{name}",
        type='ci_completed',
        text=output,
        metadata={
            'source_type': 'ci', 'name': name,
            'run_id': run_id or str(arg),
            'exit_code': exit_code
        }
    )
    _notify_event_waiters()
    source['active'] = False
    _deactivate_source_db(name)


# --- Webhook handler factory ---

def _make_webhook_handler(source_name):
    """Create an HTTP handler for a webhook source."""
    def handler(request_handler, params=None):
        body = request_handler._read_body().decode('utf-8', errors='replace')
        request_handler.send_response(200)
        request_handler.send_header('Content-Type', 'application/json')
        request_handler.end_headers()
        request_handler.wfile.write(b'{"ok":true}')

        event_data = {
            'method': request_handler.command,
            'path': request_handler.path,
            'body': body,
        }

        insert_event(
            source=f"runtime:{source_name}",
            type='webhook_received',
            text=json.dumps(event_data),
            metadata={'source_type': 'webhook', 'name': source_name}
        )
        _notify_event_waiters()

    return handler


# --- Source lifecycle management ---

def _register_runtime_source(name, stype, config):
    """Register a new runtime source. Returns (success, message)."""
    valid_types = ('poll', 'heartbeat', 'command', 'watch', 'tail', 'ci', 'webhook')
    if stype not in valid_types:
        return False, f"Invalid type '{stype}'. Valid: {', '.join(valid_types)}"

    # Validate required config per type
    if stype == 'poll':
        if 'command' not in config:
            return False, "poll requires 'command'"
        config.setdefault('interval', 30)
        config.setdefault('diff', True)
    elif stype == 'heartbeat':
        config.setdefault('interval', 60)
    elif stype == 'command':
        if 'command' not in config:
            return False, "command requires 'command'"
    elif stype == 'watch':
        if 'paths' not in config:
            return False, "watch requires 'paths' (list)"
        if isinstance(config['paths'], str):
            config['paths'] = [config['paths']]
    elif stype == 'tail':
        if 'file' not in config:
            return False, "tail requires 'file'"
        config.setdefault('timeout', 10)
        config.setdefault('max_lines', 100)
    elif stype == 'ci':
        if 'run_id' not in config and 'branch' not in config:
            return False, "ci requires 'run_id' or 'branch'"
    elif stype == 'webhook':
        if 'path' not in config:
            return False, "webhook requires 'path'"

    with _runtime_sources_lock:
        if name in _runtime_sources:
            _stop_source(name)

        source = {
            'name': name,
            'type': stype,
            'config': config,
            'active': True,
            'last_output': None,
            'process': None,
            'thread': None,
        }
        _runtime_sources[name] = source

    _save_source_to_db(name, stype, config)
    _start_source(source)

    sys.stderr.write(f"[sidecar] Runtime source registered: {name} ({stype})\n")
    return True, f"Source '{name}' registered"


def _start_source(source):
    """Start a source based on its type."""
    stype = source['type']

    if stype in ('poll', 'heartbeat'):
        interval = source['config'].get('interval', 30)

        # For poll, capture baseline immediately
        if stype == 'poll':
            try:
                result = subprocess.run(
                    source['config']['command'], shell=True,
                    capture_output=True, text=True, timeout=30
                )
                source['last_output'] = result.stdout.strip()
                _update_source_output_db(source['name'], source['last_output'])
            except Exception:
                source['last_output'] = None

        with _runtime_sources_lock:
            gen = _source_generation.get(source['name'], 0) + 1
            _source_generation[source['name']] = gen
            heapq.heappush(_scheduler_heap, (time.time() + interval, source['name'], gen))
        _scheduler_event.set()

    elif stype in ('command', 'watch', 'tail', 'ci'):
        _start_blocking_source(source)

    elif stype == 'webhook':
        path = source['config']['path']
        if not path.startswith('/'):
            path = '/' + path
        handler = _make_webhook_handler(source['name'])
        _plugin_routes[('POST', path)] = handler
        _plugin_routes[('GET', path)] = handler
        sys.stderr.write(f"[sidecar] Webhook route registered: POST {path}\n")


def _stop_source(name):
    """Stop a running source. Safe to call from any context."""
    source = _runtime_sources.get(name)
    if not source:
        return

    source['active'] = False
    # Bump generation so stale heap entries are ignored on pop
    _source_generation[name] = _source_generation.get(name, 0) + 1

    proc = source.get('process')
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
            except OSError:
                pass

    if source['type'] == 'webhook':
        path = source['config'].get('path', '')
        if not path.startswith('/'):
            path = '/' + path
        _plugin_routes.pop(('POST', path), None)
        _plugin_routes.pop(('GET', path), None)


def _remove_runtime_source(name):
    """Remove a source entirely. Returns (success, message)."""
    with _runtime_sources_lock:
        if name not in _runtime_sources:
            return False, f"Source '{name}' not found"
        _stop_source(name)
        del _runtime_sources[name]

    _delete_source_db(name)
    sys.stderr.write(f"[sidecar] Runtime source removed: {name}\n")
    return True, f"Source '{name}' removed"


def _list_runtime_sources():
    """List all runtime sources with status."""
    with _runtime_sources_lock:
        return [
            {
                'name': s['name'],
                'type': s['type'],
                'active': s['active'],
                'config': s['config'],
            }
            for s in _runtime_sources.values()
        ]


def _restore_sources():
    """Restore active sources from DB on startup."""
    saved = _load_sources_from_db()
    for s in saved:
        source = {
            'name': s['name'],
            'type': s['type'],
            'config': s['config'],
            'active': True,
            'last_output': s['last_output'],
            'process': None,
            'thread': None,
        }
        with _runtime_sources_lock:
            _runtime_sources[s['name']] = source
        _start_source(source)
        sys.stderr.write(f"[sidecar] Restored source: {s['name']} ({s['type']})\n")


# ---------------------------------------------------------------------------
# Query string parser
# ---------------------------------------------------------------------------

def _parse_qs(path):
    """Parse path into (path, params_dict)."""
    if '?' not in path:
        return path, {}
    base, query = path.split('?', 1)
    params = {}
    for part in query.split('&'):
        if '=' in part:
            k, v = part.split('=', 1)
            params[k] = unquote(v)
        elif part:
            params[part] = 'true'
    return base, params


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class SidecarHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        """Suppress default request logging."""
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()  # Force TCP send — triggers BrokenPipeError on dead client

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length) if length else b''

    def _headers_dict(self):
        """Convert request headers to a plain dict."""
        h = {}
        for key in self.headers:
            h[key] = self.headers[key]
        return h

    # ---- POST routes ----

    def do_POST(self):
        try:
            path, params = _parse_qs(self.path)

            # Check plugin routes first
            handler = _plugin_routes.get(('POST', path))
            if handler:
                handler(self)
                return

            if path == '/source':
                self._handle_source_post()
            elif path == '/voice':
                self._handle_voice_event()
            elif path == '/respond':
                self._handle_respond()
            elif path == '/ledger':
                self._handle_ledger_post()
            elif path == '/watch':
                self._handle_watch_post()
            else:
                # Fallback: any POST path → store as raw event (backward compat)
                source = path.lstrip('/').split('/')[0] if path.lstrip('/') else 'unknown'
                self._handle_ingest(source)
        except Exception as e:
            sys.stderr.write(f"[sidecar] POST {self.path} error: {e}\n")
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass

    def _handle_ingest(self, source):
        """POST /<anything> — store raw body + headers as an event (backward compat)."""
        headers = self._headers_dict()
        raw = self._read_body()
        body = raw.decode('utf-8', errors='replace')

        # Slack url_verification handshake
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get('type') == 'url_verification':
                challenge = data.get('challenge', '')
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(str(challenge).encode())
                return
        except (json.JSONDecodeError, ValueError):
            pass

        self.send_response(200)
        self.end_headers()

        _insert_raw_event(source, headers, body)
        _notify_event_waiters()
        sys.stderr.write(f"[sidecar] event from {source} ({len(body)} bytes)\n")

    def _handle_voice_event(self):
        """POST /voice — voice transcript."""
        body = self._read_body()
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "invalid json"}, 400)
            return

        text = data.get('text', '').strip()
        if not text:
            self._send_json({"error": "empty text"}, 400)
            return

        source = data.get('source', 'voice')
        insert_event(source=source, text=text)
        _notify_event_waiters()
        sys.stderr.write(f"[sidecar] voice event: {text[:80]}\n")
        self._send_json({"ok": True})

    def _handle_respond(self):
        """POST /respond — agent posts a response, routed by source."""
        body = self._read_body()
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "invalid json"}, 400)
            return

        text = data.get('text', '').strip()
        if not text:
            self._send_json({"error": "empty text"}, 400)
            return

        event_id = data.get('event_id')
        source = data.get('source', 'voice')
        _insert_response(event_id, text, source)
        _notify_response_waiters()
        sys.stderr.write(f"[sidecar] response ({source}): {text[:80]}\n")
        self._send_json({"ok": True})

    def _handle_watch_post(self):
        """POST /watch — add a watch for a PR-scoped plugin."""
        body = self._read_body()
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "invalid json"}, 400)
            return

        plugin = data.get('plugin', '').strip()
        url = data.get('url', '').strip()
        if not plugin or not url:
            self._send_json({"error": "plugin and url are required"}, 400)
            return

        if plugin not in _plugin_watch_handlers:
            self._send_json({"error": f"no watch handler for plugin '{plugin}'"}, 404)
            return

        added = _add_watch(plugin, url)
        if added:
            try:
                _plugin_watch_handlers[plugin]['add'](url)
            except Exception as e:
                sys.stderr.write(f"[sidecar] watch add callback error ({plugin}): {e}\n")

        sys.stderr.write(f"[sidecar] watch {'added' if added else 'exists'}: {plugin} → {url}\n")
        self._send_json({"ok": True, "added": added})

    # ---- DELETE routes ----

    def do_DELETE(self):
        try:
            path, params = _parse_qs(self.path)

            if path == '/watch':
                self._handle_watch_delete()
            elif path == '/source':
                self._handle_source_delete()
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:
            sys.stderr.write(f"[sidecar] DELETE {self.path} error: {e}\n")
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass

    def _handle_watch_delete(self):
        """DELETE /watch — remove a watch."""
        body = self._read_body()
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "invalid json"}, 400)
            return

        plugin = data.get('plugin', '').strip()
        url = data.get('url', '').strip()
        if not plugin or not url:
            self._send_json({"error": "plugin and url are required"}, 400)
            return

        removed = _remove_watch(plugin, url)
        if removed and plugin in _plugin_watch_handlers:
            try:
                _plugin_watch_handlers[plugin]['remove'](url)
            except Exception as e:
                sys.stderr.write(f"[sidecar] watch remove callback error ({plugin}): {e}\n")

        self._send_json({"ok": True, "removed": removed})

    # ---- GET routes ----

    def do_GET(self):
        try:
            path, params = _parse_qs(self.path)

            # Check plugin routes
            handler = _plugin_routes.get(('GET', path))
            if handler:
                handler(self, params)
                return

            if path == '/sources':
                self._handle_sources_get(params)
            elif path == '/events':
                self._handle_events(params)
            elif path == '/responses':
                self._handle_responses(params)
            elif path == '/ledger':
                self._handle_ledger_get(params)
            elif path == '/watches':
                self._handle_watches_get(params)
            elif path == '/health':
                self._handle_health()
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:
            sys.stderr.write(f"[sidecar] GET {self.path} error: {e}\n")
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass

    def _handle_events(self, params):
        """GET /events — drain all pending events from all sources.

        With wait=true, blocks until events are available or timeout expires.
        Without wait, returns immediately (may be []).

        Params:
            wait           — block until events available
            timeout        — max seconds to wait (default 480, i.e. 8 min)
            source         — exact match on source field
            sender_exclude — drop events where metadata.sender matches
        """
        wait = params.get('wait', '').lower() == 'true'
        source = params.get('source')
        sender_exclude = params.get('sender_exclude')
        timeout = float(params.get('timeout', '480'))

        if wait:
            deadline = time.time() + timeout
            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                # Block until events exist or timeout
                with _event_cond:
                    while _pending_count(source) == 0:
                        left = deadline - time.time()
                        if left <= 0:
                            break
                        _event_cond.wait(timeout=min(5.0, left))
                    if _pending_count(source) == 0:
                        break  # Timed out
                # Burst collection — wait 500ms for more events to batch
                time.sleep(0.5)
                # Peek first (don't mark picked), then send, then commit
                events, event_ids = _peek_events(source, sender_exclude=sender_exclude)
                if events:
                    try:
                        self._send_json(events)
                        self.wfile.flush()
                        # Send succeeded — now mark as picked
                        _mark_events_picked(event_ids)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass  # Client gone — events stay unpicked for next drain
                    return
                # All pending events were filtered out; mark them picked and wait for more
                if event_ids:
                    _mark_events_picked(event_ids)
            # Timeout expired — return empty so handler thread exits cleanly
            self._send_json([])
        else:
            events = _pick_events(source, sender_exclude=sender_exclude)
            self._send_json(events)

    def _handle_responses(self, params):
        """GET /responses — client picks up responses."""
        wait = params.get('wait', '').lower() == 'true'
        timeout = float(params.get('timeout', '120'))
        source = params.get('source')

        responses = _pick_responses(source)
        if responses or not wait:
            self._send_json(responses)
            return

        deadline = time.time() + timeout
        with _response_cond:
            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                _response_cond.wait(timeout=min(1.0, remaining))
                responses = _pick_responses(source)
                if responses:
                    self._send_json(responses)
                    return
        self._send_json([])

    # ---- Ledger routes ----

    def _handle_ledger_post(self):
        """POST /ledger — append an entry to the shared colony ledger."""
        body = self._read_body()
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "invalid json"}, 400)
            return

        agent_id = data.get('agent_id', '').strip()
        content = data.get('content', '').strip()
        if not agent_id or not content:
            self._send_json({"error": "agent_id and content are required"}, 400)
            return

        entry_type = data.get('entry_type', 'memory').strip()
        did = data.get('did')
        tags = data.get('tags')
        signature = data.get('signature')

        entry_id = _insert_ledger_entry(agent_id, content, entry_type, did, tags, signature)
        _notify_ledger_waiters()
        sys.stderr.write(f"[sidecar] ledger entry #{entry_id} from {agent_id}: {content[:80]}\n")
        self._send_json({"ok": True, "id": entry_id})

    def _handle_ledger_get(self, params):
        """GET /ledger — query the shared colony ledger."""
        since_id = int(params.get('since', '0'))
        agent_id = params.get('agent_id')
        entry_type = params.get('entry_type')
        tag = params.get('tag')
        limit = int(params.get('limit', '100'))
        wait = params.get('wait', '').lower() == 'true'

        entries = _query_ledger(since_id, agent_id, entry_type, tag, limit)
        if entries or not wait:
            self._send_json(entries)
            return

        deadline = time.time() + 30.0
        with _ledger_cond:
            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                _ledger_cond.wait(timeout=min(1.0, remaining))
                entries = _query_ledger(since_id, agent_id, entry_type, tag, limit)
                if entries:
                    self._send_json(entries)
                    return
        self._send_json([])

    # ---- Watch routes ----

    def _handle_watches_get(self, params):
        """GET /watches — list active watches per plugin."""
        plugin = params.get('plugin')
        watches = _list_watches(plugin)
        self._send_json(watches)

    # ---- Source management routes ----

    def _handle_source_post(self):
        """POST /source — register a runtime event source."""
        body = self._read_body()
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "invalid json"}, 400)
            return

        name = data.get('name', '').strip()
        stype = data.get('type', '').strip()
        if not name or not stype:
            self._send_json({"error": "name and type are required"}, 400)
            return

        config = {k: v for k, v in data.items() if k not in ('name', 'type')}
        ok, msg = _register_runtime_source(name, stype, config)
        self._send_json({"ok": ok, "message": msg}, 200 if ok else 400)

    def _handle_source_delete(self):
        """DELETE /source — remove a runtime event source."""
        body = self._read_body()
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "invalid json"}, 400)
            return

        name = data.get('name', '').strip()
        if not name:
            self._send_json({"error": "name is required"}, 400)
            return

        ok, msg = _remove_runtime_source(name)
        self._send_json({"ok": ok, "message": msg}, 200 if ok else 404)

    def _handle_sources_get(self, params=None):
        """GET /sources — list active runtime sources."""
        sources = _list_runtime_sources()
        self._send_json(sources)

    # ---- Health ----

    def _handle_health(self):
        """GET /health — status with pending counts, plugins, watches, sources."""
        counts = _pending_counts()
        counts['ledger_entries'] = _ledger_max_id()
        plugins = [{"name": name, "path": path} for name, path in _loaded_plugins]
        watches = _list_watches()
        sources = _list_runtime_sources()
        self._send_json({
            "status": "ok",
            "version": __version__,
            "pending": counts,
            "plugins": plugins,
            "watches": watches,
            "sources": sources,
            "db": DB_PATH,
            "project_root": _project_root,
        })


# ---------------------------------------------------------------------------
# Plugin discovery and loading
# ---------------------------------------------------------------------------

def _discover_plugins():
    """Discover sidecar plugins from the Claude Code plugin manifest.

    Reads ~/.claude/plugins/installed_plugins.json and checks each installed
    plugin for sidecar/plugin.py with a register(api) function.
    """
    manifest_path = os.path.expanduser('~/.claude/plugins/installed_plugins.json')
    if not os.path.isfile(manifest_path):
        sys.stderr.write(f"[sidecar] No plugin manifest at {manifest_path}\n")
        return []

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        sys.stderr.write(f"[sidecar] Failed to read plugin manifest: {e}\n")
        return []

    # v2 format: {"version": 2, "plugins": {"id@market": [{"installPath": "...", "scope": "project", ...}]}}
    # v1 format: [{"install_path": "...", "name": "..."}]
    raw_plugins = manifest
    if isinstance(manifest, dict):
        raw_plugins = manifest.get('plugins', {})

    plugins = []
    project_root = _project_root

    if isinstance(raw_plugins, dict):
        # v2 format — dict of plugin_id -> list of install entries
        for plugin_id, installs in raw_plugins.items():
            if not isinstance(installs, list):
                continue
            for entry in installs:
                if not isinstance(entry, dict):
                    continue
                # Filter: only load plugins scoped to this project or user-scoped
                scope = entry.get('scope', '')
                project_path = entry.get('projectPath', '')
                if scope == 'project' and project_path and project_root:
                    if os.path.realpath(project_path) != os.path.realpath(project_root):
                        continue
                install_path = entry.get('installPath') or entry.get('install_path', '')
                if not install_path:
                    continue
                plugin_py = os.path.join(install_path, 'sidecar', 'plugin.py')
                if os.path.isfile(plugin_py):
                    name = plugin_id.split('@')[0] if '@' in plugin_id else os.path.basename(install_path)
                    plugins.append((name, plugin_py))
    elif isinstance(raw_plugins, list):
        # v1 format — flat list
        for entry in raw_plugins:
            if not isinstance(entry, dict):
                continue
            install_path = entry.get('install_path') or entry.get('path', '')
            if not install_path:
                continue
            plugin_py = os.path.join(install_path, 'sidecar', 'plugin.py')
            if os.path.isfile(plugin_py):
                name = entry.get('name') or os.path.basename(install_path)
                plugins.append((name, plugin_py))

    return plugins


def _load_plugins():
    """Discover and load sidecar plugins."""
    sidecar_api = {
        'insert_event': insert_event,
        'notify_waiters': _notify_event_waiters,
        'register_route': _register_route,
        'register_poller': _register_poller,
        'register_init': _register_init,
        'register_on_pick': _register_on_pick,
        'register_enrichment': _register_enrichment,
        'register_watch_handler': _register_watch_handler,
        'get_db': _get_db,
        'db_lock': _db_lock,
        'project_root': _project_root,
    }

    discovered = _discover_plugins()

    for name, plugin_path in discovered:
        module_name = name.replace('-', '_').replace(' ', '_')
        try:
            spec = importlib.util.spec_from_file_location(module_name, plugin_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, 'register'):
                module.register(sidecar_api)
                _loaded_plugins.append((name, plugin_path))
                sys.stderr.write(f"[sidecar] Loaded plugin: {name} ({plugin_path})\n")
            else:
                sys.stderr.write(f"[sidecar] Skipping {name}: no register() function\n")
        except Exception as e:
            sys.stderr.write(f"[sidecar] Failed to load plugin {name}: {e}\n")


# ---------------------------------------------------------------------------
# Metadata file
# ---------------------------------------------------------------------------

def _write_metadata(port, pid):
    """Write sidecar metadata to .claude/sidecar.json in the project root."""
    meta_dir = os.path.join(_project_root, '.claude')
    os.makedirs(meta_dir, exist_ok=True)
    meta_path = os.path.join(meta_dir, 'sidecar.json')

    metadata = {
        "port": port,
        "pid": pid,
        "db": DB_PATH,
        "project_root": os.path.abspath(_project_root),
        "version": __version__,
        "started_at": time.time(),
    }

    with open(meta_path, 'w') as f:
        json.dump(metadata, f, indent=2)

    sys.stderr.write(f"[sidecar] Metadata written to {meta_path}\n")
    return meta_path


def _cleanup_metadata():
    """Remove sidecar.json on shutdown."""
    meta_path = os.path.join(_project_root, '.claude', 'sidecar.json')
    try:
        if os.path.isfile(meta_path):
            os.remove(meta_path)
            sys.stderr.write(f"[sidecar] Cleaned up {meta_path}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    _init_db()
    _ensure_indexes()
    _init_sources_table()

    # Load plugins from manifest
    _load_plugins()

    # Restore watches from DB and notify plugins
    for plugin_name, handler in _plugin_watch_handlers.items():
        watches = _list_watches(plugin_name)
        for w in watches:
            try:
                handler['add'](w['url'])
            except Exception as e:
                sys.stderr.write(f"[sidecar] watch restore error ({plugin_name}): {e}\n")

    # Run plugin init hooks
    for name, init_func in _plugin_inits:
        try:
            init_func()
        except Exception as e:
            sys.stderr.write(f"[sidecar] Init hook '{name}' error: {e}\n")

    # Start plugin pollers
    for name, poller_func in _plugin_pollers:
        t = threading.Thread(target=poller_func, daemon=True, name=f"poller-{name}")
        t.start()

    # Start runtime source scheduler
    threading.Thread(target=_scheduler_loop, daemon=True, name='source-scheduler').start()

    # Restore runtime sources from DB
    _restore_sources()

    server = ThreadingHTTPServer(('0.0.0.0', _requested_port), SidecarHandler)
    actual_port = server.server_address[1]

    # Write metadata
    meta_path = _write_metadata(actual_port, os.getpid())

    # Clean up on exit
    def _shutdown_handler(signum, frame):
        sys.stderr.write("[sidecar] Shutting down.\n")
        _cleanup_metadata()
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    sys.stderr.write(f"[sidecar] v{__version__} listening on 0.0.0.0:{actual_port}\n")
    sys.stderr.write(f"[sidecar] DB: {DB_PATH}\n")
    sys.stderr.write(f"[sidecar] Project: {os.path.abspath(_project_root)}\n")

    if _loaded_plugins:
        sys.stderr.write(f"[sidecar] Plugins ({len(_loaded_plugins)}):\n")
        for name, path in _loaded_plugins:
            sys.stderr.write(f"  - {name}\n")

    sys.stderr.write(f"[sidecar] Routes:\n")
    for (method, path), _ in sorted(_plugin_routes.items()):
        sys.stderr.write(f"  {method:6s} {path:<20s} — plugin\n")
    sys.stderr.write(f"  POST   /source            — Register runtime source\n")
    sys.stderr.write(f"  DELETE /source            — Remove runtime source\n")
    sys.stderr.write(f"  GET    /sources           — List runtime sources\n")
    sys.stderr.write(f"  POST   /voice             — Voice transcripts\n")
    sys.stderr.write(f"  POST   /respond           — Agent posts responses\n")
    sys.stderr.write(f"  POST   /ledger            — Colony ledger writes\n")
    sys.stderr.write(f"  POST   /watch             — Add PR watch\n")
    sys.stderr.write(f"  DELETE /watch             — Remove PR watch\n")
    sys.stderr.write(f"  GET    /events?wait=true  — Drain all sources (blocking)\n")
    sys.stderr.write(f"  GET    /responses?wait=true — Pick up responses\n")
    sys.stderr.write(f"  GET    /ledger?wait=true  — Colony ledger tail\n")
    sys.stderr.write(f"  GET    /watches           — List active watches\n")
    sys.stderr.write(f"  GET    /health            — Status + plugins + watches + sources\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup_metadata()
        sys.stderr.write("[sidecar] Shut down.\n")


if __name__ == '__main__':
    main()
