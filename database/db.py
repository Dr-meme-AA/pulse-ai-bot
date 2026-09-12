import asyncpg
from datetime import datetime, timezone
from typing import Optional


_pool: Optional[asyncpg.Pool] = None


# ==================================================
# DATABASE SCHEMA
# ==================================================

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pulse_users (
    telegram_user_id BIGINT PRIMARY KEY,

    plan TEXT NOT NULL DEFAULT 'FREE',

    questions_used INTEGER NOT NULL DEFAULT 0,

    subscription_expiry TIMESTAMPTZ,

    telegram_payment_charge_id TEXT,

    saved_latitude DOUBLE PRECISION,
    saved_longitude DOUBLE PRECISION,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS medication_reminders (
    id BIGSERIAL PRIMARY KEY,

    telegram_user_id BIGINT NOT NULL
        REFERENCES pulse_users(telegram_user_id)
        ON DELETE CASCADE,

    medicine_name TEXT NOT NULL,

    medicine_strength TEXT,

    schedule_text TEXT NOT NULL,

    interval_hours NUMERIC,

    next_due_at TIMESTAMPTZ NOT NULL,

    end_at TIMESTAMPTZ,

    active BOOLEAN NOT NULL DEFAULT TRUE,

    last_taken_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


CREATE INDEX IF NOT EXISTS idx_medication_reminders_due
ON medication_reminders (
    active,
    next_due_at
);


CREATE INDEX IF NOT EXISTS idx_medication_reminders_user
ON medication_reminders (
    telegram_user_id
);
"""


# ==================================================
# DATABASE CONNECTION
# ==================================================

async def init_db(database_url: str):
    global _pool

    if _pool is None:

        _pool = await asyncpg.create_pool(
            database_url,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )

    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def close_db():
    global _pool

    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:

    if _pool is None:
        raise RuntimeError(
            "Database has not been initialized."
        )

    return _pool


# ==================================================
# USER ACCOUNT
# ==================================================

async def ensure_user(
    telegram_user_id: int
):

    async with pool().acquire() as conn:

        await conn.execute(
            """
            INSERT INTO pulse_users (
                telegram_user_id
            )
            VALUES ($1)

            ON CONFLICT (
                telegram_user_id
            )
            DO NOTHING
            """,
            telegram_user_id,
        )


async def get_user(
    telegram_user_id: int
):

    await ensure_user(
        telegram_user_id
    )

    async with pool().acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT
                telegram_user_id,
                plan,
                questions_used,
                subscription_expiry,
                telegram_payment_charge_id,
                saved_latitude,
                saved_longitude

            FROM pulse_users

            WHERE telegram_user_id = $1
            """,
            telegram_user_id,
        )


# ==================================================
# PLAN / SUBSCRIPTION
# ==================================================

async def get_plan(
    telegram_user_id: int
):

    user = await get_user(
        telegram_user_id
    )

    plan = user["plan"]

    expiry = user[
        "subscription_expiry"
    ]


    if (
        plan == "PLUS"
        and expiry is not None
    ):

        now = datetime.now(
            timezone.utc
        )

        if expiry <= now:

            await set_free_plan(
                telegram_user_id,
                reset_usage=True,
            )

            return "FREE"


    return plan


async def activate_plus(
    telegram_user_id: int,
    subscription_expiry,
    telegram_payment_charge_id: str,
):

    await ensure_user(
        telegram_user_id
    )


    async with pool().acquire() as conn:

        await conn.execute(
            """
            UPDATE pulse_users

            SET
                plan = 'PLUS',
                questions_used = 0,
                subscription_expiry = $2,
                telegram_payment_charge_id = $3,
                updated_at = NOW()

            WHERE telegram_user_id = $1
            """,
            telegram_user_id,
            subscription_expiry,
            telegram_payment_charge_id,
        )


async def set_free_plan(
    telegram_user_id: int,
    reset_usage: bool = False,
):

    await ensure_user(
        telegram_user_id
    )


    async with pool().acquire() as conn:

        if reset_usage:

            await conn.execute(
                """
                UPDATE pulse_users

                SET
                    plan = 'FREE',
                    questions_used = 0,
                    subscription_expiry = NULL,
                    updated_at = NOW()

                WHERE telegram_user_id = $1
                """,
                telegram_user_id,
            )

        else:

            await conn.execute(
                """
                UPDATE pulse_users

                SET
                    plan = 'FREE',
                    subscription_expiry = NULL,
                    updated_at = NOW()

                WHERE telegram_user_id = $1
                """,
                telegram_user_id,
            )


# ==================================================
# QUESTION USAGE
# ==================================================

async def get_usage(
    telegram_user_id: int
):

    user = await get_user(
        telegram_user_id
    )

    plan = await get_plan(
        telegram_user_id
    )

    return {
        "plan": plan,
        "questions_used": (
            user["questions_used"]
        ),
    }


async def increment_usage(
    telegram_user_id: int
):

    await ensure_user(
        telegram_user_id
    )


    async with pool().acquire() as conn:

        return await conn.fetchval(
            """
            UPDATE pulse_users

            SET
                questions_used =
                    questions_used + 1,
                updated_at = NOW()

            WHERE telegram_user_id = $1

            RETURNING questions_used
            """,
            telegram_user_id,
        )


async def reset_usage(
    telegram_user_id: int
):

    await ensure_user(
        telegram_user_id
    )


    async with pool().acquire() as conn:

        await conn.execute(
            """
            UPDATE pulse_users

            SET
                questions_used = 0,
                updated_at = NOW()

            WHERE telegram_user_id = $1
            """,
            telegram_user_id,
        )


# ==================================================
# LOCATION / CARE FINDER
# ==================================================

async def save_location(
    telegram_user_id: int,
    latitude: float,
    longitude: float,
):

    await ensure_user(
        telegram_user_id
    )


    async with pool().acquire() as conn:

        await conn.execute(
            """
            UPDATE pulse_users

            SET
                saved_latitude = $2,
                saved_longitude = $3,
                updated_at = NOW()

            WHERE telegram_user_id = $1
            """,
            telegram_user_id,
            latitude,
            longitude,
        )


async def get_location(
    telegram_user_id: int
):

    user = await get_user(
        telegram_user_id
    )


    if (
        user["saved_latitude"] is None
        or user["saved_longitude"] is None
    ):

        return None


    return {
        "latitude": (
            user["saved_latitude"]
        ),
        "longitude": (
            user["saved_longitude"]
        ),
    }


async def clear_location(
    telegram_user_id: int
):

    await ensure_user(
        telegram_user_id
    )


    async with pool().acquire() as conn:

        await conn.execute(
            """
            UPDATE pulse_users

            SET
                saved_latitude = NULL,
                saved_longitude = NULL,
                updated_at = NOW()

            WHERE telegram_user_id = $1
            """,
            telegram_user_id,
        )


# ==================================================
# MEDICATION REMINDERS
# ==================================================

async def create_medication_reminder(
    telegram_user_id: int,
    medicine_name: str,
    medicine_strength,
    schedule_text: str,
    next_due_at,
    interval_hours=None,
    end_at=None,
):

    await ensure_user(
        telegram_user_id
    )


    async with pool().acquire() as conn:

        reminder_id = await conn.fetchval(
            """
            INSERT INTO medication_reminders (
                telegram_user_id,
                medicine_name,
                medicine_strength,
                schedule_text,
                interval_hours,
                next_due_at,
                end_at
            )

            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7
            )

            RETURNING id
            """,
            telegram_user_id,
            medicine_name,
            medicine_strength,
            schedule_text,
            interval_hours,
            next_due_at,
            end_at,
        )


    return reminder_id


async def list_active_reminders(
    telegram_user_id: int
):

    async with pool().acquire() as conn:

        return await conn.fetch(
            """
            SELECT
                id,
                medicine_name,
                medicine_strength,
                schedule_text,
                interval_hours,
                next_due_at,
                end_at

            FROM medication_reminders

            WHERE
                telegram_user_id = $1
                AND active = TRUE

            ORDER BY
                next_due_at ASC
            """,
            telegram_user_id,
        )


async def get_due_reminders(
    limit: int = 100
):

    async with pool().acquire() as conn:

        return await conn.fetch(
            """
            SELECT
                id,
                telegram_user_id,
                medicine_name,
                medicine_strength,
                schedule_text,
                interval_hours,
                next_due_at,
                end_at

            FROM medication_reminders

            WHERE
                active = TRUE
                AND next_due_at <= NOW()
                AND (
                    end_at IS NULL
                    OR end_at >= NOW()
                )

            ORDER BY
                next_due_at ASC

            LIMIT $1
            """,
            limit,
        )


async def advance_reminder(
    reminder_id: int,
    next_due_at,
):

    async with pool().acquire() as conn:

        await conn.execute(
            """
            UPDATE medication_reminders

            SET
                next_due_at = $2,
                updated_at = NOW()

            WHERE id = $1
            """,
            reminder_id,
            next_due_at,
        )


async def mark_reminder_taken(
    reminder_id: int
):

    async with pool().acquire() as conn:

        await conn.execute(
            """
            UPDATE medication_reminders

            SET
                last_taken_at = NOW(),
                updated_at = NOW()

            WHERE id = $1
            """,
            reminder_id,
        )


async def stop_reminder(
    reminder_id: int,
    telegram_user_id: int,
):

    async with pool().acquire() as conn:

        await conn.execute(
            """
            UPDATE medication_reminders

            SET
                active = FALSE,
                updated_at = NOW()

            WHERE
                id = $1
                AND telegram_user_id = $2
            """,
            reminder_id,
            telegram_user_id,
        )
