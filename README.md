# SCP Browser TUI

`scp-browser-tui` is a keyboard-driven terminal file browser for downloading files from remote Linux systems over SSH/SFTP. It uses Textual for the interface, Paramiko for SSH/SFTP operations, and keyring for password storage when available.

## Features

- Connection screen for creating, editing, deleting, and reusing saved profiles
- Password storage through the system keyring when available
- Two-pane local and remote file browser with keyboard navigation
- File metadata display: name, type, size, modified time
- Download of files and directories
- Upload of local files and directories into the current remote folder
- Download progress bar and friendly status messages
- Safe remote rename and delete operations
- Auto-rename handling for local and remote name conflicts
- Hidden-file toggle and sort cycling
- Per-profile last visited remote path and default local download directory

## Project Layout

```text
ftransfer/
├── CHANGELOG.md
├── README.md
├── requirements.txt
├── pyproject.toml
├── ftransfer.cmd
├── ftransfer.ps1
├── examples/
│   └── profiles.example.json
└── src/
    └── scp_browser/
        ├── __init__.py
        ├── __main__.py
        ├── app.py
        ├── config.py
        ├── download_manager.py
        ├── models.py
        ├── profile_manager.py
        ├── secrets.py
        ├── ssh_client.py
        └── screens.py
```

## Installation

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

To install the `ftransfer` command:

```bash
pip install -e .
```

## One-Command Install

Recommended with `pipx`:

```bash
pipx install git+https://github.com/patchharris/ftransfer.git
```

Fallback with `pip`:

```bash
pip install "ftransfer @ git+https://github.com/patchharris/ftransfer.git"
```

## Run

From the project root:

```bash
PYTHONPATH=src python -m scp_browser
```

Windows PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m scp_browser
```

After `pip install -e .`, you can launch it directly with:

```bash
ftransfer
```

From this repo folder on Windows, you can also use:

```powershell
.\ftransfer.ps1
```

## Keyboard Shortcuts

### Connection screen

- `Tab` / `Shift+Tab`: move between fields
- `Ctrl+S`: save profile
- `Ctrl+N`: clear form for a new profile
- `Delete`: delete selected profile
- `F5`: connect with current form values
- `Enter`: submit a form field and connect
- `F1`: open the in-app changelog/about screen
- `q`: quit

### Browser screen

- `Tab`: switch between local and remote panes
- `Enter`: open selected directory in the active pane
- `Backspace` or `h`: go to parent directory in the active pane
- `r`: refresh both panes
- `.`: toggle hidden files
- `s`: cycle sort order
- `F1`: open the in-app changelog/about screen
- `q`: disconnect and return to the connection screen

### Remote pane actions

- `Space`: toggle multi-select
- `d`: download selected items, or current item if none are marked
- `n`: rename selected remote item
- `x`: delete selected remote item

Delete confirmation:

- file delete: type `y` and press `Enter`
- directory delete: type `delete` and press `Enter`

### Local pane actions

- `u`: upload selected local file or directory into the current remote folder

## Profiles and Secrets

Profiles are stored as JSON in the per-user config directory:

- Linux: `~/.config/scp-browser-tui/profiles.json`
- Windows: `%APPDATA%\scp-browser-tui\profiles.json`

Passwords are not written into `profiles.json`. The app stores them in the system keyring under the `scp-browser-tui` service name when the keyring backend is available. If keyring is unavailable, the password field remains session-only and the secret handling is isolated in `src/scp_browser/secrets.py`.

## Example Profile File

See [examples/profiles.example.json](/C:/Users/patch/Coding/ftransfer/examples/profiles.example.json).

## Notes

- The app uses SFTP for directory browsing and downloads. It does not shell out to `scp`.
- It targets Python 3.11+.
- SSH key authentication is not implemented in the MVP, but the client and profile models are structured so it can be added later.
- The browser is now two-pane: local on the left, remote on the right.
- If a destination name already exists, uploads and downloads auto-rename rather than prompting.

## Future Enhancements

- SSH key and agent authentication
- Search within the current directory
- Remote folder bookmarks
- Better recursive directory progress aggregation
- Local destination picker with bookmarks/history
- Host key verification management UI
