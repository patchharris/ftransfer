from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Markdown, ProgressBar, Static

from .download_manager import DownloadManager
from .models import AppState, ConnectionProfile, LocalEntry, PreparedDownload, RemoteEntry
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
            with Vertical(id="form-pane"):
                yield Label("Connection", classes="pane-title")
                yield Input(placeholder="Profile name", id="profile-name")
                yield Input(placeholder="Host or IP", id="host")
                yield Input(placeholder="Username", id="username")
                yield Input(placeholder="Password", password=True, id="password")
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
            "port": "22",
            "download-dir": "",
        }.items():
            self.query_one(f"#{field_id}", Input).value = value
        self.set_status("Enter connection details to create a profile.")

    def load_profile(self, profile: ConnectionProfile) -> None:
        self.editing_original_name = profile.name
        self.state.selected_profile = profile.name
        self.query_one("#profile-name", Input).value = profile.name
        self.query_one("#host", Input).value = profile.host
        self.query_one("#username", Input).value = profile.username
        self.query_one("#password", Input).value = ""
        self.query_one("#port", Input).value = str(profile.port)
        self.query_one("#download-dir", Input).value = profile.download_dir
        self.set_status(f"Loaded profile '{profile.name}'. Leave password blank to use saved keyring secret.")

    def set_status(self, message: str) -> None:
        self.query_one("#connection-status", Static).update(message)

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
        if event.input.id in {"password", "download-dir", "port", "username", "host", "profile-name"}:
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
        action = actions.get(event.button.id or "")
        if action is not None:
            result = action()
            if asyncio.iscoroutine(result):
                self.run_worker(result)

    def build_profile_from_form(self) -> tuple[ConnectionProfile, str]:
        name = self.query_one("#profile-name", Input).value.strip()
        host = self.query_one("#host", Input).value.strip()
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        download_dir = self.query_one("#download-dir", Input).value.strip()
        port_text = self.query_one("#port", Input).value.strip() or "22"

        if not name or not host or not username:
            raise ValueError("Profile name, host, and username are required.")
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
        )
        existing = next((item for item in self.profiles if item.name == (self.editing_original_name or name)), None)
        if existing is not None:
            profile.last_path = existing.last_path
        return profile, password

    def action_new_profile(self) -> None:
        self.clear_form()
        self.query_one("#profile-name", Input).focus()

    def action_save_profile(self) -> None:
        try:
            profile, password = self.build_profile_from_form()
        except ValueError as exc:
            self.set_status(str(exc))
            return

        result = self.profile_manager.upsert_profile(
            profile=profile,
            password=password,
            original_name=self.editing_original_name,
        )
        self.state.selected_profile = result.profile.name
        self.editing_original_name = result.profile.name
        self.refresh_profiles()
        if result.secret_result.ok:
            self.set_status(f"Saved profile '{result.profile.name}'.")
        else:
            self.set_status(
                f"Saved profile '{result.profile.name}', but password was not stored in keyring: {result.secret_result.message}"
            )

    def action_delete_profile(self) -> None:
        name = self.query_one("#profile-name", Input).value.strip()
        if not name:
            self.set_status("No profile selected.")
            return
        self.profile_manager.delete_profile(name)
        self.state.selected_profile = None
        self.refresh_profiles()
        self.set_status(f"Deleted profile '{name}'.")

    async def action_connect_profile(self) -> None:
        try:
            profile, password = self.build_profile_from_form()
        except ValueError as exc:
            self.set_status(str(exc))
            return

        if not password:
            password = self.profile_manager.get_password(profile.name)
        if not password:
            self.set_status("Password is required. Enter one or save it to keyring first.")
            return

        self.set_status(f"Connecting to {profile.host}:{profile.port} ...")
        try:
            await asyncio.to_thread(self.ssh_client.connect, profile, password)
            self.profile_manager.upsert_profile(
                profile,
                password=password,
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
        Binding("tab", "switch_pane", "Pane"),
        Binding("space", "toggle_mark", "Mark"),
        Binding("d", "download", "Download"),
        Binding("u", "upload_selected_local", "Upload"),
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
        self.download_manager = DownloadManager(ssh_client)
        self.current_path = profile.last_path or "."
        self.remote_entries: list[RemoteEntry] = []
        self.local_path = Path.home()
        self.local_entries: list[LocalEntry] = []
        self.active_pane = "remote"
        self.inline_mode: str | None = None
        self.pending_delete: RemoteEntry | LocalEntry | None = None
        self.pending_delete_scope: str | None = None

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
            yield Input(placeholder="", id="inline-input")
            yield ProgressBar(total=100, show_eta=False, id="progress")
            yield Static("", id="browser-status")
        yield Static(
            "Made by [link=http://patchharris.info]Patchharris[/link] | F1 changelog",
            classes="brand-line",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self.hide_inline_input()
        self.focus_active_pane()
        await self.refresh_listing()

    async def show_modal(self, screen: ModalScreen[object]) -> object:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()

        def handle_result(result: object) -> None:
            if not future.done():
                future.set_result(result)

        self.app.push_screen(screen, callback=handle_result)
        return await future

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

    def update_header(self) -> None:
        self.query_one("#local-path-label", Label).update(f"Local: {self.local_path}")
        self.query_one("#remote-path-label", Label).update(f"Remote: {self.current_path}")
        self.query_one("#browser-info", Label).update(
            f"Active: {self.active_pane} | Sort: {self.state.sort_mode} | Hidden: {'on' if self.state.show_hidden else 'off'} | Marked: {len(self.state.multi_select)} | Tab switch | u upload | d download"
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
        self.remote_entries = self.sort_entries(filtered)
        self.local_entries = self.load_local_entries(self.local_path)
        self.update_header()
        self.render_local_entries()
        self.render_remote_entries()
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

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "inline-input":
            return
        if self.inline_mode == "rename":
            await self.finish_rename(event.value.strip())
        elif self.inline_mode == "delete":
            await self.finish_delete_confirmation(event.value.strip())

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

    async def action_open_selected(self) -> None:
        if self.inline_mode is not None:
            return
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
        if self.active_pane == "remote":
            self.current_path = self.ssh_client.parent_path(self.current_path)
            self.state.multi_select.clear()
        else:
            parent = self.local_path.parent
            self.local_path = parent if parent != self.local_path else self.local_path
        await self.refresh_listing()

    def action_toggle_mark(self) -> None:
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

    def action_begin_rename(self) -> None:
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

    async def action_upload_selected_local(self) -> None:
        if self.inline_mode is not None:
            return
        if self.active_pane != "local":
            self.set_status("Switch to the local pane to choose a file or directory to upload.")
            return
        entry = self.get_selected_local_entry()
        if entry is None:
            self.set_status("No local item selected.")
            return
        progress_bar = self.query_one("#progress", ProgressBar)
        progress_bar.update(progress=0, total=100)

        desired_remote = str(PurePosixPath(self.current_path) / entry.name)
        try:
            remote_target = await asyncio.to_thread(
                self.ssh_client.resolve_available_remote_path,
                desired_remote,
            )
            await asyncio.to_thread(self.run_upload, entry.path, remote_target)
        except SSHConnectionError as exc:
            self.set_status(str(exc))
            return
        except OSError as exc:
            self.set_status(f"Local filesystem error: {exc}")
            return

        progress_bar.update(progress=100)
        remote_label = PurePosixPath(remote_target).name
        self.set_status(f"Uploaded '{entry.name}' as '{remote_label}'.")
        await self.refresh_listing()

    def action_request_delete(self) -> None:
        if self.active_pane == "remote":
            entry = self.get_selected_remote_entry()
            self.pending_delete_scope = "remote"
        else:
            entry = self.get_selected_local_entry()
            self.pending_delete_scope = "local"
        if entry is None:
            self.set_status("No item selected.")
            return
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
        self.hide_inline_input()
        self.set_status("Action cancelled.")

    async def action_download(self) -> None:
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

        prepared = await self.prepare_downloads(targets, Path(base_dir).expanduser())
        if not prepared:
            self.set_status("Nothing to download.")
            return

        progress_bar = self.query_one("#progress", ProgressBar)
        progress_bar.update(progress=0, total=100)
        self.set_status(f"Downloading {len(prepared)} item(s)...")

        try:
            await asyncio.to_thread(self.run_downloads, prepared)
        except SSHConnectionError as exc:
            self.set_status(str(exc))
            return
        except OSError as exc:
            self.set_status(f"Local filesystem error: {exc}")
            return

        self.state.multi_select.clear()
        self.render_remote_entries()
        progress_bar.update(progress=100)
        self.set_status(f"Downloaded {len(prepared)} item(s) to {base_dir}")

    async def prepare_downloads(self, entries: list[RemoteEntry], base_dir: Path) -> list[PreparedDownload]:
        prepared: list[PreparedDownload] = []
        base_dir.mkdir(parents=True, exist_ok=True)

        for entry in entries:
            destination = self.resolve_destination(base_dir / entry.name)
            prepared.append(
                PreparedDownload(
                    remote_path=entry.path,
                    remote_name=entry.name,
                    destination=destination,
                    is_dir=entry.is_dir,
                )
            )
        return prepared

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

    def run_upload(self, local_path: Path, remote_target: str) -> None:
        def progress(remote: str, done: int, total: int) -> None:
            ratio = 0 if total <= 0 else int((done / total) * 100)
            self.app.call_from_thread(self.update_upload_progress, remote, ratio)

        self.ssh_client.upload_path(local_path, remote_target, progress_callback=progress)

    def delete_local_path(self, path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
            return
        path.unlink()

    def run_downloads(self, prepared: list[PreparedDownload]) -> None:
        def progress(remote: str, done: int, total: int) -> None:
            ratio = 0 if total <= 0 else int((done / total) * 100)
            self.app.call_from_thread(self.update_download_progress, remote, ratio)

        self.download_manager.download_items(prepared, progress=progress)

    def update_download_progress(self, remote: str, percent: int) -> None:
        self.query_one("#progress", ProgressBar).update(progress=percent)
        self.set_status(f"Downloading {remote} ... {percent}%")

    def update_upload_progress(self, remote: str, percent: int) -> None:
        self.query_one("#progress", ProgressBar).update(progress=percent)
        self.set_status(f"Uploading to {remote} ... {percent}%")

    def action_disconnect(self) -> None:
        self.ssh_client.close()
        self.state.multi_select.clear()
        self.app.pop_screen()

    def action_show_about(self) -> None:
        self.app.push_screen(AboutScreen())
