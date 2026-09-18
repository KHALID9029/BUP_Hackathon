import logging
import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .schemas import OptimizeRequest, OptimizeResponse
from .state import GraphState
from .graph import build_graph
from .llm import LLMNotConfigured
from .optimizer import InfeasibleScenario

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gridwise")

class NormalizePathMiddleware:
    """Tolerate sloppy base-URL joins: `//optimize-energy` and `/optimize-energy/` route like `/optimize-energy`
    (instead of a 404, or a 307 redirect that many clients don't follow on POST)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = re.sub(r"/{2,}", "/", scope["path"])
            if len(path) > 1:
                path = path.rstrip("/")
            if path != scope["path"]:
                scope = dict(scope, path=path, raw_path=path.encode())
        await self.app(scope, receive, send)


app = FastAPI(title="GridWise LLM")
app.add_middleware(NormalizePathMiddleware)
_graph = None


def get_graph():
    """Lazy: import and /health never touch the LLM client or the API key."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


@app.exception_handler(RequestValidationError)
async def _bad_request(_: Request, exc: RequestValidationError):
    # Covers invalid JSON bodies and schema violations → 400 as required by the contract.
    errors = [{"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg", "")} for e in exc.errors()]
    return JSONResponse(status_code=400, content={"error": "invalid_request", "detail": errors})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(req: OptimizeRequest):
    try:
        out = await get_graph().ainvoke(GraphState(request=req))
    except LLMNotConfigured:
        log.error("LLM not configured")
        return JSONResponse(status_code=500, content={"error": "llm_not_configured"})
    except InfeasibleScenario as e:
        return JSONResponse(status_code=422, content={"error": "infeasible_scenario", "detail": str(e)})
    except Exception:
        log.exception("optimize-energy failed for scenario %s", req.scenario_id)
        return JSONResponse(status_code=500, content={"error": "internal_error"})
    for w in out.get("warnings", []):
        log.warning("%s: %s", req.scenario_id, w)
    return OptimizeResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=out["directives"],
        hourly_plan=out["plan"],
        total_grid_kwh=out["total_grid_kwh"],
        total_cost_bdt=out["total_cost_bdt"],
        peak_grid_kwh=out["peak_grid_kwh"],
        plan_summary=out["plan_summary"],
    )


# Optional web UI, registered after the API routes. Only `/` and `/assets/*` are added, so the
# status behaviour of /health and /optimize-energy is unchanged; if the build is absent, nothing is mounted.
_ui = Path(settings.FRONTEND_DIST)
if (_ui / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=_ui / "assets", check_dir=False), name="assets")

    @app.get("/", include_in_schema=False)
    async def ui():
        return FileResponse(_ui / "index.html")
