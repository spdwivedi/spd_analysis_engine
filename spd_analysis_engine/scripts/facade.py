"""
scripts/facade.py
=================
Compatibility facade system ensuring legacy 'scripts.*' imports map directly
to the canonical 'spd_analysis_engine.scripts.*' namespace without duplicate
module instances or singletons.
"""
from __future__ import annotations

import importlib
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from typing import Any

CANONICAL_PREFIX = "spd_analysis_engine.scripts"
LEGACY_PREFIX = "scripts"


class LegacyScriptsFacadeFinder(MetaPathFinder):
    """
    Two-way MetaPathFinder that unifies 'scripts.*' and 'spd_analysis_engine.scripts.*'
    into the exact same module instances in sys.modules.
    """

    _active = False

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> ModuleSpec | None:
        if self._active:
            return None

        # Direction 1: Legacy 'scripts.*' requested
        if fullname == LEGACY_PREFIX or fullname.startswith(LEGACY_PREFIX + "."):
            canonical_name = CANONICAL_PREFIX + fullname[len(LEGACY_PREFIX):]

            # If canonical module is already loaded, reuse it
            if canonical_name in sys.modules:
                mod = sys.modules[canonical_name]
            else:
                self._active = True
                try:
                    mod = importlib.import_module(canonical_name)
                except Exception:
                    return None
                finally:
                    self._active = False

            sys.modules[fullname] = mod
            if "." in fullname:
                parent_name, attr = fullname.rsplit(".", 1)
                if parent_name in sys.modules:
                    setattr(sys.modules[parent_name], attr, mod)

            orig_loader = getattr(mod, "__loader__", None)

            class _LegacyFacadeLoader(Loader):
                def __init__(self, orig: Any = None) -> None:
                    self._orig = orig

                def __getattr__(self, name: str) -> Any:
                    if self._orig:
                        return getattr(self._orig, name)
                    raise AttributeError(name)

                def get_code(self, name: str | None = None) -> Any:
                    if self._orig and hasattr(self._orig, "get_code"):
                        target_name = getattr(self._orig, "name", canonical_name)
                        return self._orig.get_code(target_name)
                    return None

                def get_source(self, name: str | None = None) -> Any:
                    if self._orig and hasattr(self._orig, "get_source"):
                        target_name = getattr(self._orig, "name", canonical_name)
                        return self._orig.get_source(target_name)
                    return None

                def get_filename(self, name: str | None = None) -> Any:
                    if self._orig and hasattr(self._orig, "get_filename"):
                        target_name = getattr(self._orig, "name", canonical_name)
                        return self._orig.get_filename(target_name)
                    return None

                def is_package(self, name: str | None = None) -> bool:
                    if self._orig and hasattr(self._orig, "is_package"):
                        target_name = getattr(self._orig, "name", canonical_name)
                        return self._orig.is_package(target_name)
                    return hasattr(mod, "__path__")

                def create_module(self, spec: Any) -> Any:
                    return mod

                def exec_module(self, module: Any) -> None:
                    pass

            is_pkg = hasattr(mod, "__path__")
            spec = ModuleSpec(fullname, _LegacyFacadeLoader(orig_loader), is_package=is_pkg)
            if is_pkg:
                spec.submodule_search_locations = list(mod.__path__)
            return spec

        # Direction 2: Canonical 'spd_analysis_engine.scripts.*' requested
        if fullname == CANONICAL_PREFIX or fullname.startswith(CANONICAL_PREFIX + "."):
            legacy_name = fullname[len("spd_analysis_engine."):]
            self._active = True
            try:
                for finder in sys.meta_path:
                    if finder is self:
                        continue
                    if hasattr(finder, "find_spec"):
                        spec = finder.find_spec(fullname, path, target)
                        if spec and spec.loader:
                            orig_loader = spec.loader

                            class _TwinRegisteringLoader(Loader):
                                def __init__(self, loader: Any) -> None:
                                    self._orig = loader

                                def __getattr__(self, name: str) -> Any:
                                    return getattr(self._orig, name)

                                def get_code(self, name: str | None = None) -> Any:
                                    if hasattr(self._orig, "get_code"):
                                        target_name = getattr(self._orig, "name", fullname)
                                        return self._orig.get_code(target_name)
                                    return None

                                def get_source(self, name: str | None = None) -> Any:
                                    if hasattr(self._orig, "get_source"):
                                        target_name = getattr(self._orig, "name", fullname)
                                        return self._orig.get_source(target_name)
                                    return None

                                def get_filename(self, name: str | None = None) -> Any:
                                    if hasattr(self._orig, "get_filename"):
                                        target_name = getattr(self._orig, "name", fullname)
                                        return self._orig.get_filename(target_name)
                                    return None

                                def create_module(self, s: Any) -> Any:
                                    if hasattr(self._orig, "create_module"):
                                        return self._orig.create_module(s)
                                    return None

                                def exec_module(self, m: Any) -> None:
                                    self._orig.exec_module(m)
                                    sys.modules[legacy_name] = m
                                    if "." in legacy_name:
                                        p_name, a = legacy_name.rsplit(".", 1)
                                        if p_name in sys.modules:
                                            setattr(sys.modules[p_name], a, m)

                            spec.loader = _TwinRegisteringLoader(orig_loader)
                            return spec
            finally:
                self._active = False

        return None


def register_module_alias(name: str, module: Any = None) -> None:
    """Register reciprocal alias between canonical and legacy module in sys.modules."""
    if not name:
        return
    mod = module or sys.modules.get(name)
    if mod is None:
        return

    if name.startswith(CANONICAL_PREFIX):
        twin = LEGACY_PREFIX + name[len(CANONICAL_PREFIX):]
    elif name.startswith(LEGACY_PREFIX):
        twin = CANONICAL_PREFIX + name[len(LEGACY_PREFIX):]
    else:
        return

    sys.modules.setdefault(name, mod)
    sys.modules.setdefault(twin, mod)
    if "." in twin:
        parent_name, attr = twin.rsplit(".", 1)
        parent_mod = sys.modules.get(parent_name)
        if parent_mod is not None:
            setattr(parent_mod, attr, mod)


def install_facade_finder() -> None:
    """Install LegacyScriptsFacadeFinder in sys.meta_path if not already present."""
    for finder in sys.meta_path:
        if isinstance(finder, LegacyScriptsFacadeFinder):
            return
    sys.meta_path.insert(0, LegacyScriptsFacadeFinder())
