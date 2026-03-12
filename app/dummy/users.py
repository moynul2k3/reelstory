from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from applications.user.models import User, UserRole

USERS_DATA = [
    {
        "username": "admin@gmail.com",
        "password": "admin",
        "name": "Admin User",
        "role": UserRole.ADMIN,
        "is_staff": True,
        "is_superuser": True,
        "is_active": True,
    },
    {
        "username": "staff@gmail.com",
        "password": "staff",
        "name": "Staff User",
        "role": UserRole.USER,
        "is_staff": True,
        "is_superuser": False,
        "is_active": True,
    },
]


async def create_test_users():
    created_count = 0
    updated_count = 0
    for data in USERS_DATA:
        username = data["username"]
        try:
            async with in_transaction() as conn:
                defaults = {
                    "username": username,
                    "name": data.get("name"),
                    "role": data.get("role", UserRole.USER),
                    "is_active": data.get("is_active", True),
                    "is_staff": data.get("is_staff", False),
                    "is_superuser": data.get("is_superuser", False),
                    "password": User.set_password(data["password"]),
                }

                user, created = await User.get_or_create(
                    username=username,
                    defaults=defaults,
                    using_db=conn,
                )

                if created:
                    created_count += 1
                    print(f"[dummy-user] created: {username}")
                    continue

                updated = False
                for field in ["name", "role", "is_active", "is_staff", "is_superuser"]:
                    expected = defaults[field]
                    if getattr(user, field) != expected:
                        setattr(user, field, expected)
                        updated = True

                # Keep seeded credentials deterministic so login always works.
                password_valid = False
                if user.password:
                    try:
                        password_valid = user.verify_password(data["password"])
                    except Exception:
                        password_valid = False

                if not password_valid:
                    user.password = defaults["password"]
                    updated = True

                if updated:
                    await user.save(using_db=conn)
                    updated_count += 1
                    print(f"[dummy-user] updated: {username}")
                else:
                    print(f"[dummy-user] exists: {username}")
        except IntegrityError as error:
            print(f"[dummy-user] integrity error for {username}: {error}")
        except Exception as error:
            print(f"[dummy-user] unexpected error for {username}: {error}")

    print(f"[dummy-user] seeding completed (created={created_count}, updated={updated_count})")
