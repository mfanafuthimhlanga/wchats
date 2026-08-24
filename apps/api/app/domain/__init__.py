"""Frozen domain concepts: the bottom rung of the import-layers contract in pyproject.toml.

A module in here imports the standard library, third-party packages and its domain
siblings. It never imports `app.*` above this rung.
"""
