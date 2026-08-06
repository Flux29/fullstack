"""Renderers turn manifests into documents people read.

A renderer never discovers anything. If a fact is not in a manifest it does not appear in
the rendered document, which is what keeps the prose and the JSON from drifting apart — the
condition that made ENV_VARS.md document variables that did not exist.
"""

from __future__ import annotations

__all__: list[str] = []
