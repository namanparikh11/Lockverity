# Windows application icon

This document describes how the Lockverity Windows
application icon is generated, where it lives in the
repository, and how it ends up in the installer, the
launcher, the Start Menu shortcut, the desktop shortcut,
and the Settings → Installed apps list.

## Approved source artwork

The brand-board artwork is the approved
``frontend/public/favicon-source.png`` ``1024x1024``
RGBA file. The brand board also ships a hand-tuned
``frontend/public/favicon.ico`` with ``16x16``,
``32x32`` and ``48x48`` entries (the three sizes the
browser needs). Both files are tracked and must not
be modified by the build.

The canonical Windows ICO at
``backend/pyinstaller/favicon-exe.ico`` is a
**mechanical** packaging derivative with ``16``, ``20``,
``24``, ``32``, ``40``, ``48``, ``64``, ``128`` and
``256`` pixel frames. Every frame is generated from the
approved 1024x1024 PNG through the Windows-only pipeline;
the web favicon geometry is not reused.

The approved export includes a soft shadow and low-alpha
cutout residue beyond the real 844x844 dark tile. Cropping
that export alone magnifies the residue and makes the ICO
border look worn. The Windows pipeline therefore:

1. detects the exact dark-tile bbox and crops it with 0.25%
   per-side composition padding;
2. retains the opaque RGB artwork and replaces only hidden,
   transparent-edge RGB with navy derived from the source;
3. applies a symmetric eight-times-supersampled rounded
   rectangle alpha mask using the measured 27.3% corner
   radius and a 0.5% safe margin;
4. clamps sub-alpha-8 resampling residue to true transparency;
5. renders every canonical frame from the corrected source.

The 848px crop is 0.94% tighter than the previous 856px
crop. This is the controlled final size increase; it does
not alter the glyph geometry or the approved web assets.

The approved brand assets are never modified by the
build. The build script
(``backend/scripts/generate_exe_icon.py``) reads them
and writes the packaging derivative into
``backend/pyinstaller/favicon-exe.ico``.

## Why the size set matters

The Windows shell queries the ICO for the following
sizes when rendering the application icon:

| Size | Where it shows |
| ---- | -------------- |
| 16x16 | Taskbar (small), classic toolbar |
| 20x20 | Medium-density taskbar |
| 24x24 | Classic Windows desktop / toolbar |
| 32x32 | Default icon view, Start tile |
| 40x40 | Extra-density taskbar |
| 48x48 | Medium icon view, "Programs and Features" |
| 64x64 | Large icon view |
| 128x128 | Extra-large icon view, modern Start |
| 256x256 | "Large Icons" view, high-DPI shell |

A missing entry causes the shell to fall back to the
generic application icon, which is the regression
that v2.1.2 fixes. v2.1.0 and v2.1.1 shipped an ICO
with only ``{16, 32, 48, 256}``; the missing ``24``,
``64``, ``128`` entries are the documented cause of
the partial-shell-fallback behaviour for some user
accounts.

## How the icon ends up in the installer

The Inno Setup source
(``backend/installer/lockverity.iss``) declares:

```ini
[Setup]
SetupIconFile=..\pyinstaller\favicon-exe.ico
UninstallDisplayIcon={app}\{#MyAppPayloadDir}\{#MyAppExeName},0

[Files]
Source: "..\pyinstaller\favicon-exe.ico"; DestDir: "{app}"; \
    Flags: "ignoreversion"

[Icons]
Name: "{group}\{#MyAppDisplayName}"; \
    Filename: "{app}\{#MyAppPayloadDir}\{#MyAppExeName}"; \
    IconFilename: "{app}\{#MyAppPayloadDir}\{#MyAppExeName}"; \
    IconIndex: 0
```

- ``SetupIconFile`` is the icon Inno Setup embeds in
  the installer EXE itself. Inno Setup also uses
  this icon for the generated uninstaller EXE
  (there is no separate ``UninstallIconFile``
  directive in Inno Setup 6.7.3).
- ``UninstallDisplayIcon`` is the icon Windows
  renders in **Settings → Apps → Installed apps**
  (the registry ``DisplayIcon`` value under
  ``HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{AppId}``).
  The value must point at the **actual installed**
  graphical launcher (``{app}\app\Lockverity.exe``,
  with the inner ``app\`` payload subdirectory) and
  must include an explicit ``,0`` icon index so the
  shell reads the first icon group from the PE.
- The [Files] entry copies the canonical ICO to
  ``{app}\favicon-exe.ico`` at install time. This is
  not strictly required for the icon to render in
  the shell (the DisplayIcon value points at the
  launcher EXE, not the ICO file), but it is useful
  for downstream tools that look up the brand asset
  by its canonical filename.
- The [Icons] entry references the same
  ``{app}\app\Lockverity.exe`` with ``IconIndex: 0``,
  so the Start Menu and desktop shortcuts share the
  same brand icon the launcher EXE shows in
  File Explorer.

## v2.1.0 / v2.1.1 regression fixed by v2.1.2 (published 2026-08-06)

The v2.1.0 / v2.1.1 ``UninstallDisplayIcon`` was:

```ini
UninstallDisplayIcon={app}\{#MyAppExeName}
```

Two defects:

1. The path ``{app}\Lockverity.exe`` does not exist
   on disk because the launcher is installed under
   ``{app}\app\Lockverity.exe`` (the inner ``app\``
   payload subdirectory). The Windows shell silently
   fell back to the generic application icon.
2. The directive did not include an explicit ``,0``
   icon index.

v2.1.2 fixes the path to
``{app}\app\Lockverity.exe,0`` and rebuilds the
canonical ICO with the full size set. The v2.1.2
release is published on
``checkpoint-v2.1.2-public-release``.

## How the icon ends up in the PyInstaller launcher

The PyInstaller spec
(``backend/pyinstaller/lockverity.spec``) declares:

```python
exe = EXE(
    pyz,
    a.scripts,
    [],
    ...,
    icon=str(BACKEND_ROOT / "pyinstaller" / "favicon-exe.ico"),
)
```

PyInstaller embeds the ICO as the executable's
icon resource. The Windows shell extracts the icon
from the PE and renders it for File Explorer, the
taskbar previews, the Alt-Tab switcher, and (via
the registry ``DisplayIcon``) the Installed apps
list.

## Building the canonical ICO

The build is a single command:

```powershell
python backend\scripts\generate_exe_icon.py
```

The function is documented in
``backend/scripts/generate_exe_icon.py`` and
exercised by ``backend/tests/test_exe_icon.py``.

The script is **idempotent**: running it twice from
the same approved sources produces byte-identical
output. (The ICO has no timestamps.)

## What the icon does **not** affect

- The CLI executable ``lockverity-cli.exe`` is a
  console subsystem binary and does not show a
  window icon in normal use. The PyInstaller spec
  for the CLI (``backend/pyinstaller/cli.spec``)
  does not set the ``icon=`` argument; the CLI
  inherits the standard console icon.
- The runtime's web UI icon is served by the
  ``/api/v1/system/info``-derived brand assets and
  is independent of the Windows ICO.
- The signing hooks
  (``backend/scripts/_authenticode_sign.py``) do not
  touch the ICO. Authenticode signatures are bound
  to the PE binary, not to its icon resource.
