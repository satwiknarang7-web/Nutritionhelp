"""In-memory stand-in for SupabaseRepository used by tests and local previews."""

from __future__ import annotations

import copy
import uuid
from datetime import date, datetime, timezone


class InMemoryRepository:
    def __init__(self, user_id: str = "00000000-0000-0000-0000-000000000001"):
        self.user_id = user_id
        self.profile: dict = {"id": user_id, "display_name": "Asha", "timezone": "Asia/Kolkata"}
        self.goals: dict[str, float] = {}
        self.logs: dict[str, dict] = {}
        self.custom: dict[str, dict] = {}
        self.keys: dict[str, dict] = {}
        self.photos: dict[str, bytes] = {}

    def close(self) -> None:
        pass

    def get_profile(self):
        return copy.deepcopy(self.profile)

    def update_profile(self, data):
        self.profile.update(data)
        return copy.deepcopy(self.profile)

    def get_goals(self):
        return dict(self.goals)

    def set_goals(self, goals):
        self.goals.update(goals)

    def list_logs(self, start: date, end: date):
        rows = [r for r in self.logs.values() if start.isoformat() <= r["logged_on"] <= end.isoformat()]
        return copy.deepcopy(sorted(rows, key=lambda r: (r["logged_on"], r["created_at"])))

    def get_log(self, log_id):
        return copy.deepcopy(self.logs.get(log_id))

    def add_log(self, entry):
        row = {
            "id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "photo_path": None,
            **entry,
            "user_id": self.user_id,
        }
        self.logs[row["id"]] = row
        return copy.deepcopy(row)

    def update_log(self, log_id, fields):
        if log_id not in self.logs:
            return None
        self.logs[log_id].update(fields)
        return copy.deepcopy(self.logs[log_id])

    def delete_log(self, log_id):
        return self.logs.pop(log_id, None) is not None

    def list_custom_foods(self):
        return copy.deepcopy(sorted(self.custom.values(), key=lambda f: f["name"]))

    def get_custom_food(self, food_id):
        return copy.deepcopy(self.custom.get(food_id))

    def add_custom_food(self, food):
        row = {"id": str(uuid.uuid4()), "category": "My foods", **food}
        self.custom[row["id"]] = row
        return copy.deepcopy(row)

    def delete_custom_food(self, food_id):
        return self.custom.pop(food_id, None) is not None

    def get_api_key(self, provider):
        return copy.deepcopy(self.keys.get(provider))

    def set_api_key(self, provider, encrypted_key, key_hint):
        self.keys[provider] = {"encrypted_key": encrypted_key, "key_hint": key_hint}

    def delete_api_key(self, provider):
        self.keys.pop(provider, None)

    def upload_photo(self, name, data, content_type):
        path = f"{self.user_id}/{name}"
        self.photos[path] = data
        return path

    def photo_url(self, path):
        return f"https://example.test/{path}" if path in self.photos else None
