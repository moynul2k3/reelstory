from enum import Enum

from passlib.handlers import bcrypt
from tortoise import fields
from tortoise.models import Model

class UserRole(str, Enum):
    USER = "USER"
    ADMIN = "ADMIN"


class Permission(Model):
    id = fields.IntField(pk=True, readonly=True, hidden=True)
    name = fields.CharField(max_length=100, unique=True, editable=False)
    codename = fields.CharField(max_length=100, unique=True, editable=False)

    def __str__(self):
        return self.codename


class Group(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=100, unique=True)

    permissions: fields.ManyToManyRelation["Permission"] = fields.ManyToManyField(
        "models.Permission",
        related_name="groups",
        through="group_permissions",
    )

    def __str__(self):
        return self.name


class User(Model):
    id = fields.UUIDField(pk=True, editable=False, hidden=True)
    username = fields.CharField(max_length=120, null=True, unique=True)
    password = fields.CharField(max_length=2000, default="")
    name = fields.CharField(max_length=100, null=True)
    language = fields.CharField(max_length=16, default="en")
    photo = fields.CharField(max_length=400, null=True)

    role = fields.CharEnumField(UserRole, default=UserRole.USER)

    is_active = fields.BooleanField(default=True)
    is_staff = fields.BooleanField(default=False)
    is_superuser = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    groups: fields.ManyToManyRelation["Group"] = fields.ManyToManyField(
        "models.Group",
        related_name="users",
        through="user_groups",
    )
    user_permissions: fields.ManyToManyRelation["Permission"] = fields.ManyToManyField(
        "models.Permission",
        related_name="users",
        through="user_permissions",
    )

    class Meta:
        table = "users"

    @classmethod
    def set_password(cls, password: str) -> str:
        return bcrypt.hash(password)

    def verify_password(self, password: str) -> bool:
        return bcrypt.verify(password, self.password)

    def __str__(self):
        display = self.username or f"user-{self.id}"
        return f"{display}"

    async def has_permission(self, codename: str) -> bool:
        if self.is_superuser:
            return True

        if not self.is_staff:
            return False

        await self.fetch_related("user_permissions", "groups__permissions")

        for perm in self.user_permissions:
            if perm.codename == codename:
                return True

        for group in self.groups:
            for perm in group.permissions:
                if perm.codename == codename:
                    return True
        return False

    async def save(self, *args, **kwargs):
        await super().save(*args, **kwargs)
