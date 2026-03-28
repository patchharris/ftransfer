from __future__ import annotations

from textual.app import App

from .models import AppState
from .profile_manager import ProfileManager
from .screens import ConnectionScreen
from .ssh_client import SSHClientWrapper


class SCPBrowserApp(App[None]):
    TITLE = "SCP Browser TUI"
    SUB_TITLE = "Remote file browsing and downloads over SSH/SFTP"

    CSS = """
    Screen {
        layout: vertical;
    }

    #connection-layout, #browser-layout {
        height: 1fr;
    }

    #connection-layout {
        layout: horizontal;
    }

    #profiles-pane {
        width: 32;
        padding: 1 1;
        border: round $primary;
    }

    #form-pane {
        padding: 1 2;
        border: round $accent;
    }

    .pane-title {
        text-style: bold;
        margin-bottom: 1;
    }

    .field-label {
        margin-bottom: 1;
    }

    Input {
        margin-bottom: 1;
    }

    .auth-buttons {
        height: auto;
        margin-bottom: 1;
    }

    .form-buttons {
        margin: 1 0;
        height: auto;
    }

    #connection-status, #browser-status {
        min-height: 3;
        padding: 1 0;
    }

    #path-label {
        text-style: bold;
        padding: 0 1;
    }

    #browser-info {
        padding: 0 1 1 1;
    }

    #browser-panes {
        layout: horizontal;
        height: 1fr;
    }

    .browser-pane {
        width: 1fr;
        border: round $primary;
        padding: 0 1;
    }

    .pane-path {
        text-style: bold;
        padding: 0 1;
    }

    #remote-list {
        height: 1fr;
    }

    #local-list {
        height: 1fr;
    }

    #inline-input {
        display: none;
        margin: 1 0 0 0;
    }

    #progress {
        margin: 1 0 0 0;
    }

    .brand-line {
        height: 1;
        content-align: center middle;
        color: $text-muted;
        margin-top: 1;
    }

    .about-box {
        height: 1fr;
        border: round $accent;
        padding: 0 1;
    }

    .modal {
        width: 70;
        height: auto;
        background: $surface;
        border: round $warning;
        padding: 1 2;
        align: center middle;
    }

    .modal-title {
        margin-bottom: 1;
        text-style: bold;
    }

    .modal-buttons {
        margin-top: 1;
        height: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.profile_manager = ProfileManager()
        self.ssh_client = SSHClientWrapper()
        self.state = AppState()

    def on_mount(self) -> None:
        self.push_screen(
            ConnectionScreen(
                profile_manager=self.profile_manager,
                ssh_client=self.ssh_client,
                state=self.state,
            )
        )


def main() -> None:
    SCPBrowserApp().run()
