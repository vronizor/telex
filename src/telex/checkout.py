import importlib.util
from pathlib import Path
from types import ModuleType

_dispatch_cache: dict[Path, tuple[float, ModuleType]] = {}


def dispatch(root: Path) -> ModuleType:
    path = root / "scripts" / "dispatch.py"
    mtime = path.stat().st_mtime
    cached = _dispatch_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    spec = importlib.util.spec_from_file_location("telex_dispatch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _dispatch_cache[path] = (mtime, module)
    return module
