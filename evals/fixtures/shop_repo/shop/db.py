"""Tiny database wrapper."""

import sqlite3


class Database:
    """Thin wrapper that owns one sqlite3 connection."""

    def __init__(self, url: str):
        self.url = url
        self._conn = None

    def connect(self):
        """Open the connection on first use."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.url.replace("sqlite:///", ""))
        return self._conn

    def execute(self, sql: str, params: tuple = ()):
        """Run one statement and commit."""
        conn = self.connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur
