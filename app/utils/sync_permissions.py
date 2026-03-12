from applications.user.models import Permission
from tortoise import Tortoise

DEFAULT_ACTIONS = ["view", "add", "update", "delete"]

async def sync_permissions():
    apps = Tortoise.apps
    existing_models = []
    for app, models in apps.items():
        for model_name, model in models.items():
            if model.__module__.startswith("applications."):
                existing_models.append(model_name.lower())
                for action in DEFAULT_ACTIONS:
                    codename = f"{action}_{model_name.lower()}"
                    name = f"Can {action} {model_name}"
                    await Permission.get_or_create(codename=codename, defaults={"name": name})

    
    valid_codenames = [f"{action}_{m}" for m in existing_models for action in DEFAULT_ACTIONS]
    await Permission.exclude(codename__in=valid_codenames).delete()
    
    