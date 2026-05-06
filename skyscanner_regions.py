import sys
from importlib import import_module

_module = import_module("skyscanner_multi_domain.geo.regions")
globals().update(
    {
        name: value
        for name, value in vars(_module).items()
        if not (name.startswith("__") and name.endswith("__"))
    }
)
__all__ = [name for name in vars(_module) if not (name.startswith("__") and name.endswith("__"))]
_module.__all__ = __all__
sys.modules[__name__] = _module
