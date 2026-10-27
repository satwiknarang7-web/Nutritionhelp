"""Data access for one signed-in user.

`SupabaseRepository` talks to Supabase with the *user's* access token, so every
query is checked by Postgres row level security in addition to the explicit
user_id filters here.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Protocol

from postgrest import SyncPostgrestClient
from storage3 import SyncStorageClient

from app.config import Settings

log = logging.getLogger(__name__)

PHOTO_BUCKET = "meal-photos"
PAGE_SIZE = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


LOG_COLUMNS = "id,logged_on,meal,food_name,food_ref,source,quantity,unit,grams,nutrients,photo_path,created_at"


class Repository(Protocol):
    user_id: str

    def get_profile(self) -> dict | None: ...
    def update_profile(self, data: dict) -> dict: ...
    def get_goals(self) -> dict[str, float]: ...
    def set_goals(self, goals: dict[str, float]) -> None: ...
    def list_logs(self, start: date, end: date) -> list[dict]: ...
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
    def upload_photo(self, name: str, data: bytes, content_type: str) -> str | None: ...
    def photo_url(self, path: str) -> str | None: ...
    def close(self) -> None: ...


class SupabaseRepository:
    def __init__(self, settings: Settings, user_id: str, access_token: str):
        self.user_id = user_id
        headers = {
            "apikey": settings.supabase_anon_key,
            "Authorization": f"Bearer {access_token}",
        }
        self._db = SyncPostgrestClient(
            f"{settings.supabase_url}/rest/v1",
            headers={**headers, "Accept": "application/json", "Content-Type": "application/json"},
            timeout=15,
        )
        self._storage_headers = headers
        self._settings = settings
        self._storage: SyncStorageClient | None = None

    def close(self) -> None:
        for client in (self._db, self._storage):
            try:
                if client is not None:
                    client.session.close()
            except Exception:  # noqa: BLE001
                pass

    def _t(self, table: str):
        return self._db.from_(table)

    # --- profile ---
    def get_profile(self) -> dict | None:
        res = self._t("profiles").select("*").eq("id", self.user_id).limit(1).execute()
        return res.data[0] if res.data else None

    def update_profile(self, data: dict) -> dict:
        payload = {**data, "id": self.user_id, "updated_at": _now()}
        res = self._t("profiles").upsert(payload, on_conflict="id").execute()
        return res.data[0] if res.data else payload

    # --- goals ---
    def get_goals(self) -> dict[str, float]:
        res = self._t("nutrient_goals").select("nutrient,target").eq("user_id", self.user_id).execute()
        return {row["nutrient"]: float(row["target"]) for row in res.data or []}

    def set_goals(self, goals: dict[str, float]) -> None:
        rows = [
            {"user_id": self.user_id, "nutrient": k, "target": v, "updated_at": _now()}
            for k, v in goals.items()
        ]
        if rows:
            self._t("nutrient_goals").upsert(rows, on_conflict="user_id,nutrient").execute()

    # --- diary ---
    def list_logs(self, start: date, end: date) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while True:
            res = (
                self._t("food_logs")
                .select(LOG_COLUMNS)
                .eq("user_id", self.user_id)
                .gte("logged_on", start.isoformat())
                .lte("logged_on", end.isoformat())
                .order("logged_on")
                .order("created_at")
                .range(offset, offset + PAGE_SIZE - 1)
                .execute()
            )
            batch = res.data or []
            rows.extend(batch)
            if len(batch) < PAGE_SIZE:
                return rows
            offset += PAGE_SIZE

    def get_log(self, log_id: str) -> dict | None:
        res = (
            self._t("food_logs")
            .select(LOG_COLUMNS)
            .eq("id", log_id)
            .eq("user_id", self.user_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def add_log(self, entry: dict) -> dict:
        res = self._t("food_logs").insert({**entry, "user_id": self.user_id}).execute()
        return res.data[0]

    def update_log(self, log_id: str, fields: dict) -> dict | None:
        res = (
            self._t("food_logs")
            .update(fields)
            .eq("id", log_id)
            .eq("user_id", self.user_id)
            .execute()
        )
        return res.data[0] if res.data else None

    def delete_log(self, log_id: str) -> bool:
        res = self._t("food_logs").delete().eq("id", log_id).eq("user_id", self.user_id).execute()
        return bool(res.data)

    # --- custom foods ---
    def list_custom_foods(self) -> list[dict]:
        res = (
            self._t("custom_foods")
            .select("id,name,category,per_100g,servings")
            .eq("user_id", self.user_id)
            .order("name")
            .execute()
        )
        return res.data or []

    def get_custom_food(self, food_id: str) -> dict | None:
        res = (
            self._t("custom_foods")
            .select("id,name,category,per_100g,servings")
            .eq("id", food_id)
            .eq("user_id", self.user_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def add_custom_food(self, food: dict) -> dict:
        res = self._t("custom_foods").insert({**food, "user_id": self.user_id}).execute()
        return res.data[0]

    def delete_custom_food(self, food_id: str) -> bool:
        res = self._t("custom_foods").delete().eq("id", food_id).eq("user_id", self.user_id).execute()
        return bool(res.data)

    # --- user API keys (ciphertext only) ---
    def get_api_key(self, provider: str) -> dict | None:
        res = (
            self._t("user_api_keys")
            .select("encrypted_key,key_hint,updated_at")
            .eq("user_id", self.user_id)
            .eq("provider", provider)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def set_api_key(self, provider: str, encrypted_key: str, key_hint: str) -> None:
        self._t("user_api_keys").upsert(
            {
                "user_id": self.user_id,
                "provider": provider,
                "encrypted_key": encrypted_key,
                "key_hint": key_hint,
                "updated_at": _now(),
            },
            on_conflict="user_id,provider",
        ).execute()

    def delete_api_key(self, provider: str) -> None:
        self._t("user_api_keys").delete().eq("user_id", self.user_id).eq("provider", provider).execute()

    # --- photos ---
    @property
    def storage(self) -> SyncStorageClient:
        if self._storage is None:
            self._storage = SyncStorageClient(
                f"{self._settings.supabase_url}/storage/v1/", self._storage_headers
            )
        return self._storage

    def upload_photo(self, name: str, data: bytes, content_type: str) -> str | None:
        path = f"{self.user_id}/{name}"
        try:
            self.storage.from_(PHOTO_BUCKET).upload(
                path, data, {"content-type": content_type, "upsert": "true"}
            )
            return path
        except Exception:  # noqa: BLE001 - photo storage is best-effort
            log.warning("photo upload failed", exc_info=True)
            return None

    def photo_url(self, path: str) -> str | None:
        try:
            res: Any = self.storage.from_(PHOTO_BUCKET).create_signed_url(path, 3600)
            return res.get("signedURL") or res.get("signedUrl")
        except Exception:  # noqa: BLE001
            return None
