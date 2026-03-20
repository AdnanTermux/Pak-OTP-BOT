"""
database.py  —  Sigma Fetcher V10
Async SQLAlchemy + aiosqlite database layer.
"""
import os
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String, DateTime, BigInteger, Text, Index, select, func, delete
from sqlalchemy.sql import text

DB_FILE      = "bot_database.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_FILE}"

engine            = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base              = declarative_base()


# ── Models ────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id           = Column(Integer,    primary_key=True, autoincrement=True)
    user_id      = Column(BigInteger, unique=True, nullable=False)
    prefix       = Column(String(20), nullable=True)
    custom_limit = Column(Integer,    nullable=True)
    created_at   = Column(DateTime,   default=datetime.now)


class Number(Base):
    __tablename__  = "numbers"
    id             = Column(Integer,     primary_key=True, autoincrement=True)
    phone_number   = Column(String(20),  unique=True, nullable=False)
    category       = Column(String(100), nullable=False)
    status         = Column(String(20),  nullable=False, default="AVAILABLE")
    assigned_to    = Column(BigInteger,  nullable=True)
    assigned_at    = Column(DateTime,    nullable=True)
    last_otp       = Column(String(20),  nullable=True)
    last_msg       = Column(Text,        nullable=True)
    message_id     = Column(Integer,     nullable=True)
    retention_until= Column(DateTime,    nullable=True)
    created_at     = Column(DateTime,    default=datetime.now)
    __table_args__ = (Index("ix_numbers_status_assigned", "status", "assigned_to"),)


class History(Base):
    __tablename__ = "history"
    id           = Column(Integer,     primary_key=True, autoincrement=True)
    user_id      = Column(BigInteger,  nullable=False)
    phone_number = Column(String(20),  nullable=False)
    otp_code     = Column(String(20),  nullable=False)
    service      = Column(String(100), nullable=False)
    panel_name   = Column(String(100), nullable=True)
    timestamp    = Column(DateTime,    default=datetime.now)


class LogChat(Base):
    __tablename__ = "log_chats"
    id      = Column(Integer,    primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, unique=True, nullable=False)


# ── Init / Migration ─────────────────────────────────────────
async def _migrate():
    async with AsyncSessionLocal() as s:
        res  = await s.execute(text("PRAGMA table_info(panels)"))
        cols = [r[1] for r in res.fetchall()]
        for col, defval in [("uri","TEXT"),("token","TEXT"),("panel_type","TEXT DEFAULT 'login'")]:
            if col not in cols:
                try:
                    await s.execute(text(f"ALTER TABLE panels ADD COLUMN {col} {defval}"))
                except Exception: pass
        await s.commit()


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database ready.")
    await _migrate()


# ── Users ─────────────────────────────────────────────────────
async def add_user(user_id: int):
    async with AsyncSessionLocal() as s:
        if not await s.scalar(select(User).where(User.user_id == user_id)):
            s.add(User(user_id=user_id))
            await s.commit()

async def get_all_users() -> list:
    async with AsyncSessionLocal() as s:
        return [r[0] for r in await s.execute(select(User.user_id))]

async def get_user_stats(user_id: int) -> dict:
    async with AsyncSessionLocal() as s:
        total = await s.scalar(select(func.count(History.id)).where(History.user_id == user_id)) or 0
        return {"success": total, "total": total}

async def set_user_prefix(user_id: int, prefix):
    async with AsyncSessionLocal() as s:
        u = await s.scalar(select(User).where(User.user_id == user_id))
        if u: u.prefix = prefix; await s.commit()

async def get_user_prefix(user_id: int):
    async with AsyncSessionLocal() as s:
        return await s.scalar(select(User.prefix).where(User.user_id == user_id))

async def set_user_limit(user_id: int, limit):
    async with AsyncSessionLocal() as s:
        u = await s.scalar(select(User).where(User.user_id == user_id))
        if u: u.custom_limit = limit; await s.commit()

async def get_user_limit(user_id: int):
    async with AsyncSessionLocal() as s:
        return await s.scalar(select(User.custom_limit).where(User.user_id == user_id))


# ── Numbers ───────────────────────────────────────────────────
async def add_numbers_bulk(lines: list, category: str) -> int:
    added = 0
    async with AsyncSessionLocal() as s:
        for line in lines:
            num = line.strip().replace(" ","").replace("-","")
            if num and not await s.scalar(select(Number).where(Number.phone_number == num)):
                s.add(Number(phone_number=num, category=category, status="AVAILABLE"))
                added += 1
        await s.commit()
    return added

async def get_categories_summary() -> list:
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(Number.category, func.count(Number.id))
            .where(Number.status == "AVAILABLE")
            .group_by(Number.category)
        )).all()

async def delete_category(category: str) -> int:
    async with AsyncSessionLocal() as s:
        r = await s.execute(delete(Number).where(Number.category == category))
        await s.commit()
        return r.rowcount

async def check_prefix_availability(category: str, prefix: str) -> int:
    async with AsyncSessionLocal() as s:
        return await s.scalar(
            select(func.count(Number.id)).where(
                Number.category == category,
                Number.status   == "AVAILABLE",
                Number.phone_number.like(f"{prefix}%")
            )) or 0

async def request_number(user_id: int, category_hint: str = None):
    """
    Get the current active number for this user in the given category.
    Multi-service fix: only matches numbers in the specified category,
    not any active number regardless of category.
    """
    async with AsyncSessionLocal() as s:
        if category_hint and category_hint != "Check":
            # Check for existing number in THIS category only
            active = await s.scalar(
                select(Number).where(
                    Number.assigned_to == user_id,
                    Number.status.in_(["ASSIGNED","RETENTION"]),
                    Number.category == category_hint))
        else:
            # No hint — return any active number
            active = await s.scalar(
                select(Number).where(
                    Number.assigned_to == user_id,
                    Number.status.in_(["ASSIGNED","RETENTION"])))
        if active:
            return active.phone_number, active.category, "active"
        q = select(Number).where(Number.status == "AVAILABLE")
        if category_hint and category_hint != "Check":
            q = q.where(Number.category == category_hint)
        q = q.order_by(func.random()).limit(1)
        num = await s.scalar(q)
        if num:
            num.status = "ASSIGNED"; num.assigned_to = user_id; num.assigned_at = datetime.now()
            await s.commit()
            return num.phone_number, num.category, "new"
        return None, None, "unavailable"

async def request_numbers(user_id: int, category: str, count: int, message_id: int = None):
    """
    Assign `count` numbers of `category` to `user_id`.

    Multi-service fix: numbers already assigned to this user in OTHER
    categories are left untouched.  Only numbers matching THIS category
    are counted as "already assigned" and re-used.
    This allows a user to hold numbers from multiple services simultaneously.
    """
    assigned = []
    async with AsyncSessionLocal() as s:
        # Only re-use existing numbers that match THIS specific category
        existing = (await s.execute(
            select(Number).where(
                Number.assigned_to == user_id,
                Number.status.in_(["ASSIGNED","RETENTION"]),
                Number.category == category)   # ← category-scoped, not all active
        )).scalars().all()
        assigned.extend(existing)
        need = count - len(assigned)
        if need > 0:
            prefix = await s.scalar(select(User.prefix).where(User.user_id == user_id))
            q = select(Number).where(Number.category == category, Number.status == "AVAILABLE")
            if prefix: q = q.where(Number.phone_number.like(f"{prefix}%"))
            q = q.order_by(func.random()).limit(need)
            for num in (await s.execute(q)).scalars().all():
                num.status      = "ASSIGNED"
                num.assigned_to = user_id
                num.assigned_at = datetime.now()
                if message_id:
                    num.message_id = message_id
                assigned.append(num)
        if assigned:
            await s.commit()
            return [n.phone_number for n in assigned], category, "ok"
        return [], category, "unavailable"

async def get_active_numbers(user_id: int) -> list:
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(Number).where(Number.assigned_to == user_id, Number.status.in_(["ASSIGNED","RETENTION"]))
        )).scalars().all()

async def release_number(user_id: int):
    async with AsyncSessionLocal() as s:
        nums = (await s.execute(
            select(Number).where(Number.assigned_to == user_id, Number.status.in_(["ASSIGNED","RETENTION"]))
        )).scalars().all()
        if not nums: return False, None
        cat = nums[0].category
        for n in nums:
            n.status = "RETENTION"; n.retention_until = datetime.now()+timedelta(hours=1); n.assigned_to = None
        await s.commit()
        return True, cat

async def block_number(user_id: int):
    async with AsyncSessionLocal() as s:
        nums = (await s.execute(
            select(Number).where(Number.assigned_to == user_id, Number.status.in_(["ASSIGNED","RETENTION"]))
        )).scalars().all()
        if not nums: return False, None
        cat = nums[0].category
        for n in nums:
            n.status = "BLOCKED"; n.assigned_to = None; n.retention_until = None
        await s.commit()
        return True, cat

async def record_success(phone_number: str, otp_code: str):
    async with AsyncSessionLocal() as s:
        num = await s.scalar(select(Number).where(Number.phone_number == phone_number))
        if not num: return None, None, None
        cat, uid, mid = num.category, num.assigned_to, num.message_id
        s.add(History(user_id=uid, phone_number=phone_number, otp_code=otp_code, service=cat))
        num.status = "USED"; num.assigned_to = None; num.message_id = None
        await s.commit()
        return cat, uid, mid

async def update_message_id(phone_number: str, msg_id: int):
    async with AsyncSessionLocal() as s:
        num = await s.scalar(select(Number).where(Number.phone_number == phone_number))
        if num: num.message_id = msg_id; await s.commit()


# ── Stats ─────────────────────────────────────────────────────
async def get_stats() -> dict:
    async with AsyncSessionLocal() as s:
        return {k: await s.scalar(select(func.count(Number.id)).where(Number.status == v)) or 0
                for k, v in [("available","AVAILABLE"),("assigned","ASSIGNED"),
                              ("cooldown","RETENTION"),("used","USED"),("blocked","BLOCKED")]}

async def clean_cooldowns() -> int:
    async with AsyncSessionLocal() as s:
        expired = (await s.execute(
            select(Number).where(Number.status=="RETENTION", Number.retention_until<=datetime.now())
        )).scalars().all()
        for n in expired: n.status = "AVAILABLE"; n.retention_until = None
        await s.commit()
        return len(expired)


# ── Log Chats ─────────────────────────────────────────────────
async def add_log_chat(chat_id: int) -> bool:
    async with AsyncSessionLocal() as s:
        if not await s.scalar(select(LogChat).where(LogChat.chat_id == chat_id)):
            s.add(LogChat(chat_id=chat_id)); await s.commit(); return True
        return False

async def remove_log_chat(chat_id: int) -> bool:
    async with AsyncSessionLocal() as s:
        r = await s.execute(delete(LogChat).where(LogChat.chat_id == chat_id))
        await s.commit(); return r.rowcount > 0

async def get_all_log_chats() -> list:
    async with AsyncSessionLocal() as s:
        return [r[0] for r in await s.execute(select(LogChat.chat_id))]


# ── Service / Country ─────────────────────────────────────────
async def get_distinct_services() -> list:
    async with AsyncSessionLocal() as s:
        cats = [r[0] for r in await s.execute(
            select(Number.category).distinct().where(Number.status=="AVAILABLE"))]
    svcs = set()
    for c in cats:
        if " - " in c: svcs.add(c.split(" - ")[1].strip())
    return sorted(svcs)

async def get_countries_for_service(service: str) -> list:
    async with AsyncSessionLocal() as s:
        cats = [r[0] for r in await s.execute(
            select(Number.category).distinct().where(
                Number.category.like(f"% - {service}"), Number.status=="AVAILABLE"))]
    result = set()
    for c in cats:
        if " - " in c:
            fc = c.split(" - ")[0].strip()
            parts = fc.split(" ", 1)
            result.add((parts[0] if parts else "🌍", parts[1].strip() if len(parts)>1 else fc))
    return sorted(result, key=lambda x: x[1])
