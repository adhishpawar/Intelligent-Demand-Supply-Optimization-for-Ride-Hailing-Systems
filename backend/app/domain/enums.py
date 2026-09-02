"""Domain enums shared across models, schemas, and RBAC.

Roles are deliberately few. The master prompt warns against creating every
conceivable role; this product has one real axis of variation (how much of one
tenant's operation you can see/change) plus one cross-cutting concern (platform
operator vs. tenant staff), so four roles cover it:

    SUPER_ADMIN   - platform operator staff. Not scoped to a tenant.
    TENANT_ADMIN  - owns a tenant's account: manages users, cities, zones.
    OPS_ANALYST   - day-to-day user: reads forecasts, can trigger a refresh.
    VIEWER        - read-only. Dashboards, no mutation of anything.
"""

from __future__ import annotations

import enum


class Role(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    TENANT_ADMIN = "TENANT_ADMIN"
    OPS_ANALYST = "OPS_ANALYST"
    VIEWER = "VIEWER"
