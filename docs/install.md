# Lockverity v2.1.3 — Install guide

Lockverity v2.1.3 is the current published release. The packaged release target is Windows 10/11 x64; macOS and Linux remain source-based workflows.

**Release:** https://github.com/namanparikh11/Lockverity/releases/tag/v2.1.3
**Website:** https://lockverity.namanp.de
**Artifact source commit:** `2ddb0e2d8f3771c711e98809beab409d468bebdd`

## Windows installer

The installer is the recommended option for Windows users.

| Property | Value |
| --- | --- |
| File | [Lockverity-2.1.3-windows-x64-setup.exe](https://github.com/namanparikh11/Lockverity/releases/download/v2.1.3/Lockverity-2.1.3-windows-x64-setup.exe) |
| SHA-256 | `831264757dccde2c8feef0422390d9812f053fcad811455bde868756c7757bfd` |
| Size | 34,710,883 bytes |
| Architecture | Windows x64 |
| Privileges | per-user, no admin, no UAC |
| Install path | `%LOCALAPPDATA%\Programs\Lockverity` |
| Runtime home | `%LOCALAPPDATA%\Lockverity` |
| Signing | unsigned |

### Install and verify

1. Download the installer from the release link above.
2. Verify its SHA-256 before running it:

```powershell
Get-FileHash .\Lockverity-2.1.3-windows-x64-setup.exe -Algorithm SHA256
```

The hash must equal the value in the table. If it does not, do not run the file.

3. Double-click the installer and accept the licence.
4. Install normally. No elevation prompt is required.
5. Optionally launch Lockverity from the completion page or Start Menu.

v2.1.3 opens a dedicated Lockverity desktop window using Microsoft Edge WebView2 and owns its loopback FastAPI runtime lifecycle. It does not install a Windows service, scheduled task, autorun entry, firewall rule, or PATH modification.

## Windows portable

The v2.1.3 portable ZIP has been built and verified from the same source commit as the installer. Its expected identity is:

| Property | Value |
| --- | --- |
| File | `Lockverity-2.1.3-windows-x64-portable.zip` |
| SHA-256 | `5c68d1b06ad570d829a53eeb400ab4d7f2e8576c4b0a6079197d9dbdec35844f` |
| Size | 59,874,734 bytes |

The portable ZIP is now available as a release asset on the v2.1.3 release page:

- Download: [`Lockverity-2.1.3-windows-x64-portable.zip`](https://github.com/namanparikh11/Lockverity/releases/download/v2.1.3/Lockverity-2.1.3-windows-x64-portable.zip)
- SHA-256: `5c68d1b06ad570d829a53eeb400ab4d7f2e8576c4b0a6079197d9dbdec35844f`

## Unsigned-build warning

Lockverity v2.1.3 is currently unsigned. Windows may show **Unknown publisher** or a Microsoft Defender SmartScreen warning. This is why the release publishes SHA-256 values and provenance manifests. Signing status must not be represented as signed or trusted until a future release actually carries a valid signature.

## Runtime data and uninstall

Installed application files live under `%LOCALAPPDATA%\Programs\Lockverity`. Runtime data lives separately under `%LOCALAPPDATA%\Lockverity`, so uninstalling the application does not silently erase user state.

Use **Settings → Apps → Installed apps → Lockverity → Uninstall** or the Start Menu uninstall entry. Runtime data is preserved unless the user deliberately removes it.

## macOS and Linux

v2.1.3 does not publish DMG, PKG, AppImage, DEB, or RPM packages. On macOS and Linux, run Lockverity from source using the repository's Python/Node development workflow. These are developer workflows, not packaged release artifacts.

## Release integrity

The v2.1.3 release publishes:

- `INSTALLER-MANIFEST.json` — installer and embedded-payload provenance.
- `BUILD-MANIFEST.json` — portable build provenance.
- `Lockverity-2.1.3-SHA256SUMS.txt` — release-level checksums.
- `Lockverity-2.1.3-windows-x64-portable-SHA256SUMS.txt` — portable payload checksums.

The release tag is pinned to the exact artifact source commit rather than to later documentation-only commits. Do not rebuild or replace the published installer under the same tag; a changed binary requires its own release identity.

## Security boundaries

Lockverity inspects public GitHub repositories and uploaded source archives without executing analyzed repository code. It does not call repository-controlled `npm install`, `pip install`, Makefiles, or shell scripts during analysis. Missing or unavailable provider evidence remains explicit rather than being converted into a clean verdict.

See also:

- [Security policy](../SECURITY.md)
- [Privacy policy](privacy.md)
- [Code-signing policy](code-signing-policy.md)
- [Windows installer details](windows-installer.md)
- [Windows portable details](windows-portable.md)

## Historical installation documentation

The previous v2.1.2 guide is retained at [`install-v2.1.2.md`](install-v2.1.2.md). It documents the immutable v2.1.2 release and must not be used as the current v2.1.3 download guide.
