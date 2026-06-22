"""
NibCast — PyInstaller Build Script
==========================================
Creates a standalone Windows executable in dist/NibCast/.

Usage:
    python build_exe.py              # build
    python build_exe.py --onefile    # single .exe (slower to start)
    python build_exe.py --clean      # clean previous build first

Requirements:
    pip install pyinstaller

The resulting dist/NibCast/ folder is fully self-contained —
copy it anywhere or zip it for distribution.
Users only need to:
  1. Extract / copy the folder
  2. Run NibCast.exe
  3. Open the dashboard (auto-opens) and enter their API key
"""

import os
import re
import sys
import shutil
import argparse
import importlib
import subprocess

# Windows consoles default to cp1252, which can't encode the ✅/❌/→ glyphs
# used in the status messages below — reconfigure so the script never crashes
# mid-build just because it tried to print a checkmark.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_DIR  = os.path.dirname(os.path.abspath(__file__))
_DIST = os.path.join(_DIR, "dist")
_BUILD= os.path.join(_DIR, "build_pyinstaller")

# ── Files / folders to bundle alongside the executable ─────────
_DATA = [
    # Flask templates and static assets for the web dashboard
    ("templates", "templates"),
    ("static",    "static"),
]

# ── Hidden imports pynput needs on Windows ─────────────────────
_HIDDEN = [
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
    "pynput._util.win32",
    "sounddevice",
    "numpy",
    "PIL._tkinter_finder",
    "flask",
    "pyotp",
]


def _check_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not found — installing…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


# requirements.txt name -> actual importable module, where they differ
_REQ_TO_IMPORT = {
    "pillow": "PIL",
}


def _check_requirements():
    """Make sure every package in requirements.txt is importable here.

    PyInstaller bundles a module only if it can find it in *this* Python
    environment — --hidden-import does not install anything. If a package
    is missing, PyInstaller logs "missing module" as a warning and still
    exits 0, producing a .exe that crashes with ModuleNotFoundError on
    first launch (e.g. flask, imported at the top of web_dashboard.py).
    """
    req_file = os.path.join(_DIR, "requirements.txt")
    missing = []
    with open(req_file, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            req, _, marker = line.partition(";")
            if "darwin" in marker and sys.platform != "darwin":
                continue
            name = re.split(r"[<>=!~\[]", req, maxsplit=1)[0].strip()
            if not name:
                continue
            module = _REQ_TO_IMPORT.get(name.lower(), name.lower())
            try:
                importlib.import_module(module)
            except ImportError:
                missing.append(name)

    if missing:
        print("\n" + "="*55)
        print("  ERROR: missing dependencies — build aborted")
        print("="*55)
        print("  The following packages from requirements.txt are not")
        print("  installed in this Python environment:")
        for m in missing:
            print(f"    - {m}")
        print()
        print("  Building now would produce a .exe that crashes on launch")
        print("  with ModuleNotFoundError. Fix:")
        print(f"    {sys.executable} -m pip install -r requirements.txt")
        print("="*55 + "\n")
        sys.exit(1)


def _check_pipeline_bugs():
    """Run check_pipeline.py's static analysis before building.

    Catches instance/module-function mismatches like the
    has_configured_backend() crash (calling var.attr() where attr is
    only a module-level function, not a method on var's class).
    """
    import check_pipeline
    findings = check_pipeline.check(_DIR)
    if findings:
        print("\n" + "="*55)
        print("  ERROR: pipeline sanity check failed — build aborted")
        print("="*55)
        for f in findings:
            print(f"  {f}")
        print()
        print("  Fix the issue(s) above, or run:")
        print(f"    {sys.executable} check_pipeline.py")
        print("  for details.")
        print("="*55 + "\n")
        sys.exit(1)


def _check_undefined_names():
    """Fail the build if any source file references an undefined name.

    py_compile only catches syntax errors; a name that is used but never
    defined (e.g. a local dropped during a refactor) compiles fine and only
    blows up at runtime — which froze into a .exe that crashed on launch with
    "NameError: name '...' is not defined". pyflakes finds these statically.
    Only undefined-name classes abort the build; unused-import style warnings
    are ignored. If pyflakes isn't installed, warn and skip rather than block.
    """
    try:
        import ast
        from pyflakes.checker import Checker
        from pyflakes import messages as pm
    except ImportError:
        print("  ⚠️  pyflakes not installed — skipping undefined-name check "
              "(pip install pyflakes to enable).")
        return

    fatal = (pm.UndefinedName, pm.UndefinedLocal, pm.UndefinedExport)
    findings = []
    for fname in sorted(f for f in os.listdir(_DIR) if f.endswith(".py")):
        path = os.path.join(_DIR, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=fname)
        except SyntaxError as e:
            findings.append(f"{fname}:{e.lineno}: syntax error: {e.msg}")
            continue
        for msg in Checker(tree, filename=fname).messages:
            if isinstance(msg, fatal):
                findings.append(f"{fname}:{msg.lineno}: {msg.message % msg.message_args}")

    if findings:
        print("\n" + "="*55)
        print("  ERROR: undefined name(s) found — build aborted")
        print("="*55)
        print("  These compile fine but crash at runtime (NameError):")
        for f in findings:
            print(f"  {f}")
        print("="*55 + "\n")
        sys.exit(1)


def build(onefile: bool = False, clean: bool = False):
    _check_pyinstaller()
    _check_requirements()
    _check_pipeline_bugs()
    _check_undefined_names()

    if clean:
        for d in (_DIST, _BUILD):
            if os.path.isdir(d):
                print(f"  Removing {d}")
                shutil.rmtree(d)

    # Generate icon if needed
    try:
        import install as _inst
        _inst.generate_icon()
    except Exception:
        pass

    icon = os.path.join(_DIR, "icon.ico")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=NibCast",
        "--distpath=" + _DIST,
        "--workpath=" + _BUILD,
        "--specpath=" + _DIR,
        "--noconfirm",
        "--windowed",                # no console window (use pythonw-style)
        "--log-level=WARN",
    ]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    if os.path.exists(icon):
        cmd += ["--icon", icon]

    for src, dst in _DATA:
        src_path = os.path.join(_DIR, src)
        if os.path.exists(src_path):
            cmd += ["--add-data", f"{src_path}{os.pathsep}{dst}"]

    for h in _HIDDEN:
        cmd += ["--hidden-import", h]

    # Collect all sounddevice DLLs (PortAudio)
    cmd += ["--collect-all", "sounddevice"]
    cmd += ["--collect-all", "pynput"]

    # Exclude PyMuPDF (AGPL-3.0) from the bundle — PDF export works fine when
    # users run from source with pymupdf installed; bundling it would require
    # distributing the full AGPL source alongside the exe.
    cmd += ["--exclude-module", "fitz"]
    cmd += ["--exclude-module", "pymupdf"]

    cmd.append(os.path.join(_DIR, "main.py"))

    print("\n" + "="*55)
    print("  Building NibCast standalone executable")
    print("="*55)
    print(f"  Mode   : {'single file' if onefile else 'one directory'}")
    print(f"  Output : {_DIST}")
    print()

    result = subprocess.run(cmd, cwd=_DIR)
    if result.returncode != 0:
        print("\n❌  Build failed — check the output above.")
        sys.exit(1)

    out_path = os.path.join(_DIST, "NibCast.exe" if onefile else "NibCast")
    print("\n" + "="*55)
    print("  ✅  Build complete!")
    print(f"  Output: {out_path}")
    print()
    print("  To distribute:")
    if onefile:
        print("    Share NibCast.exe directly.")
    else:
        print("    Zip the dist/NibCast/ folder and share it.")
        print("    Users run NibCast/NibCast.exe — no install needed.")
    print()
    print("  Users open the dashboard (auto-opens) and enter their")
    print("  free Groq API key at Config → AI Backend → Groq.")
    print("="*55 + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build NibCast standalone executable")
    ap.add_argument("--onefile", action="store_true",
                    help="Package as single .exe (slower to start, easier to share)")
    ap.add_argument("--clean",   action="store_true",
                    help="Remove previous build output first")
    args = ap.parse_args()
    build(onefile=args.onefile, clean=args.clean)
