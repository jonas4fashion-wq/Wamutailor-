import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import Column, Integer, MetaData, Table, Text, create_engine, select, text

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_FILE = BASE_DIR / "static" / "index.html"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'wamutailor_demo.db'}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
metadata = MetaData()
security = HTTPBasic()

TABLES = {
    "customers": {"fields": ["name", "phone", "email", "address", "notes"], "required": ["name", "phone"]},
    "measurements": {"fields": ["customer_name", "customer_phone", "neck", "shoulder", "chest", "waist", "hip", "sleeve", "shirt_length", "trouser_length", "unit"], "required": ["customer_name", "customer_phone"]},
    "orders": {"fields": ["customer_name", "customer_phone", "product", "quantity", "unit_price", "total", "deposit", "balance", "delivery_date", "status", "notes"], "required": ["customer_name", "customer_phone", "product"]},
    "payments": {"fields": ["order_id", "customer_name", "amount", "method", "payment_date", "reference", "notes"], "required": ["order_id", "amount"]},
    "inventory": {"fields": ["item_name", "unit", "quantity", "unit_cost", "minimum_stock", "notes"], "required": ["item_name", "unit"]},
}
TABLE_OBJECTS = {}
for table_name, spec in TABLES.items():
    TABLE_OBJECTS[table_name] = Table(
        table_name, metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        *[Column(field, Text, nullable=True) for field in spec["fields"]],
        Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
    )

app = FastAPI(title="WamuTailor Fashion Management", description="WamuTailor internal management system", version="0.3.0")


@app.on_event("startup")
def startup():
    metadata.create_all(engine)


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    username = os.getenv("ADMIN_USERNAME", "")
    password = os.getenv("ADMIN_PASSWORD", "")
    if not username or not password:
        raise HTTPException(status_code=503, detail="Admin login is not configured. Set ADMIN_USERNAME and ADMIN_PASSWORD in Render environment variables.")
    valid_user = secrets.compare_digest(credentials.username, username)
    valid_password = secrets.compare_digest(credentials.password, password)
    if not (valid_user and valid_password):
        raise HTTPException(status_code=401, detail="Incorrect username or password", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@app.get("/", include_in_schema=False)
def dashboard():
    if not DASHBOARD_FILE.exists():
        return JSONResponse(status_code=503, content={"detail": "Dashboard file is not installed."})
    return FileResponse(DASHBOARD_FILE)


@app.get("/health")
def health_check():
    return {"status": "ok", "application": "WamuTailor Fashion Management"}


@app.get("/api/status")
def application_status(_: str = Depends(require_admin)):
    return {
        "application": "WamuTailor Fashion Management",
        "version": "0.3.0",
        "modules": list(TABLES.keys()),
        "database": "PostgreSQL" if DATABASE_URL.startswith("postgresql") else "SQLite local fallback",
        "authentication": "HTTP Basic authentication",
        "warning": "Testing release. Complete production security review and backups before entering real customer records.",
    }


@app.get("/api/{module}")
def list_records(module: str, _: str = Depends(require_admin)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    table = TABLE_OBJECTS[module]
    with engine.connect() as con:
        rows = con.execute(select(table).order_by(table.c.id.desc()).limit(300)).mappings().all()
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
    if not data:
        raise HTTPException(422, "No supported fields supplied")
    table = TABLE_OBJECTS[module]
    with engine.begin() as con:
        result = con.execute(table.insert().values(**data))
        record_id = result.inserted_primary_key[0]
        row = con.execute(select(table).where(table.c.id == record_id)).mappings().one()
    return dict(row)


@app.put("/api/{module}/{record_id}")
def update_record(module: str, record_id: int, payload: dict[str, Any], _: str = Depends(require_admin)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    data = {field: str(payload[field]).strip() for field in TABLES[module]["fields"] if field in payload}
    if not data:
        raise HTTPException(422, "No supported fields supplied")
    table = TABLE_OBJECTS[module]
    with engine.begin() as con:
        result = con.execute(table.update().where(table.c.id == record_id).values(**data))
        if result.rowcount == 0:
            raise HTTPException(404, "Record not found")
        row = con.execute(select(table).where(table.c.id == record_id)).mappings().one()
    return dict(row)


@app.delete("/api/{module}/{record_id}")
def delete_record(module: str, record_id: int, _: str = Depends(require_admin)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    table = TABLE_OBJECTS[module]
    with engine.begin() as con:
        result = con.execute(table.delete().where(table.c.id == record_id))
        if result.rowcount == 0:
            raise HTTPException(404, "Record not found")
    return {"deleted": True, "id": record_id}
