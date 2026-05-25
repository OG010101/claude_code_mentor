import json
import logging
import os
import sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "/tmp/mentor.db")

LEARNING_PATH = [
    ("установка",        "Установка и первый запуск"),
    ("базовые_команды",  "Базовые команды"),
    ("claude_md",        "CLAUDE.md — память проекта"),
    ("скиллы",           "Скиллы"),
    ("github",           "GitHub"),
    ("mcp",              "MCP-серверы"),
    ("хуки",             "Хуки"),
    ("продвинутые",      "Продвинутые техники"),
]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id   INTEGER PRIMARY KEY,
                level     TEXT,
                os        TEXT,
                goal      TEXT,
                name      TEXT,
                setup_done INTEGER DEFAULT 0,
                topics    TEXT DEFAULT '[]',
                updated_at TEXT
            )
        """)
        try:
            c.execute("ALTER TABLE profiles ADD COLUMN name TEXT")
        except Exception:
            pass
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL,
                role      TEXT NOT NULL,
                content   TEXT NOT NULL,
                ts        TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_msg_user ON messages(user_id, id)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS discoveries (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                content    TEXT NOT NULL,
                source     TEXT,
                created_at TEXT NOT NULL
            )
        """)
    logger.info(f"DB ready at {DB_PATH}")


def _serialize(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [item.model_dump() if hasattr(item, "model_dump") else item for item in content]
    if hasattr(content, "model_dump"):
        return content.model_dump()
    return content


# ── Profiles ──────────────────────────────────────────────────────────────────

def load_profile(user_id: int) -> dict:
    with _conn() as c:
        row = c.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return {}
    d = dict(row)
    d["topics"] = json.loads(d.get("topics") or "[]")
    d["setup_done"] = bool(d["setup_done"])
    return d


def save_profile(user_id: int, **kwargs) -> None:
    profile = load_profile(user_id)
    profile.update(kwargs)
    topics = json.dumps(profile.get("topics", []))
    with _conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO profiles
               (user_id, level, os, goal, name, setup_done, topics, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                profile.get("level"),
                profile.get("os"),
                profile.get("goal"),
                profile.get("name"),
                int(profile.get("setup_done", False)),
                topics,
                datetime.now().isoformat(),
            ),
        )


def clear_profile(user_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM profiles WHERE user_id=?", (user_id,))


def add_topic(user_id: int, topic: str) -> None:
    profile = load_profile(user_id)
    topics = profile.get("topics", [])
    if topic not in topics:
        topics.append(topic)
        save_profile(user_id, topics=topics)


# ── History ───────────────────────────────────────────────────────────────────

def load_history(user_id: int, limit: int = 20) -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    result = []
    for row in reversed(rows):
        result.append({"role": row["role"], "content": json.loads(row["content"])})
    return result


def append_message(user_id: int, role: str, content) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO messages (user_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (user_id, role, json.dumps(_serialize(content)), datetime.now().isoformat()),
        )


def clear_history(user_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM messages WHERE user_id=?", (user_id,))


# ── Discoveries ───────────────────────────────────────────────────────────────

def add_discovery(content: str, source: str = "") -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO discoveries (content, source, created_at) VALUES (?, ?, ?)",
            (content, source, datetime.now().isoformat()),
        )


def get_recent_discoveries(limit: int = 15) -> list[str]:
    with _conn() as c:
        rows = c.execute(
            "SELECT content FROM discoveries ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [row["content"] for row in rows]


def trim_discoveries(keep: int = 40) -> None:
    with _conn() as c:
        c.execute(
            "DELETE FROM discoveries WHERE id NOT IN "
            "(SELECT id FROM discoveries ORDER BY created_at DESC LIMIT ?)",
            (keep,),
        )


def discoveries_count() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0]
