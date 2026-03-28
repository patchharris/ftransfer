from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .models import PreparedDownload
from .ssh_client import SSHClientWrapper


class DownloadManager:
    def __init__(self, ssh_client: SSHClientWrapper) -> None:
        self.ssh_client = ssh_client

    def download_items(
        self,
        items: list[PreparedDownload],
        progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        for item in items:
            if item.is_dir:
                self._download_directory(item.remote_path, item.destination, progress)
                continue
            item.destination.parent.mkdir(parents=True, exist_ok=True)
            self.ssh_client.download_file(
                item.remote_path,
                str(item.destination),
                progress_callback=(
                    None
                    if progress is None
                    else lambda done, total, remote=item.remote_path: progress(remote, int(done), int(total))
                ),
            )

    def _download_directory(
        self,
        remote_root: str,
        local_root: Path,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        local_root.mkdir(parents=True, exist_ok=True)
        entries = self.ssh_client.walk_directory(remote_root)
        for entry in entries:
            relative = entry.path[len(remote_root) :].lstrip("/")
            destination = local_root / relative
            if entry.is_dir:
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.ssh_client.download_file(
                entry.path,
                str(destination),
                progress_callback=(
                    None
                    if progress is None
                    else lambda done, total, remote=entry.path: progress(remote, int(done), int(total))
                ),
            )
