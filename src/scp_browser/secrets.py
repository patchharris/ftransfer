from __future__ import annotations

from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError, NoKeyringError


SERVICE_NAME = "scp-browser-tui"


@dataclass(slots=True)
class SecretResult:
    ok: bool
    message: str = ""


class SecretStore:
    """Wrap keyring access so secrets handling can be replaced later."""

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self.service_name = service_name

    def get_password(self, profile_name: str) -> str:
        try:
            return keyring.get_password(self.service_name, profile_name) or ""
        except (KeyringError, NoKeyringError):
            return ""

    def set_password(self, profile_name: str, password: str) -> SecretResult:
        if not password:
            return SecretResult(ok=True)
        try:
            keyring.set_password(self.service_name, profile_name, password)
            return SecretResult(ok=True)
        except (KeyringError, NoKeyringError) as exc:
            return SecretResult(ok=False, message=str(exc))

    def delete_password(self, profile_name: str) -> SecretResult:
        try:
            existing = keyring.get_password(self.service_name, profile_name)
            if existing is None:
                return SecretResult(ok=True)
            keyring.delete_password(self.service_name, profile_name)
            return SecretResult(ok=True)
        except (KeyringError, NoKeyringError) as exc:
            return SecretResult(ok=False, message=str(exc))
