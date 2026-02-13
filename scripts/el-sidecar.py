"""Generic HTTP event buffer for the el core plugin.

Source-agnostic: stores raw HTTP requests in SQLite, lets agents drain them.
Routes by path — POST /slack → source='slack', POST /voice → source='voice', etc.

Usage: python3 el-sidecar.py [port]

Env vars:
    SIDECAR_PORT           — Port (default: 9999)
    SIDECAR_DB_PATH        — SQLite path (default: /tmp/el-sidecar.db)
    SIDECAR_DEFAULT_SOURCE — Source tag for root path POSTs (default: 'unknown')
"""

import json
import os
import sqlite3
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get('SIDECAR_PORT', '9999'))
DB_PATH = os.environ.get('SIDECAR_DB_PATH', '/tmp/el-sidecar.db')
DEFAULT_SOURCE = os.environ.get('SIDECAR_DEFAULT_SOURCE', 'unknown')

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
            headers     TEXT,
            body        TEXT,
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
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Event storage
# ---------------------------------------------------------------------------

def _insert_event(source, headers, body):
    """Insert a raw event. Returns the new row id."""
    with _db_lock:
        conn = _get_db()
        try:
            cursor = conn.execute(
                "INSERT INTO events (source, headers, body, picked_up, received_at) "
                "VALUES (?, ?, ?, 0, ?)",
                (source, json.dumps(headers), body, time.time()),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()


def _pick_events(source=None):
    """Return unpicked events, optionally filtered by source. Mark them picked up."""
    with _db_lock:
        conn = _get_db()
        try:
            if source:
                rows = conn.execute(
                    "SELECT id, source, headers, body, received_at "
                    "FROM events WHERE picked_up = 0 AND source = ? "
                    "ORDER BY received_at ASC, id ASC",
                    (source,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, source, headers, body, received_at "
                    "FROM events WHERE picked_up = 0 "
                    "ORDER BY received_at ASC, id ASC"
                ).fetchall()
            if not rows:
                return []

            events = []
            for row in rows:
                eid, source, headers_json, body, received_at = row
                try:
                    headers = json.loads(headers_json) if headers_json else {}
                except (json.JSONDecodeError, ValueError):
                    headers = {}
                events.append({
                    "id": eid,
                    "source": source,
                    "headers": headers,
                    "body": body,
                    "received_at": received_at,
                })

            ids = [row[0] for row in rows]
            placeholders = ','.join('?' for _ in ids)
            conn.execute(
                f"UPDATE events SET picked_up = 1 WHERE id IN ({placeholders})",
                ids,
            )
            conn.commit()
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
            counts['total'] = sum(counts.values())
            return counts
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Response storage and retrieval
# ---------------------------------------------------------------------------

def _insert_response(event_id, text, source):
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
# Long-poll notification
# ---------------------------------------------------------------------------

_event_notify = threading.Event()
_response_notify = threading.Event()


def _notify_event_waiters():
    _event_notify.set()


def _notify_response_waiters():
    _response_notify.set()


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
            params[k] = v
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

            if path == '/respond':
                self._handle_respond()
                return

            # Any other POST path → store as event
            source = path.lstrip('/').split('/')[0] if path.lstrip('/') else DEFAULT_SOURCE
            self._handle_ingest(source)
        except Exception as e:
            sys.stderr.write(f"[sidecar] POST {self.path} error: {e}\n")
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass

    def _handle_ingest(self, source):
        """POST /<anything> — store raw body + headers as an event."""
        headers = self._headers_dict()
        raw = self._read_body()
        body = raw.decode('utf-8', errors='replace')

        # Slack url_verification handshake — the ONLY content inspection
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

        # Ack immediately
        self.send_response(200)
        self.end_headers()

        _insert_event(source, headers, body)
        _notify_event_waiters()
        sys.stderr.write(f"[sidecar] event from {source} ({len(body)} bytes)\n")

    def _handle_respond(self):
        """POST /respond — store a response for bidirectional sources."""
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
        source = data.get('source', 'unknown')
        _insert_response(event_id, text, source)
        _notify_response_waiters()
        sys.stderr.write(f"[sidecar] response ({source}): {text[:80]}\n")
        self._send_json({"ok": True})

    # ---- GET routes ----

    def do_GET(self):
        try:
            path, params = _parse_qs(self.path)
            if path == '/events':
                self._handle_events(params)
            elif path == '/responses':
                self._handle_responses(params)
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
        """GET /events — drain pending events.

        With ?wait=true: long-poll up to 30s, then 500ms burst window.
        With ?source=slack: only drain events from that source.
        """
        wait = params.get('wait', '').lower() == 'true'
        source = params.get('source')

        if wait:
            deadline = time.time() + 30.0
            while time.time() < deadline:
                if _pending_count(source) > 0:
                    break
                _event_notify.clear()
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                _event_notify.wait(timeout=min(0.2, remaining))

            # Burst collection — if something arrived, wait 500ms for more
            if _pending_count(source) > 0:
                time.sleep(0.5)

        events = _pick_events(source)
        self._send_json(events)

    def _handle_responses(self, params):
        """GET /responses — client picks up responses.

        With ?wait=true: long-poll up to timeout (default 120s).
        Optional ?source=voice to filter by source.
        """
        wait = params.get('wait', '').lower() == 'true'
        timeout = float(params.get('timeout', '120'))
        source = params.get('source')

        responses = _pick_responses(source)
        if responses or not wait:
            self._send_json(responses)
            return

        # Long-poll
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

    def _handle_health(self):
        """GET /health — status with pending counts per source."""
        counts = _pending_counts()
        self._send_json({"status": "ok", "pending": counts})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Init DB on startup
    conn = _get_db()
    conn.close()

    server = HTTPServer(('0.0.0.0', PORT), SidecarHandler)
    server.daemon_threads = True

    sys.stderr.write(f"[sidecar] Listening on 0.0.0.0:{PORT}\n")
    sys.stderr.write(f"[sidecar] DB: {DB_PATH}\n")
    sys.stderr.write(f"[sidecar] Routes:\n")
    sys.stderr.write(f"  POST /<source>          — Ingest raw event (any path)\n")
    sys.stderr.write(f"  GET  /events?wait=true  — Drain all pending events\n")
    sys.stderr.write(f"  POST /respond           — Post a response\n")
    sys.stderr.write(f"  GET  /responses?wait=true — Pick up responses\n")
    sys.stderr.write(f"  GET  /health            — Status + pending counts\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("[sidecar] Shutting down.\n")
        server.shutdown()


if __name__ == '__main__':
    main()
