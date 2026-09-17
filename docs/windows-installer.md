# Lockverity v2.1.3 — Windows x64 installer

The v2.1.3 installer is the recommended Windows distribution.

| Property | Value |
| --- | --- |
| File | `Lockverity-2.1.3-windows-x64-setup.exe` |
| SHA-256 | `831264757dccde2c8feef0422390d9812f053fcad811455bde868756c7757bfd` |
| Size | 34,710,883 bytes |
| Source commit | `2ddb0e2d8f3771c711e98809beab409d468bebdd` |
| Architecture | x64 |
| Privileges | per-user, no admin, no UAC |
| Default path | `%LOCALAPPDATA%\Programs\Lockverity` |
| Runtime home | `%LOCALAPPDATA%\Lockverity` |
| Signing | unsigned |

Download: https://github.com/namanparikh11/Lockverity/releases/download/v2.1.3/Lockverity-2.1.3-windows-x64-setup.exe

The installer does not modify PATH, install a Windows service, create an autorun entry, add a firewall rule, or require administrator privilege. The optional desktop shortcut remains user-controlled.

The installed application opens a dedicated Lockverity desktop window backed by Microsoft Edge WebView2. The application owns the loopback FastAPI process lifecycle rather than opening the UI as a normal browser tab.

Before running the installer, verify its SHA-256 against the release checksum and `INSTALLER-MANIFEST.json`. Windows may display **Unknown publisher** or SmartScreen warnings because v2.1.3 is unsigned.

Reinstall/repair uses the same stable application identity. Uninstall removes application files while preserving `%LOCALAPPDATA%\Lockverity` runtime data unless the user deliberately removes that data.

For the full current install flow, see [`install.md`](install.md). The immutable historical v2.1.2 installer documentation is retained at [`windows-installer-v2.1.2.md`](windows-installer-v2.1.2.md).
