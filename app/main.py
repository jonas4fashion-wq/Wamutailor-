from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_FILE = BASE_DIR / "static" / "index.html"

app = FastAPI(
    title="WamuTailor Fashion Management",
    description="WamuTailor internal management system API",
    version="0.1.0",
)


@app.get("/", include_in_schema=False)
def dashboard():
    if not DASHBOARD_FILE.exists():
        return JSONResponse(
            status_code=503,
            content={"detail": "Dashboard file is not installed."},
        )
    return FileResponse(DASHBOARD_FILE)


@app.get("/health")
def health_check():
    return {"status": "ok", "application": "WamuTailor Fashion Management"}


@app.get("/api/status")
def application_status():
    return {
        "application": "WamuTailor Fashion Management",
        "version": "0.1.0",
        "stage": "deployment foundation",
        "database": "not connected yet",
        "authentication": "not configured yet",
        "production_data_enabled": False,
    }
