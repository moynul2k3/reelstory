import importlib
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.responses import HTMLResponse

ROUTES_DIR = Path(__file__).parent.parent / "routes"

SWAGGER_AUTH_SYNC_SCRIPT = """
<script>
(function () {
  const SESSION_CLASS = "cookie-authenticated";
  const SWAGGER_AUTH_STORAGE_KEY = "swagger-ui.auth";

  async function waitForUi(maxAttempts, delayMs) {
    for (let i = 0; i < maxAttempts; i++) {
      if (window.ui) return window.ui;
      await new Promise(function (resolve) { setTimeout(resolve, delayMs); });
    }
    return null;
  }

  function getTopAuthorizeButton() {
    return document.querySelector(".auth-wrapper .authorize");
  }

  function setTopButtonState(isLoggedIn) {
    const button = getTopAuthorizeButton();
    if (!button) return;

    const textNode = button.querySelector("span");
    if (textNode) {
      textNode.textContent = isLoggedIn ? "Log Out" : "Authorize";
    } else {
      button.textContent = isLoggedIn ? "Log Out" : "Authorize";
    }

    button.classList.toggle("locked", isLoggedIn);
    button.classList.toggle("unlocked", !isLoggedIn);
    button.classList.toggle("authorized", isLoggedIn);
    button.setAttribute("title", isLoggedIn ? "Log Out" : "Authorize");
  }

  function getSecuritySchemes(ui) {
    if (!ui || !ui.specSelectors) return {};
    const selector =
      (ui.specSelectors.securitySchemes && ui.specSelectors.securitySchemes()) ||
      (ui.specSelectors.securityDefinitions && ui.specSelectors.securityDefinitions()) ||
      null;
    return selector && selector.toJS ? selector.toJS() : {};
  }

  function resolveSchemeName(schemes) {
    if (!schemes || typeof schemes !== "object") return null;
    if (schemes.OAuth2PasswordBearer) return "OAuth2PasswordBearer";
    const keys = Object.keys(schemes);
    return keys.length ? keys[0] : null;
  }

  function preauthorizeToken(ui, token) {
    const schemes = getSecuritySchemes(ui);
    const schemeName = resolveSchemeName(schemes);
    if (!schemeName || !token) return;
    const scheme = schemes[schemeName] || {};

    if (ui.authActions && typeof ui.authActions.authorizeOauth2 === "function") {
      try {
        ui.authActions.authorizeOauth2({
          auth: { name: schemeName, schema: scheme },
          token: { access_token: token, token_type: "bearer" },
        });
      } catch (_error) {
        // Continue with fallback.
      }
    }

    if (ui.authActions && typeof ui.authActions.authorize === "function") {
      try {
        const auth = {};
        auth[schemeName] = {
          name: schemeName,
          schema: scheme,
          value: { access_token: token, token_type: "bearer" },
        };
        ui.authActions.authorize(auth);
      } catch (_error) {
        // Best-effort only.
      }
    }

    if (typeof ui.preauthorizeApiKey === "function") {
      try {
        ui.preauthorizeApiKey(schemeName, token);
      } catch (_error) {
        // Best-effort only.
      }
    }
  }

  function clearSwaggerClientAuth(ui) {
    if (ui && ui.authActions && typeof ui.authActions.logout === "function") {
      try {
        ui.authActions.logout();
      } catch (_error) {
        // Ignore client cleanup errors.
      }
    }

    try {
      localStorage.removeItem(SWAGGER_AUTH_STORAGE_KEY);
      sessionStorage.removeItem(SWAGGER_AUTH_STORAGE_KEY);
    } catch (_error) {
      // Storage may be blocked by browser policy.
    }
  }

  function buildUrl(path) {
    return new URL(path, window.location.href);
  }

  async function fetchSessionToken() {
    try {
      const response = await fetch(buildUrl("swagger-auth-token/"), {
        credentials: "include",
      });
      if (!response.ok) return null;
      const data = await response.json();
      return data && data.access_token ? data.access_token : null;
    } catch (_error) {
      return null;
    }
  }

  async function logoutServer() {
    try {
      await fetch(buildUrl("logout/"), {
        method: "POST",
        credentials: "include",
      });
    } catch (_error) {
      // Ignore network errors during logout.
    }
  }

  function applyLoggedInState(ui, token) {
    document.documentElement.classList.add(SESSION_CLASS);
    setTopButtonState(true);
    preauthorizeToken(ui, token);
  }

  function applyLoggedOutState(ui) {
    document.documentElement.classList.remove(SESSION_CLASS);
    setTopButtonState(false);
    clearSwaggerClientAuth(ui);
  }

  async function syncAuthState(ui) {
    const token = await fetchSessionToken();
    if (token) {
      applyLoggedInState(ui, token);
    } else {
      applyLoggedOutState(ui);
    }
  }

  function bindAuthorizeAsLogout(ui) {
    const button = getTopAuthorizeButton();
    if (!button || button.dataset.codexLogoutBound === "1") return;

    button.dataset.codexLogoutBound = "1";
    button.addEventListener(
      "click",
      async function (event) {
        const isLoggedIn = document.documentElement.classList.contains(SESSION_CLASS);
        if (!isLoggedIn) {
          return;
        }

        event.preventDefault();
        event.stopImmediatePropagation();

        await logoutServer();
        applyLoggedOutState(ui);
        window.location.reload();
      },
      true
    );
  }

  function observeSwaggerRenders(ui) {
    const observer = new MutationObserver(function () {
      bindAuthorizeAsLogout(ui);
      const isLoggedIn = document.documentElement.classList.contains(SESSION_CLASS);
      setTopButtonState(isLoggedIn);
    });

    observer.observe(document.body, { childList: true, subtree: true });
  }

  async function bootstrap() {
    const ui = await waitForUi(120, 50);
    if (!ui) return;

    bindAuthorizeAsLogout(ui);
    await syncAuthState(ui);
    observeSwaggerRenders(ui);

    setInterval(function () {
      syncAuthState(ui);
    }, 10000);
  }

  bootstrap();
})();
</script>
"""


def _inject_swagger_script(response: HTMLResponse) -> HTMLResponse:
    body = response.body.decode("utf-8")
    if "</body>" in body:
        body = body.replace("</body>", f"{SWAGGER_AUTH_SYNC_SCRIPT}</body>")
    else:
        body = f"{body}{SWAGGER_AUTH_SYNC_SCRIPT}"
    return HTMLResponse(content=body, status_code=response.status_code)


def _register_custom_docs(sub_app: FastAPI) -> None:
    @sub_app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html(request: Request):
        root_path = request.scope.get("root_path", "")
        openapi_url = f"{root_path}{sub_app.openapi_url}"
        oauth2_redirect_url = sub_app.swagger_ui_oauth2_redirect_url
        if oauth2_redirect_url:
            oauth2_redirect_url = f"{root_path}{oauth2_redirect_url}"

        response = get_swagger_ui_html(
            openapi_url=openapi_url,
            title=f"{sub_app.title} - Swagger UI",
            oauth2_redirect_url=oauth2_redirect_url,
            swagger_ui_parameters={"persistAuthorization": True},
        )
        return _inject_swagger_script(response)

    if sub_app.swagger_ui_oauth2_redirect_url:
        @sub_app.get(sub_app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
        async def swagger_ui_redirect():
            return get_swagger_ui_oauth2_redirect_html()


def register_routes(app: FastAPI):
    for sub_dir in ROUTES_DIR.iterdir():
        if not sub_dir.is_dir():
            continue

        sub_app = FastAPI(
            title=f"SubApp-{sub_dir.name}",
            docs_url=None,
            redoc_url=None,
        )
        _register_custom_docs(sub_app)
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
                    print(f"[routes] warning: no 'router' in {module_path}. Skipping.")
            except Exception as error:
                print(f"[routes] error loading {module_path}: {error}")

        if mounted:
            app.mount(f"/{sub_dir.name}", sub_app)
