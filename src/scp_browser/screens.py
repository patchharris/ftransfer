from __future__ import annotations

import asyncio
import posixpath
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Markdown, ProgressBar, Static

from .models import AppState, ConnectionProfile, LocalEntry, RemoteEntry, TransferTask
from .profile_manager import ProfileManager
from .ssh_client import SSHClientWrapper, SSHConnectionError


def format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    current = float(size)
    for unit in units:
        if current < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(current)} {unit}"
            return f"{current:.1f} {unit}"
        current /= 1024
    return f"{size} B"


def format_entry(entry: RemoteEntry, marked: bool = False) -> str:
    flag = "*" if marked else " "
    entry_type = "[DIR]" if entry.is_dir else "     "
    modified = entry.modified_time.strftime("%Y-%m-%d %H:%M")
    size = "-" if entry.is_dir else format_size(entry.size)
    return f"{flag} {entry_type} {entry.name:<40.40} {size:>10}  {modified}"


def format_local_entry(entry: LocalEntry) -> str:
    entry_type = "[DIR]" if entry.is_dir else "     "
    modified = entry.modified_time.strftime("%Y-%m-%d %H:%M")
    size = "-" if entry.is_dir else format_size(entry.size)
    return f"  {entry_type} {entry.name:<40.40} {size:>10}  {modified}"


CHANGELOG_PATH = Path(__file__).resolve().parents[2] / "CHANGELOG.md"


class AboutScreen(Screen[None]):
    BINDINGS = [
        Binding("f1", "close_about", "Back"),
        Binding("escape", "close_about", "Back"),
        Binding("q", "close_about", "Back"),
    ]

    def compose(self) -> ComposeResult:
        try:
            changelog = CHANGELOG_PATH.read_text(encoding="utf-8")
        except OSError:
            changelog = "CHANGELOG.md could not be loaded."

        yield Header(show_clock=False)
        yield Static(
            "SCP Browser TUI | Made by [link=http://patchharris.info]Patchharris[/link]",
            classes="brand-line",
        )
        with VerticalScroll(classes="about-box"):
            yield Markdown(changelog)
        yield Static("Press F1, Esc, or q to return.", classes="brand-line")
        yield Footer()

    def action_close_about(self) -> None:
        self.app.pop_screen()


class InputModal(ModalScreen[str | None]):
    BINDINGS = [
        Binding("enter", "submit", "OK"),
        Binding("tab", "focus_next", "Next"),
        Binding("shift+tab", "focus_previous", "Previous"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, title: str, initial_value: str = "", placeholder: str = "") -> None:
        super().__init__()
        self.title = title
        self.initial_value = initial_value
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Container(classes="modal"):
            yield Label(self.title, classes="modal-title")
            yield Input(value=self.initial_value, placeholder=self.placeholder, id="modal-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.call_after_refresh(self.query_one("#modal-input", Input).focus)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "modal-input":
            self.action_submit()

    def action_submit(self) -> None:
        self.dismiss(self.query_one("#modal-input", Input).value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.action_submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConflictModal(ModalScreen[tuple[str, str | None] | None]):
    BINDINGS = [
        Binding("enter", "submit", "Default"),
        Binding("tab", "focus_next", "Next"),
        Binding("shift+tab", "focus_previous", "Previous"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, path_label: str) -> None:
        super().__init__()
        self.path_label = path_label

    def compose(self) -> ComposeResult:
        with Container(classes="modal"):
            yield Label(f"Local file exists:\n{self.path_label}", classes="modal-title")
            yield Input(placeholder="New file name for rename", id="rename-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("Overwrite", variant="warning", id="overwrite")
                yield Button("Rename", variant="primary", id="rename")
                yield Button("Skip", id="skip")

    def on_mount(self) -> None:
        self.call_after_refresh(self.query_one("#rename-input", Input).focus)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "rename-input":
            self.action_submit()

    def action_submit(self) -> None:
        value = self.query_one("#rename-input", Input).value.strip()
        if value:
            self.dismiss(("rename", value))
        else:
            self.dismiss(("skip", None))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rename":
            value = self.query_one("#rename-input", Input).value.strip()
            self.dismiss(("rename", value or None))
            return
        if event.button.id == "overwrite":
            self.dismiss(("overwrite", None))
            return
        self.dismiss(("skip", None))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConnectionScreen(Screen[None]):
    BINDINGS = [
        Binding("ctrl+s", "save_profile", "Save"),
        Binding("ctrl+n", "new_profile", "New"),
        Binding("delete", "delete_profile", "Delete"),
        Binding("f5", "connect_profile", "Connect"),
        Binding("f1", "show_about", "Changelog"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, profile_manager: ProfileManager, ssh_client: SSHClientWrapper, state: AppState) -> None:
        super().__init__()
        self.profile_manager = profile_manager
        self.ssh_client = ssh_client
        self.state = state
        self.profiles: list[ConnectionProfile] = []
        self.editing_original_name: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="connection-layout"):
            with Vertical(id="profiles-pane"):
                yield Label("Saved Profiles", classes="pane-title")
                yield ListView(id="profile-list")
            with VerticalScroll(id="form-pane"):
                yield Label("Connection", classes="pane-title")
                yield Input(placeholder="Profile name", id="profile-name")
                yield Input(placeholder="Host or IP", id="host")
                yield Input(placeholder="Username", id="username")
                yield Label("Authentication", classes="field-label")
                with Horizontal(classes="auth-buttons"):
                    yield Button("Password", id="auth-password", variant="primary")
                    yield Button("SSH Key", id="auth-key")
                yield Input(placeholder="Password or SSH key passphrase", password=True, id="password")
                yield Input(placeholder="SSH key path (for key auth)", id="key-path")
                yield Input(value="22", placeholder="Port", id="port")
                yield Input(placeholder="Default local download directory", id="download-dir")
                with Horizontal(classes="form-buttons"):
                    yield Button("Save Profile", variant="primary", id="save")
                    yield Button("New", id="new")
                    yield Button("Delete", id="delete")
                    yield Button("Connect", variant="success", id="connect")
                yield Static("", id="connection-status")
        yield Static(
            "Made by [link=http://patchharris.info]Patchharris[/link] | F1 changelog",
            classes="brand-line",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_profiles()
        self.query_one("#profile-name", Input).focus()

    def refresh_profiles(self) -> None:
        self.profiles = self.profile_manager.load_profiles()
        view = self.query_one("#profile-list", ListView)
        view.clear()
        for profile in self.profiles:
            view.append(ListItem(Label(profile.name)))
        if self.profiles:
            target_index = 0
            if self.state.selected_profile:
                for index, profile in enumerate(self.profiles):
                    if profile.name == self.state.selected_profile:
                        target_index = index
                        break
            view.index = target_index
            self.load_profile(self.profiles[target_index])
        else:
            self.clear_form()

    def clear_form(self) -> None:
        self.editing_original_name = None
        for field_id, value in {
            "profile-name": "",
            "host": "",
            "username": "",
            "password": "",
            "key-path": "",
            "port": "22",
            "download-dir": "",
        }.items():
            self.query_one(f"#{field_id}", Input).value = value
        self.set_auth_type("password")
        self.set_status("Enter connection details to create a profile.")

    def load_profile(self, profile: ConnectionProfile) -> None:
        self.editing_original_name = profile.name
        self.state.selected_profile = profile.name
        self.query_one("#profile-name", Input).value = profile.name
        self.query_one("#host", Input).value = profile.host
        self.query_one("#username", Input).value = profile.username
        self.query_one("#password", Input).value = ""
        self.query_one("#key-path", Input).value = profile.key_path
        self.query_one("#port", Input).value = str(profile.port)
        self.query_one("#download-dir", Input).value = profile.download_dir
        self.set_auth_type(profile.auth_type)
        secret_name = "password" if profile.auth_type == "password" else "SSH key passphrase"
        self.set_status(f"Loaded profile '{profile.name}'. Leave {secret_name} blank to use the saved keyring secret.")

    def set_status(self, message: str) -> None:
        self.query_one("#connection-status", Static).update(message)

    def get_auth_type(self) -> str:
        return "key" if self.query_one("#auth-key", Button).variant == "primary" else "password"

    def set_auth_type(self, auth_type: str) -> None:
        password_button = self.query_one("#auth-password", Button)
        key_button = self.query_one("#auth-key", Button)
        if auth_type == "key":
            password_button.variant = "default"
            key_button.variant = "primary"
            self.query_one("#password", Input).placeholder = "SSH key passphrase (optional)"
        else:
            password_button.variant = "primary"
            key_button.variant = "default"
            self.query_one("#password", Input).placeholder = "Password"

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "profile-list":
            return
        index = event.list_view.index
        if index is None or index < 0 or index >= len(self.profiles):
            return
        self.load_profile(self.profiles[index])

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id != "profile-list":
            return
        index = event.list_view.index
        if index is None or index < 0 or index >= len(self.profiles):
            return
        self.load_profile(self.profiles[index])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"password", "download-dir", "port", "username", "host", "profile-name", "key-path"}:
            result = self.action_connect_profile()
            if asyncio.iscoroutine(result):
                self.run_worker(result)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "save": self.action_save_profile,
            "new": self.action_new_profile,
            "delete": self.action_delete_profile,
            "connect": self.action_connect_profile,
        }
        if event.button.id == "auth-password":
            self.set_auth_type("password")
            return
        if event.button.id == "auth-key":
            self.set_auth_type("key")
            return
        action = actions.get(event.button.id or "")
        if action is not None:
            result = action()
            if asyncio.iscoroutine(result):
                self.run_worker(result)

    def build_profile_from_form(self) -> tuple[ConnectionProfile, str]:
        name = self.query_one("#profile-name", Input).value.strip()
        host = self.query_one("#host", Input).value.strip()
        username = self.query_one("#username", Input).value.strip()
        auth_type = self.get_auth_type()
        password = self.query_one("#password", Input).value
        key_path = self.query_one("#key-path", Input).value.strip()
        download_dir = self.query_one("#download-dir", Input).value.strip()
        port_text = self.query_one("#port", Input).value.strip() or "22"

        if not name or not host or not username:
            raise ValueError("Profile name, host, and username are required.")
        if auth_type not in {"password", "key"}:
            raise ValueError("Auth type must be 'password' or 'key'.")
        if auth_type == "key" and not key_path:
            raise ValueError("SSH key path is required for key authentication.")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError("Port must be a number.") from exc

        profile = ConnectionProfile(
            name=name,
            host=host,
            username=username,
            port=port,
            download_dir=download_dir,
            last_path=".",
            auth_type=auth_type,
            key_path=key_path,
        )
        existing = next((item for item in self.profiles if item.name == (self.editing_original_name or name)), None)
        if existing is not None:
            profile.last_path = existing.last_path
        return profile, password

    def action_new_profile(self) -> None:
        self.clear_form()
        self.query_one("#profile-name", Input).focus()

    async def action_save_profile(self) -> None:
        try:
            profile, password = self.build_profile_from_form()
        except ValueError as exc:
            self.set_status(str(exc))
            return

        self.set_status(f"Saving profile '{profile.name}'...")
        result = await asyncio.to_thread(
            self.profile_manager.upsert_profile,
            profile,
            password,
            password if profile.auth_type == "key" else "",
            self.editing_original_name,
        )
        self.state.selected_profile = result.profile.name
        self.editing_original_name = result.profile.name
        self.refresh_profiles()
        if result.secret_result.ok and result.passphrase_result.ok:
            self.set_status(f"Saved profile '{result.profile.name}'.")
        else:
            details = result.secret_result.message or result.passphrase_result.message
            self.set_status(
                f"Saved profile '{result.profile.name}', but the secret was not stored in keyring: {details}"
            )

    async def action_delete_profile(self) -> None:
        name = self.query_one("#profile-name", Input).value.strip()
        if not name:
            self.set_status("No profile selected.")
            return
        self.set_status(f"Deleting profile '{name}'...")
        await asyncio.to_thread(self.profile_manager.delete_profile, name)
        self.state.selected_profile = None
        self.refresh_profiles()
        self.set_status(f"Deleted profile '{name}'.")

    async def action_connect_profile(self) -> None:
        try:
            profile, password = self.build_profile_from_form()
        except ValueError as exc:
            self.set_status(str(exc))
            return

        passphrase = ""
        if profile.auth_type == "key":
            passphrase = password or self.profile_manager.get_passphrase(profile.name)
        else:
            password = password or self.profile_manager.get_password(profile.name)
            if not password:
                self.set_status("Password is required. Enter one or save it to keyring first.")
                return

        self.set_status(f"Connecting to {profile.host}:{profile.port} ...")
        try:
            await asyncio.to_thread(self.ssh_client.connect, profile, password, passphrase)
            self.profile_manager.upsert_profile(
                profile,
                password=password,
                passphrase=passphrase,
                original_name=self.editing_original_name,
            )
            self.state.selected_profile = profile.name
        except SSHConnectionError as exc:
            self.set_status(str(exc))
            return

        self.set_status(f"Connected to {profile.host}.")
        self.app.push_screen(
            BrowserScreen(
                profile_manager=self.profile_manager,
                ssh_client=self.ssh_client,
                state=self.state,
                profile=profile,
            )
        )

    def action_quit(self) -> None:
        self.app.exit()

    def action_show_about(self) -> None:
        self.app.push_screen(AboutScreen())


class BrowserScreen(Screen[None]):
    BINDINGS = [
        Binding("enter", "open_selected", "Open"),
        Binding("backspace", "go_up", "Up"),
        Binding("h", "go_up", "Up"),
        Binding("p", "toggle_preview", "Preview"),
        Binding("/", "begin_filter", "Filter"),
        Binding("tab", "switch_pane", "Pane"),
        Binding("space", "toggle_mark", "Mark"),
        Binding("d", "download", "Download"),
        Binding("u", "upload_selected_local", "Upload"),
        Binding("m", "begin_mkdir", "Mkdir"),
        Binding("v", "begin_move", "Move"),
        Binding("n", "begin_rename", "Rename"),
        Binding("x", "request_delete", "Delete"),
        Binding("escape", "cancel_inline_action", "Cancel"),
        Binding("r", "refresh_listing", "Refresh"),
        Binding(".", "toggle_hidden", "Hidden"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("f1", "show_about", "Changelog"),
        Binding("q", "disconnect", "Back"),
    ]

    SORTS = ("name", "size", "modified")

    def __init__(
        self,
        profile_manager: ProfileManager,
        ssh_client: SSHClientWrapper,
        state: AppState,
        profile: ConnectionProfile,
    ) -> None:
        super().__init__()
        self.profile_manager = profile_manager
        self.ssh_client = ssh_client
        self.state = state
        self.profile = profile
        self.current_path = profile.last_path or "."
        self.remote_entries: list[RemoteEntry] = []
        preferred_local_path = Path(profile.download_dir).expanduser() if profile.download_dir else Path.home()
        self.local_path = preferred_local_path if preferred_local_path.exists() else Path.home()
        self.local_entries: list[LocalEntry] = []
        self.active_pane = "remote"
        self.preview_visible = False
        self.inline_mode: str | None = None
        self.pending_delete: RemoteEntry | LocalEntry | None = None
        self.pending_delete_scope: str | None = None
        self.pending_move: RemoteEntry | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="browser-layout"):
            yield Label("", id="browser-info")
            with Horizontal(id="browser-panes"):
                with Vertical(classes="browser-pane"):
                    yield Label("", id="local-path-label", classes="pane-path")
                    yield ListView(id="local-list")
                with Vertical(classes="browser-pane"):
                    yield Label("", id="remote-path-label", classes="pane-path")
                    yield ListView(id="remote-list")
            with Vertical(id="preview-box"):
                yield Label("Preview", id="preview-title")
                with VerticalScroll(id="preview-scroll"):
                    yield Static("", id="preview-content")
            yield Input(placeholder="", id="inline-input")
            yield ProgressBar(total=100, show_eta=False, id="progress")
            yield Static("", id="browser-status")
        yield Static(
            "Made by [link=http://patchharris.info]Patchharris[/link] | F1 changelog",
            classes="brand-line",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self.set_preview_visibility()
        self.hide_inline_input()
        self.focus_active_pane()
        await self.refresh_listing()

    def set_status(self, message: str) -> None:
        self.query_one("#browser-status", Static).update(message)

    def show_inline_input(self, value: str, placeholder: str) -> None:
        inline = self.query_one("#inline-input", Input)
        inline.styles.display = "block"
        inline.value = value
        inline.placeholder = placeholder
        inline.focus()

    def hide_inline_input(self) -> None:
        inline = self.query_one("#inline-input", Input)
        inline.value = ""
        inline.placeholder = ""
        inline.styles.display = "none"
        self.focus_active_pane()

    def focus_active_pane(self) -> None:
        pane_id = "#remote-list" if self.active_pane == "remote" else "#local-list"
        self.query_one(pane_id, ListView).focus()

    def sync_active_pane_from_focus(self) -> str:
        if self.query_one("#remote-list", ListView).has_focus:
            self.active_pane = "remote"
        elif self.query_one("#local-list", ListView).has_focus:
            self.active_pane = "local"
        return self.active_pane

    def set_preview_visibility(self) -> None:
        preview_box = self.query_one("#preview-box", Vertical)
        preview_box.styles.display = "block" if self.preview_visible else "none"

    def update_header(self) -> None:
        self.query_one("#local-path-label", Label).update(f"Local: {self.local_path}")
        self.query_one("#remote-path-label", Label).update(f"Remote: {self.current_path}")
        self.query_one("#browser-info", Label).update(
            f"Active: {self.active_pane} | Sort: {self.state.sort_mode} | Hidden: {'on' if self.state.show_hidden else 'off'} | "
            f"Local filter: {self.state.local_filter or '-'} | Remote filter: {self.state.remote_filter or '-'} | "
            f"Marked: {len(self.state.multi_select)} | p preview | / filter | m mkdir | v move"
        )

    def render_remote_entries(self) -> None:
        view = self.query_one("#remote-list", ListView)
        view.clear()
        for entry in self.remote_entries:
            marked = entry.path in self.state.multi_select
            view.append(ListItem(Label(format_entry(entry, marked=marked))))
        if self.remote_entries:
            view.index = 0

    def render_local_entries(self) -> None:
        view = self.query_one("#local-list", ListView)
        view.clear()
        for entry in self.local_entries:
            view.append(ListItem(Label(format_local_entry(entry))))
        if self.local_entries:
            view.index = 0

    async def refresh_listing(self) -> None:
        self.update_header()
        self.set_status("Loading local and remote directories...")
        try:
            normalized = await asyncio.to_thread(self.ssh_client.normalize, self.current_path)
            entries = await asyncio.to_thread(self.ssh_client.list_directory, normalized)
        except SSHConnectionError as exc:
            self.set_status(str(exc))
            return

        self.current_path = normalized
        self.profile.last_path = normalized
        self.profile_manager.upsert_profile(self.profile, original_name=self.profile.name)
        filtered = [entry for entry in entries if self.state.show_hidden or not entry.name.startswith(".")]
        if self.state.remote_filter:
            filter_text = self.state.remote_filter.lower()
            filtered = [entry for entry in filtered if filter_text in entry.name.lower()]
        self.remote_entries = self.sort_entries(filtered)
        self.local_entries = self.load_local_entries(self.local_path)
        self.update_header()
        self.render_local_entries()
        self.render_remote_entries()
        await self.update_preview()
        self.set_status(
            f"{len(self.local_entries)} local item(s) in {self.local_path} | {len(self.remote_entries)} remote item(s) in {self.current_path}"
        )

    def sort_entries(self, entries: list[RemoteEntry]) -> list[RemoteEntry]:
        if self.state.sort_mode == "size":
            key = lambda item: (not item.is_dir, item.size, item.name.lower())
        elif self.state.sort_mode == "modified":
            key = lambda item: (not item.is_dir, item.modified_time, item.name.lower())
        else:
            key = lambda item: (not item.is_dir, item.name.lower())
        return sorted(entries, key=key)

    def load_local_entries(self, directory: Path) -> list[LocalEntry]:
        try:
            resolved = directory.expanduser().resolve()
            entries = []
            for child in resolved.iterdir():
                if not self.state.show_hidden and child.name.startswith("."):
                    continue
                if self.state.local_filter and self.state.local_filter.lower() not in child.name.lower():
                    continue
                stats = child.stat()
                entries.append(
                    LocalEntry(
                        name=child.name,
                        path=child,
                        is_dir=child.is_dir(),
                        size=stats.st_size,
                        modified_time=datetime.fromtimestamp(stats.st_mtime),
                    )
                )
        except OSError as exc:
            self.set_status(f"Local path error: {exc}")
            return []

        if self.state.sort_mode == "size":
            key = lambda item: (not item.is_dir, item.size, item.name.lower())
        elif self.state.sort_mode == "modified":
            key = lambda item: (not item.is_dir, item.modified_time, item.name.lower())
        else:
            key = lambda item: (not item.is_dir, item.name.lower())
        return sorted(entries, key=key)

    def get_selected_remote_entry(self) -> RemoteEntry | None:
        view = self.query_one("#remote-list", ListView)
        index = view.index
        if index is None or index < 0 or index >= len(self.remote_entries):
            return None
        return self.remote_entries[index]

    def get_selected_local_entry(self) -> LocalEntry | None:
        view = self.query_one("#local-list", ListView)
        index = view.index
        if index is None or index < 0 or index >= len(self.local_entries):
            return None
        return self.local_entries[index]

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "remote-list":
            self.active_pane = "remote"
            self.update_header()
            await self.action_open_selected()
        elif event.list_view.id == "local-list":
            self.active_pane = "local"
            self.update_header()
            await self.action_open_selected()

    async def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "remote-list":
            self.active_pane = "remote"
        elif event.list_view.id == "local-list":
            self.active_pane = "local"
        else:
            return
        self.update_header()
        await self.update_preview()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "inline-input":
            return
        if self.inline_mode == "rename":
            await self.finish_rename(event.value.strip())
        elif self.inline_mode == "delete":
            await self.finish_delete_confirmation(event.value.strip())
        elif self.inline_mode == "mkdir":
            await self.finish_mkdir(event.value.strip())
        elif self.inline_mode == "move":
            await self.finish_move(event.value.strip())
        elif self.inline_mode == "filter":
            await self.finish_filter(event.value.strip())

    async def finish_rename(self, new_name: str) -> None:
        entry = self.get_selected_remote_entry()
        if entry is None:
            self.inline_mode = None
            self.hide_inline_input()
            self.set_status("No item selected.")
            return
        if not new_name:
            self.inline_mode = None
            self.hide_inline_input()
            self.set_status("Rename cancelled. Name cannot be empty.")
            return
        if "/" in new_name:
            self.set_status("Rename cancelled. Enter a name, not a path.")
            return

        new_path = str(PurePosixPath(entry.path).with_name(new_name))
        try:
            await asyncio.to_thread(self.ssh_client.rename_path, entry.path, new_path)
        except SSHConnectionError as exc:
            self.set_status(str(exc))
        else:
            self.set_status(f"Renamed '{entry.name}' to '{new_name}'.")
        finally:
            self.inline_mode = None
            self.hide_inline_input()
            await self.refresh_listing()

    async def finish_delete_confirmation(self, confirmation: str) -> None:
        entry = self.pending_delete
        if entry is None:
            self.inline_mode = None
            self.hide_inline_input()
            self.set_status("No delete action is pending.")
            return
        expected = "delete" if entry.is_dir else "y"
        if confirmation.lower() != expected:
            self.inline_mode = None
            self.pending_delete = None
            self.pending_delete_scope = None
            self.hide_inline_input()
            if entry.is_dir:
                self.set_status("Delete cancelled. Type 'delete' exactly to confirm.")
            else:
                self.set_status("Delete cancelled. Type 'y' to confirm a file delete.")
            return

        try:
            if self.pending_delete_scope == "remote":
                assert isinstance(entry, RemoteEntry)
                await asyncio.to_thread(self.ssh_client.delete_path, entry.path)
            else:
                assert isinstance(entry, LocalEntry)
                await asyncio.to_thread(self.delete_local_path, entry.path)
        except SSHConnectionError as exc:
            self.set_status(str(exc))
            return
        except OSError as exc:
            self.set_status(f"Local filesystem error: {exc}")
            return
        finally:
            self.pending_delete = None
            self.pending_delete_scope = None
            self.inline_mode = None
            self.hide_inline_input()

        if isinstance(entry, RemoteEntry):
            self.state.multi_select.discard(entry.path)
        self.set_status(f"Deleted '{entry.name}'.")
        await self.refresh_listing()

    async def finish_mkdir(self, name: str) -> None:
        self.inline_mode = None
        self.hide_inline_input()
        if not name:
            self.set_status("Create directory cancelled. Name cannot be empty.")
            return
        if "/" in name:
            self.set_status("Create directory cancelled. Enter a directory name, not a path.")
            return
        target = posixpath.join(self.current_path, name)
        try:
            await asyncio.to_thread(self.ssh_client.mkdir, target)
        except SSHConnectionError as exc:
            self.set_status(str(exc))
            return
        self.set_status(f"Created remote directory '{name}'.")
        await self.refresh_listing()

    async def finish_move(self, target_value: str) -> None:
        entry = self.pending_move
        self.pending_move = None
        self.inline_mode = None
        self.hide_inline_input()
        if entry is None:
            self.set_status("No remote item selected for move.")
            return
        if not target_value:
            self.set_status("Move cancelled.")
            return

        target_path = target_value
        if not target_value.startswith("/"):
            target_path = posixpath.join(self.current_path, target_value)
        try:
            if await asyncio.to_thread(self.ssh_client.path_exists, target_path) and await asyncio.to_thread(self.ssh_client.is_dir, target_path):
                target_path = posixpath.join(target_path, entry.name)
        except SSHConnectionError as exc:
            self.set_status(str(exc))
            return
        try:
            await asyncio.to_thread(self.ssh_client.rename_path, entry.path, target_path)
        except SSHConnectionError as exc:
            self.set_status(str(exc))
            return
        self.state.multi_select.discard(entry.path)
        self.set_status(f"Moved '{entry.name}' to '{target_path}'.")
        await self.refresh_listing()

    async def finish_filter(self, filter_value: str) -> None:
        self.inline_mode = None
        self.hide_inline_input()
        if self.active_pane == "remote":
            self.state.remote_filter = filter_value
        else:
            self.state.local_filter = filter_value
        await self.refresh_listing()

    async def update_preview(self) -> None:
        if not self.preview_visible:
            return
        title = self.query_one("#preview-title", Label)
        content = self.query_one("#preview-content", Static)

        if self.active_pane == "remote":
            entry = self.get_selected_remote_entry()
            if entry is None:
                title.update("Preview")
                content.update("No remote item selected.")
                return
            title.update(f"Preview | Remote | {entry.name}")
            content.update(await self.build_remote_preview(entry))
            return

        entry = self.get_selected_local_entry()
        if entry is None:
            title.update("Preview")
            content.update("No local item selected.")
            return
        title.update(f"Preview | Local | {entry.name}")
        content.update(self.build_local_preview(entry))

    async def build_remote_preview(self, entry: RemoteEntry) -> str:
        header = [
            f"Name: {entry.name}",
            f"Path: {entry.path}",
            f"Type: {'Directory' if entry.is_dir else 'File'}",
            f"Size: {format_size(entry.size)}",
            f"Modified: {entry.modified_time:%Y-%m-%d %H:%M:%S}",
        ]
        if entry.is_dir:
            return "\n".join(header + ["", "[directory]"])
        try:
            preview = await asyncio.to_thread(self.ssh_client.read_text_preview, entry.path)
        except SSHConnectionError as exc:
            preview = f"[preview unavailable: {exc}]"
        return "\n".join(header + ["", preview])

    def build_local_preview(self, entry: LocalEntry) -> str:
        header = [
            f"Name: {entry.name}",
            f"Path: {entry.path}",
            f"Type: {'Directory' if entry.is_dir else 'File'}",
            f"Size: {format_size(entry.size)}",
            f"Modified: {entry.modified_time:%Y-%m-%d %H:%M:%S}",
        ]
        if entry.is_dir:
            return "\n".join(header + ["", "[directory]"])
        try:
            data = entry.path.read_bytes()[:8192]
            if b"\x00" in data:
                preview = "[binary file]"
            else:
                preview = data.decode("utf-8", errors="replace") or "[empty file]"
                if entry.size > 8192:
                    preview += "\n\n[preview truncated]"
        except OSError as exc:
            preview = f"[preview unavailable: {exc}]"
        return "\n".join(header + ["", preview])

    async def action_open_selected(self) -> None:
        if self.inline_mode is not None:
            return
        self.sync_active_pane_from_focus()
        if self.active_pane == "remote":
            entry = self.get_selected_remote_entry()
            if entry is None:
                return
            if not entry.is_dir:
                self.set_status("Selected remote item is a file. Press 'd' to download it.")
                return
            self.current_path = entry.path
            self.state.multi_select.clear()
            await self.refresh_listing()
            return

        entry = self.get_selected_local_entry()
        if entry is None:
            return
        if not entry.is_dir:
            self.set_status("Selected local item is a file. Press 'u' to upload it.")
            return
        self.local_path = entry.path
        await self.refresh_listing()

    async def action_go_up(self) -> None:
        if self.inline_mode is not None:
            return
        self.sync_active_pane_from_focus()
        if self.active_pane == "remote":
            self.current_path = self.ssh_client.parent_path(self.current_path)
            self.state.multi_select.clear()
        else:
            parent = self.local_path.parent
            self.local_path = parent if parent != self.local_path else self.local_path
        await self.refresh_listing()

    def action_toggle_mark(self) -> None:
        self.sync_active_pane_from_focus()
        if self.inline_mode is not None or self.active_pane != "remote":
            return
        entry = self.get_selected_remote_entry()
        if entry is None:
            return
        if entry.path in self.state.multi_select:
            self.state.multi_select.remove(entry.path)
        else:
            self.state.multi_select.add(entry.path)
        self.update_header()
        self.render_remote_entries()

    async def action_refresh_listing(self) -> None:
        await self.refresh_listing()

    async def action_toggle_hidden(self) -> None:
        self.state.show_hidden = not self.state.show_hidden
        await self.refresh_listing()

    async def action_cycle_sort(self) -> None:
        current_index = self.SORTS.index(self.state.sort_mode)
        self.state.sort_mode = self.SORTS[(current_index + 1) % len(self.SORTS)]
        await self.refresh_listing()

    def action_switch_pane(self) -> None:
        if self.inline_mode is not None:
            return
        self.active_pane = "local" if self.active_pane == "remote" else "remote"
        self.focus_active_pane()
        self.update_header()

    async def action_toggle_preview(self) -> None:
        self.preview_visible = not self.preview_visible
        self.set_preview_visibility()
        self.update_header()
        if self.preview_visible:
            await self.update_preview()

    def action_begin_rename(self) -> None:
        self.sync_active_pane_from_focus()
        if self.active_pane != "remote":
            self.set_status("Rename only applies to remote items.")
            return
        entry = self.get_selected_remote_entry()
        if entry is None:
            self.set_status("No item selected.")
            return
        self.pending_delete = None
        self.inline_mode = "rename"
        self.show_inline_input(entry.name, "Enter new name and press Enter")
        self.set_status(f"Renaming '{entry.name}'. Press Enter to save or Esc to cancel.")

    def action_begin_mkdir(self) -> None:
        self.sync_active_pane_from_focus()
        if self.active_pane != "remote":
            self.set_status("Remote mkdir only applies to the remote pane.")
            return
        self.pending_delete = None
        self.pending_delete_scope = None
        self.pending_move = None
        self.inline_mode = "mkdir"
        self.show_inline_input("", "Enter new remote directory name")
        self.set_status("Enter a new remote directory name and press Enter.")

    def action_begin_move(self) -> None:
        self.sync_active_pane_from_focus()
        if self.active_pane != "remote":
            self.set_status("Remote move only applies to the remote pane.")
            return
        entry = self.get_selected_remote_entry()
        if entry is None:
            self.set_status("No remote item selected.")
            return
        self.pending_delete = None
        self.pending_delete_scope = None
        self.pending_move = entry
        self.inline_mode = "move"
        self.show_inline_input(entry.name, "Enter target remote path or new name")
        self.set_status(f"Move '{entry.name}' to a new path or rename within the current folder.")

    def action_begin_filter(self) -> None:
        self.sync_active_pane_from_focus()
        current_filter = self.state.remote_filter if self.active_pane == "remote" else self.state.local_filter
        self.pending_delete = None
        self.pending_delete_scope = None
        self.pending_move = None
        self.inline_mode = "filter"
        self.show_inline_input(current_filter, "Enter filter text, or leave blank to clear")
        self.set_status(f"Set the {self.active_pane} pane filter and press Enter.")

    async def action_upload_selected_local(self) -> None:
        if self.inline_mode is not None:
            return
        self.sync_active_pane_from_focus()
        if self.active_pane != "local":
            self.set_status("Switch to the local pane to choose a file or directory to upload.")
            return
        entry = self.get_selected_local_entry()
        if entry is None:
            self.set_status("No local item selected.")
            return
        try:
            queue = await asyncio.to_thread(self.build_upload_queue, entry.path)
        except SSHConnectionError as exc:
            self.set_status(str(exc))
            return
        except OSError as exc:
            self.set_status(f"Local filesystem error: {exc}")
            return

        if not queue:
            self.set_status("Nothing to upload.")
            return
        await self.execute_transfer_queue(queue, "upload")
        await self.refresh_listing()

    def action_request_delete(self) -> None:
        self.sync_active_pane_from_focus()
        if self.active_pane == "remote":
            entry = self.get_selected_remote_entry()
            self.pending_delete_scope = "remote"
        else:
            entry = self.get_selected_local_entry()
            self.pending_delete_scope = "local"
        if entry is None:
            self.set_status("No item selected.")
            return
        self.pending_move = None
        self.pending_delete = entry
        self.inline_mode = "delete"
        item_type = "directory" if entry.is_dir else "file"
        if entry.is_dir:
            self.show_inline_input("", "Type delete and press Enter")
            self.set_status(
                f"Type 'delete' to confirm removal of {item_type} '{entry.name}', or press Esc to cancel."
            )
        else:
            self.show_inline_input("", "Type y to delete, or n to cancel")
            self.set_status(
                f"Type 'y' to delete {item_type} '{entry.name}', or type 'n' / press Esc to cancel."
            )

    def action_cancel_inline_action(self) -> None:
        self.inline_mode = None
        self.pending_delete = None
        self.pending_delete_scope = None
        self.pending_move = None
        self.hide_inline_input()
        self.set_status("Action cancelled.")

    async def action_download(self) -> None:
        self.sync_active_pane_from_focus()
        if self.active_pane != "remote":
            self.set_status("Download only applies to remote items.")
            return
        marked = list(self.state.multi_select)
        targets = [entry for entry in self.remote_entries if entry.path in marked]
        if not targets:
            entry = self.get_selected_remote_entry()
            if entry is None:
                self.set_status("No file selected.")
                return
            targets = [entry]

        base_dir = str(self.local_path)
        self.profile.download_dir = base_dir
        self.profile_manager.upsert_profile(self.profile, original_name=self.profile.name)

        try:
            queue = await asyncio.to_thread(self.build_download_queue, targets, Path(base_dir).expanduser())
        except SSHConnectionError as exc:
            self.set_status(str(exc))
            return
        except OSError as exc:
            self.set_status(f"Local filesystem error: {exc}")
            return

        if not queue:
            self.set_status("Nothing to download.")
            return
        await self.execute_transfer_queue(queue, "download")
        self.state.multi_select.clear()
        self.render_remote_entries()
        self.set_status(f"Downloaded {len(queue)} file(s) to {base_dir}")

    def resolve_destination(self, destination: Path) -> Path:
        if not destination.exists():
            return destination

        stem = destination.stem
        suffix = destination.suffix
        parent = destination.parent
        counter = 1
        while True:
            candidate = parent / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def delete_local_path(self, path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
            return
        path.unlink()

    def build_download_queue(self, entries: list[RemoteEntry], base_dir: Path) -> list[TransferTask]:
        queue: list[TransferTask] = []
        base_dir.mkdir(parents=True, exist_ok=True)

        for entry in entries:
            root_destination = self.resolve_destination(base_dir / entry.name)
            if entry.is_dir:
                for child in self.ssh_client.walk_directory(entry.path):
                    if child.is_dir:
                        continue
                    relative = child.path[len(entry.path):].lstrip("/")
                    destination = root_destination / relative
                    queue.append(
                        TransferTask(
                            direction="download",
                            source=child.path,
                            destination=str(destination),
                            size=child.size,
                            label=child.name,
                        )
                    )
                continue
            queue.append(
                TransferTask(
                    direction="download",
                    source=entry.path,
                    destination=str(root_destination),
                    size=entry.size,
                    label=entry.name,
                )
            )
        return queue

    def build_upload_queue(self, local_path: Path) -> list[TransferTask]:
        desired_remote = str(PurePosixPath(self.current_path) / local_path.name)
        remote_target = self.ssh_client.resolve_available_remote_path(desired_remote)
        queue: list[TransferTask] = []

        if local_path.is_dir():
            for child in local_path.rglob("*"):
                if child.is_dir():
                    continue
                relative = child.relative_to(local_path).as_posix()
                destination = posixpath.join(remote_target, relative)
                queue.append(
                    TransferTask(
                        direction="upload",
                        source=str(child),
                        destination=destination,
                        size=child.stat().st_size,
                        label=child.name,
                    )
                )
            return queue

        queue.append(
            TransferTask(
                direction="upload",
                source=str(local_path),
                destination=remote_target,
                size=local_path.stat().st_size,
                label=local_path.name,
            )
        )
        return queue

    async def execute_transfer_queue(self, queue: list[TransferTask], direction: str) -> None:
        total_bytes = sum(max(task.size, 1) for task in queue)
        progress_bar = self.query_one("#progress", ProgressBar)
        progress_bar.update(progress=0, total=100)
        noun = "upload" if direction == "upload" else "download"
        self.set_status(f"Starting {noun} queue with {len(queue)} file(s)...")

        try:
            await asyncio.to_thread(self.run_transfer_queue, queue, total_bytes)
        except SSHConnectionError as exc:
            self.set_status(str(exc))
            return
        except OSError as exc:
            self.set_status(f"Filesystem error: {exc}")
            return

        progress_bar.update(progress=100)
        self.set_status(f"Completed {noun} queue with {len(queue)} file(s).")

    def run_transfer_queue(self, queue: list[TransferTask], total_bytes: int) -> None:
        completed_bytes = 0

        for index, task in enumerate(queue, start=1):
            def progress(done: int, total: int, *, current_task: TransferTask = task, current_index: int = index) -> None:
                total_for_file = max(total, current_task.size, 1)
                overall_done = completed_bytes + min(done, total_for_file)
                percent = int((overall_done / max(total_bytes, 1)) * 100)
                self.app.call_from_thread(
                    self.update_transfer_progress,
                    current_task,
                    current_index,
                    len(queue),
                    percent,
                    int(done),
                    int(total_for_file),
                )

            if task.direction == "download":
                destination = Path(task.destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                self.ssh_client.download_file(task.source, str(destination), progress_callback=progress)
            else:
                self.ssh_client.ensure_parent_dir(task.destination)
                self.ssh_client.upload_file(task.source, task.destination, progress_callback=progress)

            completed_bytes += max(task.size, 1)

    def update_transfer_progress(
        self,
        task: TransferTask,
        index: int,
        count: int,
        overall_percent: int,
        current_done: int,
        current_total: int,
    ) -> None:
        direction_label = "Uploading" if task.direction == "upload" else "Downloading"
        current_percent = int((current_done / max(current_total, 1)) * 100)
        self.query_one("#progress", ProgressBar).update(progress=overall_percent)
        self.set_status(
            f"{direction_label} {task.label} ({index}/{count}) | current {current_percent}% | overall {overall_percent}%"
        )

    def action_disconnect(self) -> None:
        self.ssh_client.close()
        self.state.multi_select.clear()
        self.app.pop_screen()

    def action_show_about(self) -> None:
        self.app.push_screen(AboutScreen())
