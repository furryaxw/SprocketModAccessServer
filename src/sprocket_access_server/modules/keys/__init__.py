from __future__ import annotations

from .resources import attach
from .._base import ResourceManifestModule

MODULE = ResourceManifestModule("keys", attach_handlers=attach)
