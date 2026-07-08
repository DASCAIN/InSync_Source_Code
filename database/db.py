import os
import sqlite3
from typing import Optional


class Database:
    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./lead_generation.db")
        self.connection: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_db_path(self) -> str:
        if self.database_url.startswith("sqlite:///"):
            return self.database_url.replace("sqlite:///", "")
        if self.database_url.startswith("sqlite://"):
            return self.database_url.replace("sqlite://", "")
        return self.database_url

    def _init_db(self) -> None:
        db_path = self._get_db_path()
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_calls (
              conversation_id TEXT PRIMARY KEY,
              lead_id TEXT,
              user_id TEXT,
              started_at TIMESTAMP,
              ended_at TIMESTAMP,
              status TEXT
            );
            """
        )
        self.connection.commit()

    def get_connection(self) -> sqlite3.Connection:
        if self.connection is None:
            self._init_db()
        return self.connection


_database: Optional[Database] = None


def get_database() -> sqlite3.Connection:
    global _database
    if _database is None:
        _database = Database()
    return _database.get_connection()
