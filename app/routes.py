import importlib
from pathlib import Path
from fastapi import FastAPI, APIRouter

ROUTES_DIR = Path(__file__).parent.parent / "routes"

def register_routes(app: FastAPI):
    for sub_dir in ROUTES_DIR.iterdir():
        if not sub_dir.is_dir():
            continue

        sub_app = FastAPI(title=f"SubApp-{sub_dir.name}")
        mounted = False

        for py_file in sub_dir.glob("*.py"):
            if py_file.stem.startswith("__"):
                continue

            module_path = f"routes.{sub_dir.name}.{py_file.stem}"
            try:
                module = importlib.import_module(module_path)
                if hasattr(module, "router") and isinstance(module.router, APIRouter):
                    sub_app.include_router(module.router)
                    mounted = True
                else:
                    print(f"⚠️ No 'router' in {module_path}. Skipping.")
            except Exception as e:
                print(f"⚠️ Error loading {module_path}: {e}")

        if mounted:
            app.mount(f"/{sub_dir.name}", sub_app)
