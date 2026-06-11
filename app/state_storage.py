from __future__ import annotations

import copy
import hashlib
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)


class StateStorage:
    def __init__(
        self,
        *,
        data_dir: Path,
        supabase_url: str = "",
        supabase_key: str = "",
        enabled: bool = True,
        state_table: str = "bot_state_files",
        versions_table: str = "bot_state_file_versions",
        bootstrap_local: bool = False,
        timeout_seconds: float = 4.0,
        remote_save_interval_seconds: float = 60.0,
        versioning_enabled: bool = False,
        version_interval_seconds: float = 3600.0,
    ) -> None:
        self.data_dir = data_dir
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_key = supabase_key.strip()
        self.enabled = bool(enabled and self.supabase_url and self.supabase_key)
        self.state_table = state_table.strip() or "bot_state_files"
        self.versions_table = versions_table.strip() or "bot_state_file_versions"
        self.bootstrap_local = bootstrap_local
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.remote_save_interval_seconds = max(0.0, remote_save_interval_seconds)
        self.versioning_enabled = versioning_enabled
        self.version_interval_seconds = max(0.0, version_interval_seconds)
        self.last_error = ""
        self.last_sync_at: Optional[datetime] = None
        self.connected = False
        self.pending_remote_writes: set[str] = set()
        self.skipped_remote_saves = 0
        self.skipped_version_saves = 0
        self._remote_hashes: dict[str, str] = {}
        self._last_remote_save_at: dict[str, datetime] = {}
        self._last_version_save_at: dict[str, datetime] = {}
        self._pending_payloads: dict[str, tuple[dict[str, Any], str]] = {}

    def load_json(self, name: str, path: Path) -> Optional[dict[str, Any]]:
        if self.enabled:
            remote = self._load_remote(name)
            if remote is not None:
                self._write_local(path, remote)
                return remote
            if not self.last_error and not self.bootstrap_local:
                return None

        local = self._load_local(path)
        if local is not None and self.enabled and self.bootstrap_local and not self.last_error:
            self.save_json(name, path, local)
        return local

    def save_json(self, name: str, path: Path, payload: dict[str, Any]) -> None:
        self._write_local(path, payload)
        if not self.enabled:
            return
        payload_hash = self._payload_hash(payload)
        if payload_hash == self._remote_hashes.get(name):
            self.skipped_remote_saves += 1
            self.pending_remote_writes.discard(name)
            self._pending_payloads.pop(name, None)
            return
        now = datetime.now(timezone.utc)
        if not self._remote_save_due(name, now):
            self.skipped_remote_saves += 1
            self.pending_remote_writes.add(name)
            self._pending_payloads[name] = (copy.deepcopy(payload), payload_hash)
            return
        if self._upsert_remote(name, payload, payload_hash=payload_hash, now=now):
            self.pending_remote_writes.discard(name)
            self._pending_payloads.pop(name, None)
            self._insert_version(name, payload, now=now)
        else:
            self.pending_remote_writes.add(name)
            self._pending_payloads[name] = (copy.deepcopy(payload), payload_hash)

    def flush_pending(self) -> None:
        if not self.enabled or not self._pending_payloads:
            return
        now = datetime.now(timezone.utc)
        for name, (payload, payload_hash) in list(self._pending_payloads.items()):
            if payload_hash == self._remote_hashes.get(name):
                self.pending_remote_writes.discard(name)
                self._pending_payloads.pop(name, None)
                continue
            if not self._remote_save_due(name, now):
                continue
            if self._upsert_remote(name, payload, payload_hash=payload_hash, now=now):
                self.pending_remote_writes.discard(name)
                self._pending_payloads.pop(name, None)
                self._insert_version(name, payload, now=now)

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "state_table": self.state_table,
            "versions_table": self.versions_table,
            "remote_first": True,
            "bootstrap_local": self.bootstrap_local,
            "remote_save_interval_seconds": self.remote_save_interval_seconds,
            "versioning_enabled": self.versioning_enabled,
            "version_interval_seconds": self.version_interval_seconds,
            "pending_remote_writes": sorted(self.pending_remote_writes),
            "skipped_remote_saves": self.skipped_remote_saves,
            "skipped_version_saves": self.skipped_version_saves,
            "last_error": self.last_error,
            "last_sync_at": self.last_sync_at,
        }

    def _load_remote(self, name: str) -> Optional[dict[str, Any]]:
        query_name = urllib.parse.quote(name, safe="")
        url = (
            f"{self.supabase_url}/rest/v1/{self.state_table}"
            f"?name=eq.{query_name}&select=payload,updated_at&limit=1"
        )
        try:
            data = self._request("GET", url)
            if not isinstance(data, list) or not data:
                self.last_error = ""
                self.connected = True
                self.last_sync_at = datetime.now(timezone.utc)
                return None
            payload = data[0].get("payload")
            if isinstance(payload, dict):
                self.last_error = ""
                self.connected = True
                self.last_sync_at = datetime.now(timezone.utc)
                self._remote_hashes[name] = self._payload_hash(payload)
                return payload
            return None
        except Exception as exc:
            self.last_error = f"Supabase load {name}: {exc}"
            self.connected = False
            LOGGER.warning(self.last_error)
            return None

    def _upsert_remote(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        payload_hash: str,
        now: datetime,
    ) -> bool:
        url = f"{self.supabase_url}/rest/v1/{self.state_table}?on_conflict=name"
        body = [
            {
                "name": name,
                "payload": payload,
                "updated_at": now.isoformat(),
            }
        ]
        try:
            self._request(
                "POST",
                url,
                body,
                prefer="resolution=merge-duplicates,return=minimal",
            )
            self.last_error = ""
            self.connected = True
            self.last_sync_at = now
            self._remote_hashes[name] = payload_hash
            self._last_remote_save_at[name] = now
            return True
        except Exception as exc:
            self.last_error = f"Supabase save {name}: {exc}"
            self.connected = False
            self._last_remote_save_at[name] = now
            LOGGER.warning(self.last_error)
            return False

    def _insert_version(self, name: str, payload: dict[str, Any], *, now: datetime) -> None:
        if not self.versioning_enabled or not self.versions_table:
            self.skipped_version_saves += 1
            return
        if not self._version_save_due(name, now):
            self.skipped_version_saves += 1
            return
        url = f"{self.supabase_url}/rest/v1/{self.versions_table}"
        body = [
            {
                "name": name,
                "payload": payload,
                "created_at": now.isoformat(),
            }
        ]
        try:
            self._request("POST", url, body, prefer="return=minimal")
            self._last_version_save_at[name] = now
        except Exception as exc:
            LOGGER.warning("Supabase version save %s: %s", name, exc)

    def _remote_save_due(self, name: str, now: datetime) -> bool:
        last_save = self._last_remote_save_at.get(name)
        if last_save is None:
            return True
        elapsed = (now - last_save).total_seconds()
        return elapsed >= self.remote_save_interval_seconds

    def _version_save_due(self, name: str, now: datetime) -> bool:
        last_save = self._last_version_save_at.get(name)
        if last_save is None:
            return True
        elapsed = (now - last_save).total_seconds()
        return elapsed >= self.version_interval_seconds

    def _request(
        self,
        method: str,
        url: str,
        body: Optional[list[dict[str, Any]]] = None,
        *,
        prefer: str = "",
    ) -> Any:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer

        request = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

        if not raw:
            return None
        return json.loads(raw)

    @staticmethod
    def _load_local(path: Path) -> Optional[dict[str, Any]]:
        for candidate in (path, path.with_suffix(f"{path.suffix}.bak")):
            if not candidate.exists():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
            except Exception:
                LOGGER.exception("No se pudo cargar %s", candidate)
        return None

    @staticmethod
    def _write_local(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        previous = path.read_text(encoding="utf-8") if path.exists() else None
        if previous == encoded:
            return
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        backup_path = path.with_suffix(f"{path.suffix}.bak")
        temp_path.write_text(encoded, encoding="utf-8")
        if previous is not None:
            backup_path.write_text(previous, encoding="utf-8")
        temp_path.replace(path)

    @staticmethod
    def _payload_hash(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
