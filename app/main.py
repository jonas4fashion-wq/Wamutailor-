import os
import sqlite3
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_FILE = BASE_DIR / "static" / "index.html"
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "wamutailor_demo.db"))
security = HTTPBasic()

app = FastAPI(
    title="WamuTailor Fashion Management",
    description="WamuTailor internal management system",
    version="0.2.0",
)

TABLES = {
    "customers": {
        "fields": ["name", "phone", "email", "address", "notes"],
        "required": ["name", "phone"],
    },
    "measurements": {
        "fields": ["customer_name", "customer_phone", "neck", "shoulder", "chest", "waist", "hip", "sleeve", "shirt_length", "trouser_length", "unit"],
        "required": ["customer_name", "customer_phone"],
    },
    "orders": {
        "fields": ["customer_name", "customer_phone", "product", "quantity", "unit_price", "total", "deposit", "balance", "delivery_date", "status", "notes"],
        "required": ["customer_name", "customer_phone", "product"],
    },
    "payments": {
        "fields": ["order_id", "customer_name", "amount", "method", "payment_date", "reference", "notes"],
        "required": ["order_id", "amount"],
    },
    "inventory": {
        "fields": ["item_name", "unit", "quantity", "unit_cost", "minimum_stock", "notes"],
        "required": ["item_name", "unit"],
    },
}


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with db() as con:
        for table, spec in TABLES.items():
            columns = ", ".join(f'"{field}" TEXT' for field in spec["fields"])
            con.execute(
                f'CREATE TABLE IF NOT EXISTS "{table}" (id INTEGER PRIMARY KEY AUTOINCREMENT, {columns}, created_at TEXT DEFAULT CURRENT_TIMESTAMP)'
            )


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    username = os.getenv("ADMIN_USERNAME", "")
    password = os.getenv("ADMIN_PASSWORD", "")
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin login is not configured. Set ADMIN_USERNAME and ADMIN_PASSWORD in Render environment variables.",
        )
    valid_user = secrets.compare_digest(credentials.username, username)
    valid_password = secrets.compare_digest(credentials.password, password)
    if not (valid_user and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.on_event("startup")
def startup():
    init_db()


@app.get("/health")
def health_check():
    return {"status": "ok", "application": "WamuTailor Fashion Management"}


@app.get("/", include_in_schema=False)
def dashboard():
    if not DASHBOARD_FILE.exists():
        return JSONResponse(status_code=503, content={"detail": "Dashboard file is not installed."})
    return FileResponse(DASHBOARD_FILE)


@app.get("/api/status")
def application_status(_: str = Depends(require_admin)):
    return {
        "application": "WamuTailor Fashion Management",
        "version": "0.2.0",
        "modules": list(TABLES.keys()),
        "database": "SQLite prototype; persistent PostgreSQL setup required before live business use",
        "authentication": "HTTP Basic authentication enabled when Render credentials are configured",
        "production_data_enabled": False,
    }


@app.get("/api/{module}")
def list_records(module: str, _: str = Depends(require_admin)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    with db() as con:
        rows = con.execute(f'SELECT * FROM "{module}" ORDER BY id DESC LIMIT 300').fetchall()
    return [dict(row) for row in rows]


@app.post("/api/{module}", status_code=201)
def create_record(module: str, payload: dict[str, Any], _: str = Depends(require_admin)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    spec = TABLES[module]
    missing = [field for field in spec["required"] if not str(payload.get(field, "")).strip()]
    if missing:
        raise HTTPException(422, f"Required fields: {', '.join(missing)}")
    data = {field: str(payload.get(field, "")).strip() for field in spec["fields"] if field in payload}
    columns = list(data.keys())
    if not columns:
        raise HTTPException(422, "No supported fields supplied")
    placeholders = ", ".join("?" for _ in columns)
    names = ", ".join(f'"{name}"' for name in columns)
    with db() as con:
        cursor = con.execute(
            f'INSERT INTO "{module}" ({names}) VALUES ({placeholders})',
            [data[name] for name in columns],
        )
        row = con.execute(f'SELECT * FROM "{module}" WHERE id = ?', (cursor.lastrowid,)).fetchone()
    return dict(row)


@app.put("/api/{module}/{record_id}")
def update_record(module: str, record_id: int, payload: dict[str, Any], _: str = Depends(require_admin)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    data = {field: str(payload[field]).strip() for field in TABLES[module]["fields"] if field in payload}
    if not data:
        raise HTTPException(422, "No supported fields supplied")
    assignments = ", ".join(f'"{field}" = ?' for field in data)
    with db() as con:
        cursor = con.execute(
            f'UPDATE "{module}" SET {assignments} WHERE id = ?',
            [*data.values(), record_id],
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "Record not found")
        row = con.execute(f'SELECT * FROM "{module}" WHERE id = ?', (record_id,)).fetchone()
    return dict(row)


@app.delete("/api/{module}/{record_id}")
def delete_record(module: str, record_id: int, _: str = Depends(require_admin)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    with db() as con:
        cursor = con.execute(f'DELETE FROM "{module}" WHERE id = ?', (record_id,))
        if cursor.rowcount == 0:
            raise HTTPException(404, "Record not found")
    return {"deleted": True, "id": record_id}
