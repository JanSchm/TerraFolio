"""TerraFolio — renewables portfolio optimiser.

The package is layered, and the layering is enforced by
``tests/unit/test_import_boundaries.py``::

    api -> runner -> optimiser -> economics -> pipeline -> config -> domain

``economics``, ``model`` and ``optimiser`` are the numeric core: they import
numpy, the standard library and ``config`` only. They never import pydantic,
HTTP or the store, which is what keeps them testable at 1e-12 and cheap to
start in a worker process.
"""

__version__ = "0.1.0"
