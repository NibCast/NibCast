# ============================================================
#  NibCast — Pipeline Sanity Checker
# ============================================================
#  Static check for the "instance.method() vs module_function()"
#  mismatch that caused the has_configured_backend() crash: code
#  calling `var.attr()` where `var = SomeClass(...)` but `attr`
#  is only defined as a module-level function in that class's
#  module, not as a method on the class.
#
#  Run standalone:   python check_pipeline.py
#  Wired into build_exe.py as a pre-build gate.
# ============================================================

import ast
import importlib
import inspect
import pathlib
import sys


def check(root_dir: str) -> list[str]:
    """Return a list of human-readable finding strings (empty list = clean)."""
    root = pathlib.Path(root_dir)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    findings = []

    for pyfile in root.glob("*.py"):
        src = pyfile.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(src, filename=str(pyfile))
        except SyntaxError as e:
            findings.append(f"{pyfile.name}: SYNTAX ERROR — {e}")
            continue

        from_imports = {}    # local_name -> (module_name, class_or_func_name)
        module_imports = {}  # local_name -> module_name

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    local = alias.asname or alias.name
                    from_imports[local] = (node.module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    module_imports[local] = alias.name

        # var_name -> (module_name, class_name), for `var = ClassName(...)`
        var_classes = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                tgt, val = node.targets[0], node.value
                if (isinstance(tgt, ast.Name) and isinstance(val, ast.Call)
                        and isinstance(val.func, ast.Name) and val.func.id in from_imports):
                    mod_name, cls_name = from_imports[val.func.id]
                    var_classes[tgt.id] = (mod_name, cls_name)

        mod_cache, cls_cache = {}, {}

        def get_module(name):
            if name not in mod_cache:
                try:
                    mod_cache[name] = importlib.import_module(name)
                except Exception:
                    mod_cache[name] = None
            return mod_cache[name]

        def get_class(mod_name, cls_name):
            key = (mod_name, cls_name)
            if key not in cls_cache:
                mod = get_module(mod_name)
                cls_cache[key] = getattr(mod, cls_name, None) if mod else None
            return cls_cache[key]

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)):
                continue
            obj_name, attr, lineno = node.func.value.id, node.func.attr, node.lineno

            if obj_name not in var_classes:
                continue
            mod_name, cls_name = var_classes[obj_name]
            cls, mod = get_class(mod_name, cls_name), get_module(mod_name)
            if cls is None or mod is None or hasattr(cls, attr):
                continue

            mod_attr = getattr(mod, attr, None)
            is_module_func = (inspect.isfunction(mod_attr)
                               and getattr(mod_attr, "__module__", None) == mod.__name__)

            # Suppress if `obj_name` is ALSO bound via `import X as <obj_name>`
            # elsewhere in the file and that module defines `attr` -- the call
            # may be hitting that binding instead of the instance (e.g.
            # `tp = TextProcessor()` plus a later `import text_processor as tp`).
            if obj_name in module_imports:
                alias_mod = get_module(module_imports[obj_name])
                if alias_mod is not None and hasattr(alias_mod, attr):
                    continue

            if is_module_func:
                findings.append(
                    f"{pyfile.name}:{lineno}: {obj_name}.{attr}() -- {cls_name} has no "
                    f"'{attr}' member, but {mod_name}.{attr}() exists as a module-level "
                    f"function (instance/module-function mismatch)")
            elif mod_attr is None and not attr.startswith("_"):
                findings.append(
                    f"{pyfile.name}:{lineno}: {obj_name}.{attr}() -- {cls_name} (from "
                    f"{mod_name}) has no '{attr}' member at all (AttributeError at runtime)")

    return findings


if __name__ == "__main__":
    results = check(str(pathlib.Path(__file__).resolve().parent))
    if not results:
        print("check_pipeline: no issues found.")
    else:
        for r in results:
            print("check_pipeline:", r)
        print(f"\ncheck_pipeline: {len(results)} issue(s) found.")
        sys.exit(1)
