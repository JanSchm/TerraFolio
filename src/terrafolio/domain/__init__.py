"""Enumerations, conventions and the pydantic models for files, mandates and results.

This module is deliberately **empty of imports**. ``config`` imports
``domain.enums``, and the numeric core imports ``config`` — so a convenience
re-export here would drag pydantic into ``optimiser`` through an edge the
dependency rules allow. Import the submodule you need directly::

    from terrafolio.domain.enums import Technology

``tests/unit/test_import_boundaries.py`` asserts this file has no imports.
"""
