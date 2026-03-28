from __future__ import annotations

import posixpath
import socket
import stat
from datetime import datetime
from pathlib import Path
from typing import Callable

import paramiko
from paramiko import AuthenticationException, SSHException
from paramiko.sftp_attr import SFTPAttributes

from .models import ConnectionProfile, RemoteEntry


class SSHConnectionError(RuntimeError):
    """Raised when SSH or SFTP operations fail in a user-visible way."""


class SSHClientWrapper:
    def __init__(self) -> None:
        self.profile: ConnectionProfile | None = None
        self.password: str = ""
        self.client: paramiko.SSHClient | None = None
        self.sftp: paramiko.SFTPClient | None = None

    def connect(self, profile: ConnectionProfile, password: str = "", passphrase: str = "") -> None:
        self.close()
        self.profile = profile
        self.password = password if profile.auth_type == "password" else passphrase
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            connect_kwargs: dict[str, object] = {
                "hostname": profile.host,
                "port": profile.port,
                "username": profile.username,
                "timeout": 10,
                "banner_timeout": 10,
                "auth_timeout": 10,
            }
            if profile.auth_type == "key":
                connect_kwargs["look_for_keys"] = False
                connect_kwargs["allow_agent"] = True
                if profile.key_path:
                    connect_kwargs["key_filename"] = profile.key_path
                if passphrase:
                    connect_kwargs["passphrase"] = passphrase
            else:
                connect_kwargs["password"] = password
                connect_kwargs["look_for_keys"] = False
                connect_kwargs["allow_agent"] = False

            client.connect(**connect_kwargs)
            self.client = client
            self.sftp = client.open_sftp()
        except AuthenticationException as exc:
            client.close()
            if profile.auth_type == "key":
                raise SSHConnectionError("Authentication failed. Check the SSH key, passphrase, or agent.") from exc
            raise SSHConnectionError("Authentication failed. Check username or password.") from exc
        except (socket.gaierror, TimeoutError, OSError) as exc:
            client.close()
            raise SSHConnectionError("Unable to reach the remote host.") from exc
        except SSHException as exc:
            client.close()
            raise SSHConnectionError(f"SSH error: {exc}") from exc

    def ensure_connected(self) -> None:
        if self.client is None or self.sftp is None or self.profile is None:
            raise SSHConnectionError("No active SSH session.")
        transport = self.client.get_transport()
        if transport is not None and transport.is_active():
            return
        if self.profile.auth_type == "key":
            self.connect(self.profile, passphrase=self.password)
        else:
            self.connect(self.profile, password=self.password)

    def close(self) -> None:
        if self.sftp is not None:
            self.sftp.close()
            self.sftp = None
        if self.client is not None:
            self.client.close()
            self.client = None

    def normalize(self, remote_path: str) -> str:
        try:
            return self._with_sftp_retry(lambda sftp: sftp.normalize(remote_path))
        except OSError as exc:
            raise SSHConnectionError(str(exc)) from exc

    def list_directory(self, remote_path: str) -> list[RemoteEntry]:
        try:
            attrs = self._with_sftp_retry(lambda sftp: sftp.listdir_attr(remote_path))
        except FileNotFoundError as exc:
            raise SSHConnectionError("Remote path does not exist.") from exc
        except PermissionError as exc:
            raise SSHConnectionError("Permission denied for that remote path.") from exc
        except OSError as exc:
            raise SSHConnectionError(str(exc)) from exc

        entries: list[RemoteEntry] = []
        for attr in attrs:
            entries.append(self._attr_to_entry(remote_path, attr))
        return entries

    def is_dir(self, remote_path: str) -> bool:
        try:
            mode = self._with_sftp_retry(lambda sftp: sftp.stat(remote_path).st_mode)
            return stat.S_ISDIR(mode)
        except OSError as exc:
            raise SSHConnectionError(str(exc)) from exc

    def walk_directory(self, remote_path: str) -> list[RemoteEntry]:
        self.ensure_connected()
        to_visit = [remote_path]
        collected: list[RemoteEntry] = []
        while to_visit:
            current = to_visit.pop()
            for entry in self.list_directory(current):
                collected.append(entry)
                if entry.is_dir:
                    to_visit.append(entry.path)
        return collected

    def download_file(
        self,
        remote_path: str,
        local_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        try:
            self._with_sftp_retry(lambda sftp: sftp.get(remote_path, local_path, callback=progress_callback))
        except FileNotFoundError as exc:
            raise SSHConnectionError("Remote file no longer exists.") from exc
        except PermissionError as exc:
            raise SSHConnectionError("Permission denied while downloading the remote file.") from exc
        except OSError as exc:
            raise SSHConnectionError(str(exc)) from exc

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        try:
            self._with_sftp_retry(lambda sftp: sftp.put(local_path, remote_path, callback=progress_callback))
        except FileNotFoundError as exc:
            raise SSHConnectionError("Local file no longer exists.") from exc
        except PermissionError as exc:
            raise SSHConnectionError("Permission denied while uploading the file.") from exc
        except OSError as exc:
            raise SSHConnectionError(str(exc)) from exc

    def upload_path(
        self,
        local_path: Path,
        remote_path: str,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> None:
        if local_path.is_dir():
            self._upload_directory(local_path, remote_path, progress_callback)
            return
        self.ensure_parent_dir(remote_path)
        self.upload_file(
            str(local_path),
            remote_path,
            progress_callback=(
                None
                if progress_callback is None
                else lambda sent, total, target=remote_path: progress_callback(target, int(sent), int(total))
            ),
        )

    def rename_path(self, remote_path: str, new_path: str) -> None:
        try:
            self._with_sftp_retry(lambda sftp: sftp.rename(remote_path, new_path))
        except FileNotFoundError as exc:
            raise SSHConnectionError("Remote file no longer exists.") from exc
        except PermissionError as exc:
            raise SSHConnectionError("Permission denied while renaming the remote item.") from exc
        except OSError as exc:
            raise SSHConnectionError(str(exc)) from exc

    def mkdir(self, remote_path: str) -> None:
        try:
            self._with_sftp_retry(lambda sftp: sftp.mkdir(remote_path))
        except PermissionError as exc:
            raise SSHConnectionError("Permission denied while creating the remote directory.") from exc
        except OSError as exc:
            raise SSHConnectionError(str(exc)) from exc

    def delete_path(self, remote_path: str) -> None:
        self.ensure_connected()
        if self.is_dir(remote_path):
            self._delete_directory(remote_path)
            return
        try:
            self._with_sftp_retry(lambda sftp: sftp.remove(remote_path))
        except FileNotFoundError as exc:
            raise SSHConnectionError("Remote file no longer exists.") from exc
        except PermissionError as exc:
            raise SSHConnectionError("Permission denied while deleting the remote file.") from exc
        except OSError as exc:
            raise SSHConnectionError(str(exc)) from exc

    @staticmethod
    def parent_path(remote_path: str) -> str:
        if remote_path in ("", "/"):
            return "/"
        parent = posixpath.dirname(remote_path.rstrip("/"))
        return parent or "/"

    def path_exists(self, remote_path: str) -> bool:
        try:
            self._with_sftp_retry(lambda sftp: sftp.stat(remote_path))
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise SSHConnectionError(str(exc)) from exc

    def ensure_parent_dir(self, remote_path: str) -> None:
        parent = self.parent_path(remote_path)
        self.ensure_dir(parent)

    def ensure_dir(self, remote_path: str) -> None:
        if remote_path in ("", "/"):
            return
        parts = [part for part in remote_path.split("/") if part]
        current = "/"
        for part in parts:
            current = posixpath.join(current, part)
            try:
                mode = self._with_sftp_retry(lambda sftp, path=current: sftp.stat(path).st_mode)
                if not stat.S_ISDIR(mode):
                    raise SSHConnectionError(f"Remote path exists and is not a directory: {current}")
            except FileNotFoundError:
                try:
                    self._with_sftp_retry(lambda sftp, path=current: sftp.mkdir(path))
                except OSError as exc:
                    raise SSHConnectionError(str(exc)) from exc
            except OSError as exc:
                raise SSHConnectionError(str(exc)) from exc

    def resolve_available_remote_path(self, desired_path: str) -> str:
        if not self.path_exists(desired_path):
            return desired_path
        parent = self.parent_path(desired_path)
        filename = posixpath.basename(desired_path)
        stem, suffix = self._split_name(filename)
        counter = 1
        while True:
            candidate_name = f"{stem} ({counter}){suffix}"
            candidate_path = posixpath.join(parent, candidate_name)
            if not self.path_exists(candidate_path):
                return candidate_path
            counter += 1

    def _delete_directory(self, remote_path: str) -> None:
        try:
            for entry in self.list_directory(remote_path):
                if entry.is_dir:
                    self._delete_directory(entry.path)
                else:
                    self._with_sftp_retry(lambda sftp, path=entry.path: sftp.remove(path))
            self._with_sftp_retry(lambda sftp: sftp.rmdir(remote_path))
        except FileNotFoundError as exc:
            raise SSHConnectionError("Remote directory no longer exists.") from exc
        except PermissionError as exc:
            raise SSHConnectionError("Permission denied while deleting the remote directory.") from exc
        except OSError as exc:
            raise SSHConnectionError(str(exc)) from exc

    def _upload_directory(
        self,
        local_root: Path,
        remote_root: str,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self.ensure_dir(remote_root)
        for child in local_root.rglob("*"):
            relative = child.relative_to(local_root).as_posix()
            target = posixpath.join(remote_root, relative)
            if child.is_dir():
                self.ensure_dir(target)
                continue
            self.ensure_parent_dir(target)
            self.upload_file(
                str(child),
                target,
                progress_callback=(
                    None
                    if progress_callback is None
                    else lambda sent, total, target_path=target: progress_callback(target_path, int(sent), int(total))
                ),
            )

    @staticmethod
    def _split_name(name: str) -> tuple[str, str]:
        if "." not in name or name.startswith(".") and name.count(".") == 1:
            return name, ""
        stem, suffix = name.rsplit(".", 1)
        return stem, f".{suffix}"

    def _attr_to_entry(self, parent: str, attr: SFTPAttributes) -> RemoteEntry:
        name = attr.filename
        path = posixpath.join(parent, name) if parent != "/" else f"/{name}"
        is_dir = stat.S_ISDIR(attr.st_mode)
        modified = datetime.fromtimestamp(attr.st_mtime)
        return RemoteEntry(
            name=name,
            path=path,
            is_dir=is_dir,
            size=attr.st_size,
            modified_time=modified,
            mode=attr.st_mode,
        )

    def _with_sftp_retry(self, operation: Callable[[paramiko.SFTPClient], object]) -> object:
        self.ensure_connected()
        assert self.sftp is not None
        try:
            return operation(self.sftp)
        except (EOFError, SSHException):
            if self.profile is None:
                raise SSHConnectionError("Connection dropped and no profile is available to reconnect.") from None
            if self.profile.auth_type == "key":
                self.connect(self.profile, passphrase=self.password)
            else:
                self.connect(self.profile, password=self.password)
            assert self.sftp is not None
            try:
                return operation(self.sftp)
            except (EOFError, SSHException) as exc:
                raise SSHConnectionError("Connection dropped while talking to the remote server.") from exc
