# Lockverity v2.1.3 — Windows x64 portable

The v2.1.3 portable package is built and verified from the same artifact source commit as the installer.

| Property | Value |
| --- | --- |
| File | `Lockverity-2.1.3-windows-x64-portable.zip` |
| SHA-256 | `5c68d1b06ad570d829a53eeb400ab4d7f2e8576c4b0a6079197d9dbdec35844f` |
| Size | 59,874,734 bytes |
| Source commit | `2ddb0e2d8f3771c711e98809beab409d468bebdd` |
| Runtime home | `%LOCALAPPDATA%\Lockverity` by default |
| Signing | unsigned |

The large portable ZIP attachment is still being finalized on GitHub. The [v2.1.3 release page](https://github.com/namanparikh11/Lockverity/releases/tag/v2.1.3) is the source of truth; do not use a guessed direct URL until the ZIP appears there as an uploaded release asset.

When available, extract the **entire** archive to a directory you control and run `Lockverity.exe`. Do not run it from inside the ZIP viewer. `Lockverity.exe` opens the dedicated WebView2 desktop window; `lockverity-cli.exe` provides the documented CLI lifecycle and diagnostics commands.

Portable means the application files do not require installation. Runtime data is still stored separately under `%LOCALAPPDATA%\Lockverity` unless `LOCKVERITY_HOME` is explicitly overridden.

Verify the ZIP SHA-256 before extraction and use the bundled `SHA256SUMS.txt` / `BUILD-MANIFEST.json` to verify payload integrity. v2.1.3 is unsigned, so Windows may display SmartScreen or Unknown publisher warnings for its executables.

For the current release guide, see [`install.md`](install.md). The immutable historical v2.1.2 portable documentation is retained at [`windows-portable-v2.1.2.md`](windows-portable-v2.1.2.md).
