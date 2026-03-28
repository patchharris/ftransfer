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

    def _account_name(self, profile_name: str, secret_type: str) -> str:
        return f"{profile_name}:{secret_type}"

    def get_secret(self, profile_name: str, secret_type: str = "password") -> str:
        try:
            return keyring.get_password(self.service_name, self._account_name(profile_name, secret_type)) or ""
        except (KeyringError, NoKeyringError):
            return ""

    def set_secret(self, profile_name: str, secret: str, secret_type: str = "password") -> SecretResult:
        if not secret:
            return SecretResult(ok=True)
        try:
            keyring.set_password(self.service_name, self._account_name(profile_name, secret_type), secret)
            return SecretResult(ok=True)
        except (KeyringError, NoKeyringError) as exc:
            return SecretResult(ok=False, message=str(exc))

    def delete_secret(self, profile_name: str, secret_type: str = "password") -> SecretResult:
        try:
            account_name = self._account_name(profile_name, secret_type)
            existing = keyring.get_password(self.service_name, account_name)
            if existing is None:
                return SecretResult(ok=True)
            keyring.delete_password(self.service_name, account_name)
            return SecretResult(ok=True)
        except (KeyringError, NoKeyringError) as exc:
            return SecretResult(ok=False, message=str(exc))

    def get_password(self, profile_name: str) -> str:
        return self.get_secret(profile_name, "password")

    def set_password(self, profile_name: str, password: str) -> SecretResult:
        return self.set_secret(profile_name, password, "password")

    def delete_password(self, profile_name: str) -> SecretResult:
        return self.delete_secret(profile_name, "password")
