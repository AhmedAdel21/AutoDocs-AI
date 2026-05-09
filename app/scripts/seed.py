"""Realistic seed data for AutoDocs.

Run: uv run python -m app.scripts.seed
"""

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models.audit_log import AuditAction
from app.security import hash_password


BATCH_SIZE = 1000


async def seed_users(db: AsyncSession, count: int) -> list[uuid.UUID]:
    """Insert users in batches. Returns list of inserted user IDs."""
    print(f"Seeding {count} users...")
    user_ids: list[uuid.UUID] = []

    # One bcrypt hash, reused. Real seeds shouldn't bother computing many hashes — bcrypt is slow.
    shared_hash = hash_password("seedpassword123")

    for batch_start in range(0, count, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, count)
        rows = []
        for i in range(batch_start, batch_end):
            uid = uuid.uuid4()
            user_ids.append(uid)
            # Spread created_at over the last 90 days for realistic time distribution
            offset_seconds = random.randint(0, 90 * 24 * 3600)
            created_at = datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)
            role = random.choice(
                ["engineer", "engineer", "engineer", "lead", "admin"]
            )  # weighted
            # ~5% soft-deleted to make the WHERE deleted_at IS NULL filter realistic
            deleted_at = (
                created_at + timedelta(days=random.randint(1, 30))
                if random.random() < 0.05
                else None
            )

            rows.append(
                {
                    "id": str(uid),
                    "email": f"user_{batch_start + i}@valeo.com",
                    "full_name": f"Engineer {batch_start + i}",
                    "hashed_password": shared_hash,
                    "role": role,
                    "version": 1,
                    "is_active": deleted_at is None,
                    "created_at": created_at,
                    "updated_at": created_at,
                    "deleted_at": deleted_at,
                }
            )

        # Bulk insert via raw SQL for speed (ORM is slow for this)
        await db.execute(
            text(
                """
                INSERT INTO users (id, email, full_name, hashed_password, role, version, is_active, created_at, updated_at, deleted_at)
                VALUES (:id, :email, :full_name, :hashed_password, :role, :version, :is_active, :created_at, :updated_at, :deleted_at)
            """
            ),
            rows,
        )
        await db.commit()
        print(f"  ...{batch_end}/{count}")

    print(f"Done seeding users. Total: {count}")
    return user_ids


async def seed_audit_logs(
    db: AsyncSession, user_ids: list[uuid.UUID], count: int
) -> None:
    """Insert audit logs in batches. Each log targets a random user."""
    print(f"Seeding {count} audit logs...")

    actions = [
        AuditAction.USER_CREATED,
        AuditAction.USER_UPDATED,
        AuditAction.USER_LOGIN_SUCCESS,
        AuditAction.USER_LOGIN_FAILED,
        AuditAction.USER_LOGOUT,
    ]

    for batch_start in range(0, count, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, count)
        rows = []
        for _ in range(batch_start, batch_end):
            target_id = random.choice(user_ids)
            actor_id = (
                random.choice(user_ids) if random.random() > 0.1 else None
            )  # 10% system actions
            offset_seconds = random.randint(0, 90 * 24 * 3600)
            created_at = datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)

            rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "target_user_id": str(target_id),
                    "actor_user_id": str(actor_id) if actor_id else None,
                    "action": random.choice(actions).value,
                    "details": '{"seed": true}',  # JSON string for raw SQL
                    "ip_address": f"10.0.{random.randint(0,255)}.{random.randint(0,255)}",
                    "user_agent": "Mozilla/5.0 (Seed)",
                    "created_at": created_at,
                }
            )

        await db.execute(
            text(
                """
                INSERT INTO audit_logs (id, target_user_id, actor_user_id, action, details, ip_address, user_agent, created_at)
                VALUES (:id, :target_user_id, :actor_user_id, :action, CAST(:details AS jsonb), :ip_address, :user_agent, :created_at)
            """
            ),
            rows,
        )
        await db.commit()
        print(f"  ...{batch_end}/{count}")

    print(f"Done seeding audit logs. Total: {count}")


async def main() -> None:
    async with SessionLocal() as db:
        # Skip if already seeded substantially (idempotent enough)
        result = await db.execute(text("SELECT COUNT(*) FROM users"))
        existing = result.scalar() or 0
        # if existing > 1000:
        #     print(f"Users already seeded ({existing}), skipping user seed")
        #     existing_ids_result = await db.execute(
        #         text("SELECT id FROM users LIMIT 10000")
        #     )
        #     user_ids = [row[0] for row in existing_ids_result.fetchall()]
        # else:
        user_ids = await seed_users(db, 10000)

        result = await db.execute(text("SELECT COUNT(*) FROM audit_logs"))
        existing_logs = result.scalar() or 0
        # if existing_logs > 100000:
        #     print(f"Audit logs already seeded ({existing_logs}), skipping")
        # else:
        await seed_audit_logs(db, user_ids, 200000)


if __name__ == "__main__":
    asyncio.run(main())
