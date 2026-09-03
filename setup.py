"""Legacy setuptools entry point.

All project metadata, dependencies, and package discovery live in
``pyproject.toml``.  Keeping this shim allows older tooling that still invokes
``python setup.py`` to use the same single source of truth without maintaining
a second dependency list.
"""

from setuptools import setup


setup()
