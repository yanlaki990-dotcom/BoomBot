import os
from datetime import datetime, timedelta, timezone
import aiosqlite
import config

# База будет создаваться в папке /app/data, которую мы настроили в Dockerfile
DATA_DIR = os.getenv("DATA_DIR", "/app/data")
DB_PATH = os.path.join(DATA_DIR, "bot.db")


def _convert_dates(row):
    """Вспомогательная функция для преобразования строковых дат SQLite в datetime"""
    if row is None:
        return None
    d = dict(row)
    for field in ["first_seen", "started_at", "expires_at", "created_at"]:
        if field in d and d[field]:
            try:
                d[field] = datetime.fromisoformat(d[field])
            except (ValueError, TypeError):
                pass
    return d


async def init():
    # Создаём папку, если её нет
    os.makedirs(DATA_DIR, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                vendor_username TEXT,
                password TEXT,
                first_seen TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT,
                source TEXT NOT NULL DEFAULT 'manual',
                FOREIGN KEY (tg_id) REFERENCES users(tg_id)
            );
            CREATE TABLE IF NOT EXISTS pending_vendor (
                tg_id INTEGER PRIMARY KEY,
                plan TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        await db.commit()


async def upsert_user(tg_id: int, username: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username",
            (tg_id, username)
        )
        await db.commit()


async def get_user(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)) as cur:
            row = await cur.fetchone()
            return _convert_dates(row)


async def get_active_subscription(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions "
            "WHERE tg_id=? AND (expires_at IS NULL OR expires_at > datetime('now')) "
            "ORDER BY (expires_at IS NULL) DESC, expires_at DESC LIMIT 1",
            (tg_id,)
        ) as cur:
            row = await cur.fetchone()
            return _convert_dates(row)


async def grant_subscription(tg_id: int, plan: str, source: str = "manual"):
    name, days, _paid = config.PLANS[plan]
    now = datetime.now(timezone.utc)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions "
            "WHERE tg_id=? AND (expires_at IS NULL OR expires_at > datetime('now')) "
            "ORDER BY (expires_at IS NULL) DESC, expires_at DESC LIMIT 1",
            (tg_id,)
        ) as cur:
            current_row = await cur.fetchone()
            current = _convert_dates(current_row)

        if current and current["expires_at"] is None:
            return current  # уже навсегда

        if days is None:
            expires_at = None
        else:
            base = now
            if current and current["expires_at"]:
                cur_exp = current["expires_at"]
                # Если дата из базы пришла без таймзоны, добавляем UTC
                if cur_exp.tzinfo is None:
                    cur_exp = cur_exp.replace(tzinfo=timezone.utc)
                if cur_exp > now:
                    base = cur_exp
            expires_at = (base + timedelta(days=days)).isoformat()

        await db.execute(
            "INSERT INTO subscriptions (tg_id, plan, expires_at, source) VALUES (?, ?, ?, ?)",
            (tg_id, plan, expires_at, source)
        )
        await db.commit()

        async with db.execute("SELECT * FROM subscriptions WHERE id = last_insert_rowid()") as cur:
            row = await cur.fetchone()
            return _convert_dates(row)


async def get_all_tg_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tg_id FROM users") as cur:
            rows = await cur.fetchall()
        return [r[0] for r in rows]


async def stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT count(*) FROM users") as cur:
            users = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT count(DISTINCT tg_id) FROM subscriptions "
            "WHERE expires_at IS NULL OR expires_at > datetime('now')"
        ) as cur:
            active = (await cur.fetchone())[0]
    return users, active, 0, 0  # 0,0 — заглушка для старых ключей


async def set_pending(tg_id: int, plan: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO pending_vendor (tg_id, plan) VALUES (?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET plan=excluded.plan, created_at=datetime('now')",
            (tg_id, plan)
        )
        await db.commit()


async def get_pending(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM pending_vendor WHERE tg_id=?", (tg_id,)) as cur:
            row = await cur.fetchone()
            return _convert_dates(row)


async def clear_pending(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pending_vendor WHERE tg_id=?", (tg_id,))
        await db.commit()
