"""
SQLite database layer for the check-in bot.
"""

import sqlite3
import json
import os
from typing import Optional


class Database:
    def __init__(self, path: str = "data/sites.db"):
        self.path = path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self):
        os.makedirs(os.path.dirname(self.path) if os.path.dirname(self.path) else ".", exist_ok=True)
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sites (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    name           TEXT NOT NULL,
                    url            TEXT NOT NULL,
                    auth_type      TEXT NOT NULL,  -- 'cookie' or 'password'
                    session_cookie TEXT,
                    api_user       TEXT,
                    username       TEXT,
                    password       TEXT,
                    last_verify    TEXT,           -- 'ok' | 'failed' | NULL
                    last_checkin   TEXT,           -- datetime string | NULL
                    last_status    TEXT            -- 'ok' | 'failed' | NULL
                )
            """)
            conn.commit()

    def add_site(self, site: dict, last_verify: str = None) -> int:
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO sites
                  (name, url, auth_type, session_cookie, api_user, username, password, last_verify)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                site["name"], site["url"], site["auth_type"],
                site.get("session_cookie"), site.get("api_user"),
                site.get("username"), site.get("password"),
                last_verify,
            ))
            conn.commit()
            return cur.lastrowid

    def list_sites(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM sites ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def get_site(self, site_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return dict(row) if row else None

    def delete_site(self, site_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
            conn.commit()

    def update_verify(self, site_id: int, status: str):
        with self._conn() as conn:
            conn.execute("UPDATE sites SET last_verify = ? WHERE id = ?", (status, site_id))
            conn.commit()

    def update_checkin(self, site_id: int, success: bool, timestamp: str = None):
        with self._conn() as conn:
            if success and timestamp:
                conn.execute(
                    "UPDATE sites SET last_checkin = ?, last_status = ? WHERE id = ?",
                    (timestamp, "ok", site_id)
                )
            else:
                # Only update status, preserve last successful checkin time
                conn.execute(
                    "UPDATE sites SET last_status = ? WHERE id = ?",
                    ("failed", site_id)
                )
            conn.commit()

    def update_cookie(self, site_id: int, session_cookie: str, api_user: str = None):
        """Update cookie after a fresh password-based login."""
        with self._conn() as conn:
            if api_user:
                conn.execute(
                    "UPDATE sites SET session_cookie = ?, api_user = ? WHERE id = ?",
                    (session_cookie, api_user, site_id)
                )
            else:
                conn.execute(
                    "UPDATE sites SET session_cookie = ? WHERE id = ?",
                    (session_cookie, site_id)
                )
            conn.commit()
