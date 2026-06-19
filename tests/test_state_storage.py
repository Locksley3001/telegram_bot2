from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.state_storage import StateStorage


class RecordingStorage(StateStorage):
    def __init__(self, data_dir: Path, **overrides: object) -> None:
        options = {
            "data_dir": data_dir,
            "supabase_url": "https://example.supabase.co",
            "supabase_key": "service-role",
            "enabled": True,
        }
        options.update(overrides)
        super().__init__(**options)
        self.requests: list[tuple[str, str, Optional[list[dict[str, Any]]], str]] = []

    def _request(
        self,
        method: str,
        url: str,
        body: Optional[list[dict[str, Any]]] = None,
        *,
        prefer: str = "",
    ) -> Any:
        self.requests.append((method, url, body, prefer))
        if method == "GET":
            return []
        return None


class FailingSaveStorage(RecordingStorage):
    def _request(
        self,
        method: str,
        url: str,
        body: Optional[list[dict[str, Any]]] = None,
        *,
        prefer: str = "",
    ) -> Any:
        self.requests.append((method, url, body, prefer))
        if method == "GET":
            return []
        raise TimeoutError("remote unavailable")


class StateStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_skips_remote_save_when_payload_did_not_change(self) -> None:
        storage = RecordingStorage(self.data_dir)
        path = self.data_dir / "state.json"

        storage.save_json("state.json", path, {"value": 1})
        storage.save_json("state.json", path, {"value": 1})

        posts = [request for request in storage.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(storage.skipped_remote_saves, 1)

    def test_throttles_changed_remote_saves_per_file(self) -> None:
        storage = RecordingStorage(self.data_dir, remote_save_interval_seconds=60)
        path = self.data_dir / "state.json"

        storage.save_json("state.json", path, {"value": 1})
        storage.save_json("state.json", path, {"value": 2})

        posts = [request for request in storage.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(storage.pending_remote_writes, {"state.json"})

        storage.flush_pending()
        posts = [request for request in storage.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 1)

        storage._last_remote_save_at["state.json"] = datetime.now(timezone.utc) - timedelta(seconds=61)
        storage.flush_pending()

        posts = [request for request in storage.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertEqual(storage.pending_remote_writes, set())

    def test_force_remote_save_bypasses_throttle(self) -> None:
        storage = RecordingStorage(self.data_dir, remote_save_interval_seconds=60)
        path = self.data_dir / "broker_trades.json"

        storage.save_json("broker_trades.json", path, {"trades": [1]})
        storage.save_json("broker_trades.json", path, {"trades": [1, 2]}, force_remote=True)

        posts = [request for request in storage.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertEqual(storage.pending_remote_writes, set())

    def test_version_table_is_opt_in(self) -> None:
        storage = RecordingStorage(self.data_dir, versioning_enabled=True)
        path = self.data_dir / "state.json"

        storage.save_json("state.json", path, {"value": 1})

        posts = [request for request in storage.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertIn("/bot_state_files", posts[0][1])
        self.assertIn("/bot_state_file_versions", posts[1][1])

    def test_failed_remote_save_is_throttled_before_retry(self) -> None:
        storage = FailingSaveStorage(self.data_dir, remote_save_interval_seconds=60)
        path = self.data_dir / "state.json"

        storage.save_json("state.json", path, {"value": 1})
        storage.save_json("state.json", path, {"value": 2})
        storage.flush_pending()

        posts = [request for request in storage.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(storage.pending_remote_writes, {"state.json"})

        storage._last_remote_save_at["state.json"] = datetime.now(timezone.utc) - timedelta(seconds=61)
        storage.flush_pending()

        posts = [request for request in storage.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 2)


if __name__ == "__main__":
    unittest.main()
