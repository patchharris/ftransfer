# Changelog

All notable changes to this project will be documented in this file.

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
