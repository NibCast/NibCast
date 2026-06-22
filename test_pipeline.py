# ============================================================
#  NibCast — Pipeline Self-Test
# ============================================================
#  Exercises every non-GUI code path and (optionally) makes live
#  API calls so you can confirm a build works on a given laptop
#  WITHOUT having to press hotkeys or speak into the mic.
#
#  Run:
#     venv\Scripts\python test_pipeline.py          # includes live calls if keys exist
#     venv\Scripts\python test_pipeline.py --offline  # skip all network calls
#
#  Exit code 0 = no failures, 1 = at least one [XX] FAIL.
#
#  What it CANNOT test (do these by hand on each laptop):
#    - Physically holding a hotkey and pasting into a real app
#    - Speaking the wake phrase / speaker verification with a real mic
#  See the manual checklist printed at the end.
# ============================================================

import argparse
import ast
import sys
import time
import types

ROWS = []
def rec(area, name, status, detail=""):
    ROWS.append((area, name, status, detail))


def _load_wake_matcher():
    """main.py is the app entry point and blocks on import, so AST-extract the
    two pure functions and exec them against a stub config/log."""
    import config
    src = open("main.py", encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = [n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name in {"_jaro", "_match_wake_word"}]
    log = types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
    ns = {"re": __import__("re"), "config": config, "log": log, "getattr": getattr}
    for f in funcs:
        exec(compile(ast.Module([f], []), "main.py", "exec"), ns)
    return ns["_match_wake_word"]


def test_wake_word(match, config):
    ww = (getattr(config, "WAKE_WORD", "") or "").strip() or "hey flow"
    # Generic edge cases that must hold for ANY two-word "hey X" phrase.
    cases = [
        (ww, True),
        (ww.title(), True),
        (ww + " open the file", True),
        ("hello there how are you", False),
        ("thank you so much", False),
        ("but it was", False),
    ]
    ok = True
    for text, exp in cases:
        m, _ = match(text, ww)
        if m != exp:
            ok = False
            rec("Wake word", f"{text!r} vs {ww!r}", "FAIL", f"got {m}, expected {exp}")
    if ok:
        rec("Wake word", f"matcher suite [{ww}]", "PASS", f"{len(cases)} cases clean")
    # Regression guard for the 3c fuzzy-match fix (interjection can't carry a weak word)
    rec("Wake word", "3c fix: 'hey fowler' != 'hey flow'", "PASS" if not match("hey fowler", "hey flow")[0] else "FAIL")
    rec("Wake word", "3c fix: 'hey floor' != 'hey flow'", "PASS" if not match("hey floor", "hey flow")[0] else "FAIL")


def test_filters(transcriber):
    junk = ["thank you", "okay", "you", "[music]", ".", "  ", "bye bye."]
    real = ["open the dashboard", "send this to the team"]
    jc = all(transcriber._is_hallucination(t) for t in junk)
    rk = not any(transcriber._is_hallucination(t) for t in real)
    rec("ASR filter", "hallucination filter", "PASS" if (jc and rk) else "FAIL",
        f"junk-caught={jc} real-kept={rk}")


def test_asr_backends(transcriber):
    for b in ["groq", "openai", "deepgram", "local", "custom", "nvidia"]:
        p = transcriber._backend_params(b)
        if b == "nvidia":
            rec("ASR backend", f"{b}", "PASS" if p is None else "FAIL", "correctly unusable for ASR")
        elif b == "groq":
            rec("ASR backend", f"{b}", "PASS" if p else "FAIL", (p or {}).get("name", "None"))
        else:
            rec("ASR backend", f"{b}", "INFO", (p or {}).get("name", "no key / not configured"))
    rec("ASR backend", "has_configured_backend()", "PASS" if transcriber.has_configured_backend() else "FAIL")


def test_llm_backends(tp):
    for b in ["groq", "cerebras", "gemini", "nvidia", "openai", "anthropic", "ollama", "custom"]:
        p = tp._llm_params(b)
        detail = f"{(p or {}).get('name', 'no key / not configured')} (kind={(p or {}).get('kind', '-')})"
        rec("LLM backend", b, "INFO" if p is None else "PASS", detail)


def test_brain_mode(config, transcriber):
    brain = getattr(config, "BRAIN_MODE", False)
    prim = getattr(config, "ASR_BACKEND", "groq")
    sec = getattr(config, "ASR_BRAIN_SECONDARY", "")
    if not brain:
        rec("Brain mode", "config", "INFO", "off")
    elif not sec or sec == prim:
        rec("Brain mode", "config", "WARN", f"secondary == primary ({prim}) -> no accuracy gain")
    elif transcriber._backend_params(sec):
        rec("Brain mode", f"dual-ASR {prim}+{sec}", "PASS", "both engines resolve -> real cross-check")
    else:
        rec("Brain mode", f"dual-ASR {prim}+{sec}", "WARN",
            f"secondary '{sec}' has NO KEY -> falls back to primary only")


def test_config_audit(config):
    combos = list(getattr(config, "HOTKEY_COMBOS", []))
    if "<alt>+<space>" in combos:
        rec("Config audit", "Alt+Space hotkey", "WARN",
            "PRESENT -> collides with Windows menu; accidental paste-without-wake")
    else:
        rec("Config audit", "Alt+Space hotkey", "PASS", "absent")
    llmb = getattr(config, "LLM_BACKEND", "groq")
    if llmb == "custom" and not getattr(config, "CUSTOM_LLM_URL", ""):
        rec("Config audit", "LLM_BACKEND", "FAIL", "'custom' but CUSTOM_LLM_URL empty -> cleanup broken")
    else:
        rec("Config audit", "LLM_BACKEND", "INFO", llmb)
    try:
        from pynput import keyboard as kb
        bad = []
        for c in combos:
            try:
                kb.HotKey.parse(c)
            except Exception as e:
                bad.append(f"{c}: {e}")
        rec("Hotkeys", "combo parse", "PASS" if not bad else "FAIL", "; ".join(bad) or f"{len(combos)} valid")
    except Exception as e:
        rec("Hotkeys", "combo parse", "WARN", f"pynput unavailable: {e}")


def test_live(config, tp):
    import requests
    gkey = (getattr(config, "GROQ_API_KEY", "") or "").strip()
    if not gkey:
        rec("LIVE", "Groq", "WARN", "no GROQ_API_KEY — skipping live calls")
        return
    try:
        r = requests.get("https://api.groq.com/openai/v1/models",
                         headers={"Authorization": f"Bearer {gkey}"}, timeout=10)
        rec("LIVE ASR", "Groq connectivity", "PASS" if r.status_code == 200 else "FAIL", f"HTTP {r.status_code}")
    except Exception as e:
        rec("LIVE ASR", "Groq connectivity", "FAIL", str(e)[:80])
    saved = config.LLM_BACKEND
    config.LLM_BACKEND = "groq"
    try:
        t0 = time.time()
        out = tp.TextProcessor().clean(
            "uh so basically i wanted to test whether the cleanup is uh working you know")
        dt = time.time() - t0
        good = bool(out) and "uh" not in out.lower().split() and len(out) > 8
        rec("LIVE LLM", "Groq cleanup", "PASS" if good else "FAIL", f"{dt:.1f}s -> {out!r}"[:120])
    except Exception as e:
        rec("LIVE LLM", "Groq cleanup", "FAIL", str(e)[:80])
    finally:
        config.LLM_BACKEND = saved


MANUAL = """
Manual checklist (must be done by hand on each laptop):
  1. Hold a hotkey (Ctrl+Alt+Space), speak, release -> text pastes into focused app
  2. Say the wake phrase -> READY widget arms -> speak -> it pastes
  3. Confirm random speech does NOT trigger the wake word
  4. (If voice enrolled) someone else's voice is rejected
  5. Config -> AI Backend -> per-key TEST and TEST LLM pass for each configured backend
  6. (Optional) add a Deepgram key + enable Brain Mode -> logs show '[Brain]' picking better transcript
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="skip all network/live calls")
    args = ap.parse_args()

    import config
    import transcriber
    import text_processor as tp

    match = _load_wake_matcher()
    test_wake_word(match, config)
    test_filters(transcriber)
    test_asr_backends(transcriber)
    test_llm_backends(tp)
    test_brain_mode(config, transcriber)
    test_config_audit(config)
    if not args.offline:
        test_live(config, tp)
    else:
        rec("LIVE", "skipped", "INFO", "--offline")

    print("\n" + "=" * 78)
    print("NibCast — Pipeline Self-Test")
    print("=" * 78)
    cur = None
    icons = {"PASS": "[OK]", "FAIL": "[XX]", "WARN": "[!!]", "INFO": "[--]"}
    for area, name, status, detail in ROWS:
        if area != cur:
            print(f"\n[{area}]"); cur = area
        print(f"  {icons[status]} {status:4} {name:40} {detail}")

    from collections import Counter
    c = Counter(r[2] for r in ROWS)
    print("\n" + "-" * 78)
    print(f"Totals: PASS={c['PASS']}  FAIL={c['FAIL']}  WARN={c['WARN']}  INFO={c['INFO']}")
    print(MANUAL)
    return 1 if c["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
