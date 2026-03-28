# Changelog

All notable changes to this project will be documented in this file.

## [0.2.1] - 2026-03-28

### Added

- SSH key authentication with profile-level key path support
- Remote directory creation
- Remote move operation
- Local and remote pane filtering
- Queued transfer progress with overall progress updates
- Live local and remote preview panel for highlighted items
- GitHub Actions workflow for PyPI Trusted Publishing

### Changed

- Connection profiles can now store an auth type and SSH key path
- Transfer progress now reflects the full queue instead of a single file at a time
- The local pane starts in the profile's saved local download directory when available
- The connection form now uses toggle buttons for password vs SSH key auth
- The connection form pane is scrollable in smaller terminals
- SFTP operations now reconnect and retry once after a dropped channel
- The preview panel starts closed by default and can be toggled on demand
- The preview panel content is scrollable
- Pane navigation now follows the currently focused pane more reliably
- The PyPI distribution name is now `scp-browser-tui` while the CLI command remains `ftransfer`

## [0.1.0] - 2026-03-28

### Added

- Initial Textual-based TUI application structure
- Saved connection profiles backed by JSON config storage
- System keyring integration for password storage where available
- Remote SSH/SFTP browsing with Paramiko
- Two-pane browser layout with local and remote directory navigation
- Remote file and directory download support
- Local file and directory upload support
- Progress updates for upload and download operations
- Remote rename support
- Remote delete support with safer confirmation flow
- Packaging metadata and `ftransfer` console entry point
- Windows launcher scripts
- Example profile configuration

### Changed

- Reworked upload flow from raw path entry to local pane selection
- Replaced interactive conflict prompts with automatic destination renaming
- Updated branding to include `Made by Patchharris` with link support in compatible terminals

### Notes

- This release focuses on a functional MVP with a keyboard-driven workflow.
- SSH key authentication and richer confirmations remain future improvements.
