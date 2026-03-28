from __future__ import annotations

from dataclasses import dataclass

from .config import PROFILES_FILE, load_json_file, write_json_file
from .models import ConnectionProfile
from .secrets import SecretResult, SecretStore


@dataclass(slots=True)
class SaveProfileResult:
    profile: ConnectionProfile
    secret_result: SecretResult


class ProfileManager:
    def __init__(self, secret_store: SecretStore | None = None) -> None:
        self.secret_store = secret_store or SecretStore()

    def load_profiles(self) -> list[ConnectionProfile]:
        payload = load_json_file(PROFILES_FILE, {"version": 1, "profiles": []})
        raw_profiles = payload.get("profiles", [])
        profiles = [
            ConnectionProfile.from_dict(item)
            for item in raw_profiles
            if isinstance(item, dict)
        ]
        return sorted(profiles, key=lambda profile: profile.name.lower())

    def save_profiles(self, profiles: list[ConnectionProfile]) -> None:
        payload = {
            "version": 1,
            "profiles": [profile.to_dict() for profile in profiles],
        }
        write_json_file(PROFILES_FILE, payload)

    def upsert_profile(
        self,
        profile: ConnectionProfile,
        password: str = "",
        original_name: str | None = None,
    ) -> SaveProfileResult:
        profiles = self.load_profiles()
        existing_index = next(
            (
                index
                for index, current in enumerate(profiles)
                if current.name == (original_name or profile.name)
            ),
            None,
        )
        if existing_index is None:
            profiles.append(profile)
        else:
            profiles[existing_index] = profile
        self.save_profiles(sorted(profiles, key=lambda item: item.name.lower()))

        if original_name and original_name != profile.name:
            self.secret_store.delete_password(original_name)

        secret_result = SecretResult(ok=True)
        if password:
            secret_result = self.secret_store.set_password(profile.name, password)
        return SaveProfileResult(profile=profile, secret_result=secret_result)

    def delete_profile(self, profile_name: str) -> SecretResult:
        profiles = [profile for profile in self.load_profiles() if profile.name != profile_name]
        self.save_profiles(profiles)
        return self.secret_store.delete_password(profile_name)

    def get_password(self, profile_name: str) -> str:
        return self.secret_store.get_password(profile_name)
