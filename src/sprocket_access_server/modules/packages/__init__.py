from __future__ import annotations

from .resources import attach
from .._base import ResourceManifestModule

MODULE = ResourceManifestModule("packages", attach_handlers=attach)
