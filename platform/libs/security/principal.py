from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    RIDER = "RIDER"
    DRIVER = "DRIVER"
    ADMIN = "ADMIN"


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: Role
    phone: str

    def has_role(self, *roles: Role) -> bool:
        return self.role in roles
