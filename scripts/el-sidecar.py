"""el-sidecar — Plugin-aware event hub for Claude Code agents.

Version: 0.9.0

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
    - Auto-port (port 0) with metadata written to .claude/sidecar.json
    - Per-project DB isolation (/tmp/el-sidecar-{hash}.db)
    - Plugin discovery from ~/.claude/plugins/installed_plugins.json

Usage: python3 el-sidecar.py [port] [--project-root PATH]

Env vars:
    SIDECAR_PORT           — Port (default: 0 = auto-assign)
    SIDECAR_DB_PATH        — SQLite path override (default: auto per-project)
    SIDECAR_PROJECT_ROOT   — Project root for metadata (default: cwd)
"""

__version__ = '0.9.0'

import hashlib
import importlib.util
import json
import os
import signal
import sqlite3
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import unquote


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


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

    port = port or int(os.environ.get('SIDECAR_PORT', '0'))
    project_root = project_root or os.environ.get('SIDECAR_PROJECT_ROOT', os.getcwd())

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

_db_lock = threading.Lock()


def _get_db():
    """Create a new connection for the calling thread."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
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
    return conn


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

def _pick_events(source=None):
    """Return all unpicked events, mark them picked up."""
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
                events.append(evt)

            # Mark picked up
            ids = [row[0] for row in rows]
            placeholders = ','.join('?' for _ in ids)
            conn.execute(
                f"UPDATE events SET picked_up = 1 WHERE id IN ({placeholders})",
                ids,
            )
            conn.commit()

            # Notify plugins about picked events
            for name, callback in _plugin_on_pick:
                try:
                    callback(events)
                except Exception as e:
                    sys.stderr.write(f"[sidecar] on_pick callback '{name}' error: {e}\n")

            return events
        finally:
            conn.close()


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

_event_notify = threading.Event()
_response_notify = threading.Event()
_ledger_notify = threading.Event()


def _notify_event_waiters():
    _event_notify.set()


def _notify_response_waiters():
    _response_notify.set()


def _notify_ledger_waiters():
    _ledger_notify.set()


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

            if path == '/voice':
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

            if path == '/events':
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

        With wait=true, blocks until events are available (never returns []).
        Without wait, returns immediately (may be []).
        """
        wait = params.get('wait', '').lower() == 'true'
        source = params.get('source')

        if wait:
            # Block until events exist — no timeout, no empty returns
            while _pending_count(source) == 0:
                _event_notify.clear()
                _event_notify.wait(timeout=5.0)

            # Burst collection — wait 500ms for more events to batch
            time.sleep(0.5)

        events = _pick_events(source)
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
        while time.time() < deadline:
            _response_notify.clear()
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            _response_notify.wait(timeout=min(1.0, remaining))
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
        while time.time() < deadline:
            _ledger_notify.clear()
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            _ledger_notify.wait(timeout=min(1.0, remaining))
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

    # ---- Health ----

    def _handle_health(self):
        """GET /health — status with pending counts, plugins, watches."""
        counts = _pending_counts()
        counts['ledger_entries'] = _ledger_max_id()
        plugins = [{"name": name, "path": path} for name, path in _loaded_plugins]
        watches = _list_watches()
        self._send_json({
            "status": "ok",
            "version": __version__,
            "pending": counts,
            "plugins": plugins,
            "watches": watches,
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
    sys.stderr.write(f"  POST   /voice             — Voice transcripts\n")
    sys.stderr.write(f"  POST   /respond           — Agent posts responses\n")
    sys.stderr.write(f"  POST   /ledger            — Colony ledger writes\n")
    sys.stderr.write(f"  POST   /watch             — Add PR watch\n")
    sys.stderr.write(f"  DELETE /watch             — Remove PR watch\n")
    sys.stderr.write(f"  GET    /events?wait=true  — Drain all sources (blocking)\n")
    sys.stderr.write(f"  GET    /responses?wait=true — Pick up responses\n")
    sys.stderr.write(f"  GET    /ledger?wait=true  — Colony ledger tail\n")
    sys.stderr.write(f"  GET    /watches           — List active watches\n")
    sys.stderr.write(f"  GET    /health            — Status + plugins + watches\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup_metadata()
        sys.stderr.write("[sidecar] Shut down.\n")


if __name__ == '__main__':
    main()
