from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class ConnectionProfile:
    name: str
    host: str
    username: str
    port: int = 22
    download_dir: str = ""
    last_path: str = "."
    auth_type: str = "password"
    key_path: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "host": self.host,
            "username": self.username,
            "port": self.port,
            "download_dir": self.download_dir,
            "last_path": self.last_path,
            "auth_type": self.auth_type,
            "key_path": self.key_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ConnectionProfile":
        return cls(
            name=str(data.get("name", "")).strip(),
            host=str(data.get("host", "")).strip(),
            username=str(data.get("username", "")).strip(),
            port=int(data.get("port", 22) or 22),
            download_dir=str(data.get("download_dir", "")).strip(),
            last_path=str(data.get("last_path", ".") or ".").strip(),
            auth_type=str(data.get("auth_type", "password") or "password").strip(),
            key_path=str(data.get("key_path", "") or "").strip(),
        )


@dataclass(slots=True)
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    size: int
    modified_time: datetime
    mode: int

    @property
    def type_label(self) -> str:
        return "dir" if self.is_dir else "file"


@dataclass(slots=True)
class LocalEntry:
    name: str
    path: Path
    is_dir: bool
    size: int
    modified_time: datetime

    @property
    def type_label(self) -> str:
        return "dir" if self.is_dir else "file"


@dataclass(slots=True)
class DownloadItem:
    remote_path: str
    remote_name: str
    is_dir: bool


@dataclass(slots=True)
class PreparedDownload:
    remote_path: str
    remote_name: str
    destination: Path
    is_dir: bool


@dataclass(slots=True)
class TransferTask:
    direction: str
    source: str
    destination: str
    size: int
    label: str


@dataclass(slots=True)
class AppState:
    selected_profile: str | None = None
    show_hidden: bool = False
    sort_mode: str = "name"
    multi_select: set[str] = field(default_factory=set)
    local_filter: str = ""
    remote_filter: str = ""
