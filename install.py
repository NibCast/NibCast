# ============================================================
#  NibCast — Desktop Installer
#  Run once: python install.py
#  Creates a desktop shortcut and configures startup.
# ============================================================
import io
import os
import struct
import sys
import subprocess

_DIR = os.path.dirname(os.path.abspath(__file__))


def _pythonw() -> str:
    """Return path to pythonw.exe (no console window)."""
    candidates = [
        os.path.join(_DIR, "venv", "Scripts", "pythonw.exe"),
        sys.executable.replace("python.exe", "pythonw.exe"),
        os.path.join(os.path.dirname(sys.executable), "pythonw.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return sys.executable   # fallback: python.exe with window


def create_desktop_shortcut() -> bool:
    """Create NibCast.lnk on the Windows Desktop via PowerShell."""
    pythonw  = _pythonw()
    main_py  = os.path.join(_DIR, "main.py")
    icon_ico = os.path.join(_DIR, "icon.ico")

    # Build desktop path (handles OneDrive-redirected Desktop)
    ps_desktop = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Environment]::GetFolderPath('Desktop')"],
        capture_output=True, text=True
    )
    desktop = ps_desktop.stdout.strip() or os.path.join(os.path.expanduser("~"), "Desktop")
    shortcut = os.path.join(desktop, "NibCast.lnk")

    def _ps(p: str) -> str: return p.replace("'", "''")
    icon_arg = f"$lnk.IconLocation = '{_ps(icon_ico)}'" if os.path.exists(icon_ico) else ""

    ps_script = f"""
$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut('{_ps(shortcut)}')
$lnk.TargetPath      = '{_ps(pythonw)}'
$lnk.Arguments       = '"{_ps(main_py)}"'
$lnk.WorkingDirectory= '{_ps(_DIR)}'
$lnk.Description     = 'NibCast - AI Voice Dictation'
{icon_arg}
$lnk.Save()
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"[OK] Desktop shortcut created -> {shortcut}")
        return True
    else:
        print(f"[FAIL] Shortcut creation failed:\n{result.stderr}")
        return False


def create_run_batch() -> str:
    """Create a portable no-console launch batch in the project folder.
    Uses %~dp0 so the batch works from any location, not just the install path.
    """
    bat = os.path.join(_DIR, "run_nibcast.bat")
    with open(bat, "w") as f:
        f.write("@echo off\n")
        f.write("cd /d \"%~dp0\"\n")
        f.write("start \"\" \"%~dp0venv\\Scripts\\pythonw.exe\" \"%~dp0main.py\"\n")
    print(f"[OK] Run script created -> {bat}")
    return bat


def setup_autostart(enable: bool = True) -> bool:
    """Add/remove NibCast from Windows startup via Registry."""
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    val_name = "NibCastLocal"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                             winreg.KEY_SET_VALUE)
        if enable:
            pythonw = _pythonw()
            main_py = os.path.join(_DIR, "main.py")
            winreg.SetValueEx(key, val_name, 0, winreg.REG_SZ,
                              f'"{pythonw}" "{main_py}"')
            print("[OK] Startup entry added (runs at login)")
        else:
            try:
                winreg.DeleteValue(key, val_name)
                print("[OK] Startup entry removed")
            except FileNotFoundError:
                print("[INFO] No startup entry found")
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"[FAIL] Registry error: {e}")
        return False


def _build_ico(images) -> bytes:
    """Assemble a multi-size ICO from a list of PIL RGBA images (PNG-in-ICO)."""
    n      = len(images)
    header = struct.pack("<HHH", 0, 1, n)
    dirs   = b""
    blobs  = []
    offset = 6 + n * 16
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        blob = buf.getvalue()
        w, h = img.size
        dirs  += struct.pack("<BBBBHHII",
                             w if w < 256 else 0,
                             h if h < 256 else 0,
                             0, 0, 1, 32, len(blob), offset)
        blobs.append(blob)
        offset += len(blob)
    return header + dirs + b"".join(blobs)


def generate_icon(force: bool = False):
    """Generate icon.ico — NibCast brand (amber waveform + NC on dark).

    force=True regenerates even when icon.ico already exists.
    """
    icon_path = os.path.join(_DIR, "icon.ico")
    if os.path.exists(icon_path) and not force:
        return icon_path
    try:
        from PIL import Image, ImageDraw, ImageFont

        BG      = ( 13,  13,  13, 255)   # #0d0d0d
        AMBER   = (232, 165,  37, 255)   # #e8a525
        AMBER_H = (255, 200,  80, 255)   # centre bar highlight
        TRANSP  = (  0,   0,   0,   0)
        BARS    = [0.38, 0.68, 1.00, 0.68, 0.38]

        def _frame(sz: int) -> Image.Image:
            img  = Image.new("RGBA", (sz, sz), TRANSP)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle([0, 0, sz-1, sz-1],
                                   radius=max(sz//5, 3), fill=BG)
            pad_x    = sz * 0.17
            wave_top = sz * 0.14
            wave_bot = sz * 0.72
            wave_h   = wave_bot - wave_top
            n        = len(BARS)
            total_w  = sz - 2 * pad_x
            bar_w    = total_w / (n + (n - 1) * 0.67)
            gap_w    = bar_w * 0.67
            for i, r in enumerate(BARS):
                bh  = max(wave_h * r, 2)
                bx  = pad_x + i * (bar_w + gap_w)
                bw  = max(bar_w, 1)
                clr = AMBER_H if i == 2 else AMBER
                draw.rounded_rectangle(
                    [bx, wave_bot - bh, bx + bw - 1, wave_bot - 1],
                    radius=max(bw / 2, 1), fill=clr)
            if sz >= 48:
                fs   = max(int(sz * 0.22), 8)
                font = None
                for face in ("ariblk.ttf", "arialbd.ttf", "arial.ttf",
                             "calibrib.ttf", "verdanab.ttf"):
                    try:
                        font = ImageFont.truetype(face, fs)
                        break
                    except Exception:
                        pass
                if font is None:
                    font = ImageFont.load_default()
                bb = draw.textbbox((0, 0), "NC", font=font)
                tw, th = bb[2] - bb[0], bb[3] - bb[1]
                draw.text(
                    ((sz - tw) // 2 - bb[0],
                     int(wave_bot + (sz - wave_bot - th) / 2) - bb[1]),
                    "NC", font=font, fill=AMBER)
            return img

        frames = [_frame(s) for s in (256, 128, 64, 48, 32, 16)]
        with open(icon_path, "wb") as f:
            f.write(_build_ico(frames))
        print(f"[OK] Icon generated -> {icon_path}")
        return icon_path
    except Exception as e:
        print(f"[WARN] Could not generate icon: {e}")
        return None


def run_setup(autostart: bool = False):
    print("\n" + "="*38)
    print("  NibCast -- Setup")
    print("="*38)
    generate_icon()
    create_run_batch()
    create_desktop_shortcut()
    if autostart:
        setup_autostart(True)
    print("\nSetup complete!  Double-click the desktop icon to launch.\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--autostart", action="store_true",
                        help="Add to Windows startup")
    parser.add_argument("--remove-autostart", action="store_true",
                        help="Remove from Windows startup")
    args = parser.parse_args()

    if args.remove_autostart:
        setup_autostart(False)
    else:
        run_setup(autostart=args.autostart)
