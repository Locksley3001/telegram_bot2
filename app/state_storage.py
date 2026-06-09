from __future__ import annotations

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
    ) -> None:
        self.data_dir = data_dir
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_key = supabase_key.strip()
        self.enabled = bool(enabled and self.supabase_url and self.supabase_key)
        self.state_table = state_table.strip() or "bot_state_files"
        self.versions_table = versions_table.strip() or "bot_state_file_versions"
        self.bootstrap_local = bootstrap_local
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.last_error = ""
        self.last_sync_at: Optional[datetime] = None
        self.connected = False

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
        self._upsert_remote(name, payload)
        self._insert_version(name, payload)

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "state_table": self.state_table,
            "versions_table": self.versions_table,
            "remote_first": True,
            "bootstrap_local": self.bootstrap_local,
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
                return payload
            return None
        except Exception as exc:
            self.last_error = f"Supabase load {name}: {exc}"
            self.connected = False
            LOGGER.warning(self.last_error)
            return None

    def _upsert_remote(self, name: str, payload: dict[str, Any]) -> None:
        url = f"{self.supabase_url}/rest/v1/{self.state_table}?on_conflict=name"
        body = [
            {
                "name": name,
                "payload": payload,
                "updated_at": datetime.now(timezone.utc).isoformat(),
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
            self.last_sync_at = datetime.now(timezone.utc)
        except Exception as exc:
            self.last_error = f"Supabase save {name}: {exc}"
            self.connected = False
            LOGGER.warning(self.last_error)

    def _insert_version(self, name: str, payload: dict[str, Any]) -> None:
        if not self.versions_table:
            return
        url = f"{self.supabase_url}/rest/v1/{self.versions_table}"
        body = [
            {
                "name": name,
                "payload": payload,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        try:
            self._request("POST", url, body, prefer="return=minimal")
        except Exception as exc:
            LOGGER.warning("Supabase version save %s: %s", name, exc)

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
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        backup_path = path.with_suffix(f"{path.suffix}.bak")
        temp_path.write_text(encoded, encoding="utf-8")
        if path.exists():
            backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        temp_path.replace(path)
