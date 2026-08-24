"""
Build wheel

Use: python setup.py bdist_wheel
"""

from pathlib import Path

from setuptools import setup

version = Path(__file__).with_name("VERSION").read_text(encoding="utf-8").strip()

_ = setup(
    version=version,
)
