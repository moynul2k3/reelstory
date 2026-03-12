import os
import importlib
from contextlib import asynccontextmanager
from app.task_config import start
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.config import settings, init_db
from app.redis import init_redis, redis_client
from app.routes import register_routes
from app.utils.sync_permissions import sync_permissions
from app.utils.auto_routing import get_module
from app.dummy.users import create_test_users
from app.dummy.reels import create_test_reels_data

# import logging
# logging.basicConfig(level=logging.DEBUG)

@asynccontextmanager
async def lifespan(routerAPI: FastAPI):
    await init_db()
    init_redis()
    start()
    await sync_permissions()

    if settings.ENV != "production":
        try:
            await create_test_users()
        except Exception as error:
            print(f"[dummy-user] startup seeding failed: {error}")

        try:
            await create_test_reels_data()
        except Exception as error:
            print(f"[dummy-reels] startup seeding failed: {error}")
        
    
    for app_name in get_module(base_dir="applications"):
        try:
            importlib.import_module(f"applications.{app_name}.signals")
        except ModuleNotFoundError:
            print(f"[startup] warning: no signals.py in '{app_name}' sub-app.")
    yield
    if redis_client:
        await redis_client.aclose()
    print("Application shutdown complete.")

app = FastAPI(
    lifespan=lifespan,
    debug=settings.DEBUG,
    swagger_ui_parameters={"persistAuthorization": True},
)
register_routes(app)

# if settings.DEBUG else "index.html"

templates = Jinja2Templates(directory="templates")
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    routes = get_module()
    html_file = "development.html"
    return templates.TemplateResponse(
        html_file,
        {
            "request": request, 
            "routes": routes,
            "image_url": "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=1920&q=80"
        }
    )


ALLOWED_HOST = ["http://localhost:3000", "http://127.0.0.1:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_HOST,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


os.makedirs(settings.MEDIA_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory="templates/static"), name="static")
app.mount("/media", StaticFiles(directory=settings.MEDIA_DIR), name="media")
