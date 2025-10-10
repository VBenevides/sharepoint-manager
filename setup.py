"""
Build wheel

Use: python setup.py bdist_wheel
"""

from setuptools import setup

_ = setup(
    name="sharepoint_manager",
    version="0.0.3",
    packages=["sharepoint_manager"],
    url="https://github.com/VBenevides/sharepoint_manager",
    license="MIT",
    author="Vinicius Benevides",
    author_email="viniciusm.benevides@gmail.com",
    description="Library for interacting with sharepoint using Microsoft Graph API",
    install_requires=["msal", "automation-logging"],
)
