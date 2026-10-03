from __future__ import annotations

from .applications import MODULE as APPLICATIONS_MODULE
from .audit import MODULE as AUDIT_MODULE
from .keys import MODULE as KEYS_MODULE
from .packages import MODULE as PACKAGES_MODULE
from .permission_assignments import MODULE as PERMISSION_ASSIGNMENTS_MODULE
from .permission_templates import MODULE as PERMISSION_TEMPLATES_MODULE
from .system import MODULE as SYSTEM_MODULE
from .team import MODULE as TEAM_MODULE
from .users import MODULE as USERS_MODULE

DEFAULT_MODULES = (
    SYSTEM_MODULE,
    TEAM_MODULE,
    USERS_MODULE,
    PERMISSION_TEMPLATES_MODULE,
    PERMISSION_ASSIGNMENTS_MODULE,
    PACKAGES_MODULE,
    KEYS_MODULE,
    AUDIT_MODULE,
    APPLICATIONS_MODULE,
)
