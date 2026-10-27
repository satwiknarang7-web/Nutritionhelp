"""Data access for one signed-in user, backed by Neon Postgres.

Every method runs in `db.user_tx`, i.e. as the restricted `nutritrack_user`
role with row level security scoped to this user, in addition to the explicit
user_id filters below.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from app import db

LOG_COLUMNS = (
    "id, logged_on, meal, food_name, food_ref, source, quantity, unit, grams, nutrients, photo_id, created_at"
)
FOOD_COLUMNS = "id, name, category, per_100g, servings"
PROFILE_FIELDS = (
    "display_name", "sex", "date_of_birth", "height_cm", "weight_kg",
    "activity_level", "weight_goal", "diet_type", "timezone",
)


class Repository(Protocol):
    user_id: str

    def get_profile(self) -> dict | None: ...
    def update_profile(self, data: dict) -> dict: ...
    def get_goals(self) -> dict[str, float]: ...
    def set_goals(self, goals: dict[str, float]) -> None: ...
    def list_logs(self, start: date, end: date) -> list[dict]: ...
    def logged_days(self, start: date, end: date) -> dict[str, float]: ...
    def get_log(self, log_id: str) -> dict | None: ...
    def add_log(self, entry: dict) -> dict: ...
    def update_log(self, log_id: str, fields: dict) -> dict | None: ...
    def delete_log(self, log_id: str) -> bool: ...
    def list_custom_foods(self) -> list[dict]: ...
    def get_custom_food(self, food_id: str) -> dict | None: ...
    def add_custom_food(self, food: dict) -> dict: ...
    def delete_custom_food(self, food_id: str) -> bool: ...
    def get_api_key(self, provider: str) -> dict | None: ...
    def set_api_key(self, provider: str, encrypted_key: str, key_hint: str) -> None: ...
    def delete_api_key(self, provider: str) -> None: ...
    def save_photo(self, data: bytes, mime_type: str) -> str: ...
    def get_photo(self, photo_id: str) -> tuple[bytes, str] | None: ...
    def owns_photo(self, photo_id: str) -> bool: ...
    def close(self) -> None: ...


def _plain(value: Any) -> Any:
    """JSON-friendly values so rows can be cached and rendered directly."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, memoryview):
        return bytes(value)
    return value


def _row(row: dict | None) -> dict | None:
    return {k: _plain(v) for k, v in row.items()} if row else None


class NeonRepository:
    def __init__(self, user_id: str):
        self.user_id = str(user_id)

    def close(self) -> None:
        pass  # connections are borrowed from the pool per call

    def _one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        with db.user_tx(self.user_id) as conn:
            return _row(conn.execute(sql, params).fetchone())

    def _all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with db.user_tx(self.user_id) as conn:
            return [_row(r) for r in conn.execute(sql, params).fetchall()]

    # --- profile ---
    def get_profile(self) -> dict | None:
        row = self._one("select * from profiles where user_id = %s", (self.user_id,))
        if row:
            row["id"] = row["user_id"]
        return row

    def update_profile(self, data: dict) -> dict:
        fields = {k: data.get(k) for k in PROFILE_FIELDS if k in data}
        if not fields:
            return self.get_profile() or {"id": self.user_id}
        cols = ", ".join(fields)
        placeholders = ", ".join(f"%({k})s" for k in fields)
        updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
        row = self._one(
            f"insert into profiles (user_id, {cols}) values (%(user_id)s, {placeholders}) "
            f"on conflict (user_id) do update set {updates}, updated_at = now() returning *",
            {**fields, "user_id": self.user_id},
        )
        row["id"] = row["user_id"]
        return row

    # --- goals ---
    def get_goals(self) -> dict[str, float]:
        rows = self._all("select nutrient, target from nutrient_goals where user_id = %s", (self.user_id,))
        return {r["nutrient"]: float(r["target"]) for r in rows}

    def set_goals(self, goals: dict[str, float]) -> None:
        if not goals:
            return
        with db.user_tx(self.user_id) as conn, conn.cursor() as cur:
            cur.executemany(
                "insert into nutrient_goals (user_id, nutrient, target) values (%s, %s, %s) "
                "on conflict (user_id, nutrient) do update set target = excluded.target, updated_at = now()",
                [(self.user_id, k, float(v)) for k, v in goals.items()],
            )

    # --- diary ---
    def list_logs(self, start: date, end: date) -> list[dict]:
        return self._all(
            f"select {LOG_COLUMNS} from food_logs where user_id = %s and logged_on between %s and %s "
            "order by logged_on, created_at",
            (self.user_id, start, end),
        )

    def logged_days(self, start: date, end: date) -> dict[str, float]:
        """Energy per logged day - a cheap query for the week strip, calendar and streaks."""
        rows = self._all(
            "select logged_on, sum(coalesce((nutrients->>'energy')::float8, 0)) as energy "
            "from food_logs where user_id = %s and logged_on between %s and %s "
            "and coalesce(food_ref, '') <> 'water' group by logged_on",
            (self.user_id, start, end),
        )
        return {r["logged_on"]: float(r["energy"] or 0) for r in rows}

    def get_log(self, log_id: str) -> dict | None:
        return self._one(f"select {LOG_COLUMNS} from food_logs where id = %s and user_id = %s", (log_id, self.user_id))

    def add_log(self, entry: dict) -> dict:
        return self._one(
            "insert into food_logs (user_id, logged_on, meal, food_name, food_ref, source, quantity, unit, grams, "
            "nutrients, photo_id) values (%(user_id)s, %(logged_on)s, %(meal)s, %(food_name)s, %(food_ref)s, "
            f"%(source)s, %(quantity)s, %(unit)s, %(grams)s, %(nutrients)s, %(photo_id)s) returning {LOG_COLUMNS}",
            {
                "user_id": self.user_id,
                "logged_on": entry["logged_on"],
                "meal": entry["meal"],
                "food_name": entry["food_name"],
                "food_ref": entry.get("food_ref"),
                "source": entry.get("source", "manual"),
                "quantity": entry["quantity"],
                "unit": entry["unit"],
                "grams": entry["grams"],
                "nutrients": Jsonb(entry.get("nutrients") or {}),
                "photo_id": entry.get("photo_id"),
            },
        )

    def update_log(self, log_id: str, fields: dict) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in ("meal", "quantity", "unit", "grams", "nutrients")}
        if not allowed:
            return self.get_log(log_id)
        if "nutrients" in allowed:
            allowed["nutrients"] = Jsonb(allowed["nutrients"])
        sets = ", ".join(f"{k} = %({k})s" for k in allowed)
        return self._one(
            f"update food_logs set {sets} where id = %(id)s and user_id = %(user_id)s returning {LOG_COLUMNS}",
            {**allowed, "id": log_id, "user_id": self.user_id},
        )

    def delete_log(self, log_id: str) -> bool:
        row = self._one("delete from food_logs where id = %s and user_id = %s returning id", (log_id, self.user_id))
        return row is not None

    # --- custom foods ---
    def list_custom_foods(self) -> list[dict]:
        return self._all(f"select {FOOD_COLUMNS} from custom_foods where user_id = %s order by name", (self.user_id,))

    def get_custom_food(self, food_id: str) -> dict | None:
        return self._one(
            f"select {FOOD_COLUMNS} from custom_foods where id = %s and user_id = %s", (food_id, self.user_id)
        )

    def add_custom_food(self, food: dict) -> dict:
        return self._one(
            "insert into custom_foods (user_id, name, category, per_100g, servings) values (%s, %s, %s, %s, %s) "
            f"returning {FOOD_COLUMNS}",
            (
                self.user_id,
                food["name"],
                food.get("category", "My foods"),
                Jsonb(food["per_100g"]),
                Jsonb(food.get("servings", [])),
            ),
        )

    def delete_custom_food(self, food_id: str) -> bool:
        row = self._one("delete from custom_foods where id = %s and user_id = %s returning id", (food_id, self.user_id))
        return row is not None

    # --- user API keys (ciphertext only) ---
    def get_api_key(self, provider: str) -> dict | None:
        return self._one(
            "select encrypted_key, key_hint, updated_at from user_api_keys where user_id = %s and provider = %s",
            (self.user_id, provider),
        )

    def set_api_key(self, provider: str, encrypted_key: str, key_hint: str) -> None:
        self._one(
            "insert into user_api_keys (user_id, provider, encrypted_key, key_hint) values (%s, %s, %s, %s) "
            "on conflict (user_id, provider) do update set encrypted_key = excluded.encrypted_key, "
            "key_hint = excluded.key_hint, updated_at = now() returning provider",
            (self.user_id, provider, encrypted_key, key_hint),
        )

    def delete_api_key(self, provider: str) -> None:
        self._one(
            "delete from user_api_keys where user_id = %s and provider = %s returning provider",
            (self.user_id, provider),
        )

    # --- photos ---
    def save_photo(self, data: bytes, mime_type: str) -> str:
        row = self._one(
            "insert into meal_photos (user_id, content, mime_type) values (%s, %s, %s) returning id",
            (self.user_id, data, mime_type),
        )
        return row["id"]

    def get_photo(self, photo_id: str) -> tuple[bytes, str] | None:
        row = self._one(
            "select content, mime_type from meal_photos where id = %s and user_id = %s", (photo_id, self.user_id)
        )
        return (row["content"], row["mime_type"]) if row else None

    def owns_photo(self, photo_id: str) -> bool:
        return self._one("select 1 as ok from meal_photos where id = %s and user_id = %s", (photo_id, self.user_id)) is not None
