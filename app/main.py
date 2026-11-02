import os
import secrets
import hashlib
import hmac
import base64
import json
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
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
bearer = HTTPBearer(auto_error=False)

TABLES = {
    "customers": {"fields": ["name", "phone", "email", "address", "notes"], "required": ["name", "phone"]},
    "measurements": {"fields": ["customer_name", "customer_phone", "neck", "shoulder", "chest", "waist", "hip", "sleeve", "shirt_length", "trouser_length", "unit"], "required": ["customer_name", "customer_phone"]},
    "orders": {"fields": ["customer_name", "customer_phone", "product", "quantity", "unit_price", "total", "deposit", "balance", "delivery_date", "status", "notes"], "required": ["customer_name", "customer_phone", "product"]},
    "payments": {"fields": ["order_id", "customer_name", "amount", "method", "payment_date", "reference", "notes"], "required": ["order_id", "amount"]},
    "inventory": {"fields": ["item_name", "unit", "quantity", "unit_cost", "minimum_stock", "notes"], "required": ["item_name", "unit"]},
    "products": {"fields": ["name", "sku", "category", "description", "price", "currency", "active"], "required": ["name", "price"]},
}
TABLE_OBJECTS = {}
for table_name, spec in TABLES.items():
    TABLE_OBJECTS[table_name] = Table(
        table_name, metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        *[Column(field, Text, nullable=True) for field in spec["fields"]],
        Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
        *([Column("portal_password_hash", Text, nullable=True)] if table_name == "customers" else []),
    )

app = FastAPI(title="WamuTailor Fashion Management", description="WamuTailor internal management system", version="0.3.0")


@app.on_event("startup")
def startup():
    metadata.create_all(engine)
    with engine.begin() as con:
        if engine.dialect.name == "sqlite":
            cols = [row[1] for row in con.execute(text("PRAGMA table_info(customers)"))]
        else:
            cols = [row[0] for row in con.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'customers'"))]
        if "portal_password_hash" not in cols:
            con.execute(text("ALTER TABLE customers ADD COLUMN portal_password_hash TEXT"))


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


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_b64, digest_b64 = stored.split("$", 1)
        salt = base64.urlsafe_b64decode(salt_b64)
        expected = base64.urlsafe_b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def issue_client_token(customer_id: int) -> str:
    secret = os.getenv("CLIENT_TOKEN_SECRET", "")
    if not secret:
        raise HTTPException(503, "Client portal is not configured. Set CLIENT_TOKEN_SECRET in Render.")
    payload = base64.urlsafe_b64encode(json.dumps({"cid": customer_id, "exp": int(time.time()) + 86400}).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def require_client(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Please sign in", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload, signature = credentials.credentials.split(".", 1)
        secret = os.getenv("CLIENT_TOKEN_SECRET", "")
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not secret or not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid signature")
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if int(data["exp"]) < int(time.time()):
            raise ValueError("Expired token")
        table = TABLE_OBJECTS["customers"]
        with engine.connect() as con:
            customer = con.execute(select(table).where(table.c.id == int(data["cid"]))).mappings().first()
        if not customer or not customer.get("portal_password_hash"):
            raise ValueError("Unknown customer")
        return dict(customer)
    except Exception:
        raise HTTPException(401, "Session expired. Please sign in again.", headers={"WWW-Authenticate": "Bearer"})


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


@app.get("/portal", include_in_schema=False)
def client_portal():
    portal_file = BASE_DIR / "static" / "portal.html"
    if not portal_file.exists():
        return JSONResponse(status_code=503, content={"detail": "Client portal is not installed."})
    return FileResponse(portal_file)


@app.post("/api/admin/customers/{customer_id}/portal-access")
def enable_client_portal(customer_id: int, payload: dict[str, Any], _: str = Depends(require_admin)):
    password = str(payload.get("password", ""))
    if len(password) < 10:
        raise HTTPException(422, "Client password must be at least 10 characters.")
    table = TABLE_OBJECTS["customers"]
    with engine.begin() as con:
        customer = con.execute(select(table).where(table.c.id == customer_id)).mappings().first()
        if not customer:
            raise HTTPException(404, "Customer not found")
        con.execute(table.update().where(table.c.id == customer_id).values(portal_password_hash=hash_password(password)))
    return {"enabled": True, "customer_id": customer_id, "phone": customer["phone"]}


@app.post("/api/client/login")
def client_login(payload: dict[str, Any]):
    phone = str(payload.get("phone", "")).strip()
    password = str(payload.get("password", ""))
    if not phone or not password:
        raise HTTPException(422, "Phone and password are required")
    table = TABLE_OBJECTS["customers"]
    with engine.connect() as con:
        customer = con.execute(select(table).where(table.c.phone == phone)).mappings().first()
    if not customer or not customer.get("portal_password_hash") or not verify_password(password, customer["portal_password_hash"]):
        raise HTTPException(401, "Phone or password is incorrect")
    return {"access_token": issue_client_token(int(customer["id"])), "token_type": "bearer", "customer": {"name": customer["name"], "phone": customer["phone"]}}


@app.get("/api/client/me")
def client_dashboard(customer: dict = Depends(require_client)):
    phone = customer["phone"]
    with engine.connect() as con:
        orders = con.execute(select(TABLE_OBJECTS["orders"]).where(TABLE_OBJECTS["orders"].c.customer_phone == phone).order_by(TABLE_OBJECTS["orders"].c.id.desc())).mappings().all()
        measurements = con.execute(select(TABLE_OBJECTS["measurements"]).where(TABLE_OBJECTS["measurements"].c.customer_phone == phone).order_by(TABLE_OBJECTS["measurements"].c.id.desc())).mappings().all()
        own_order_ids = [str(order["id"]) for order in orders]
        if own_order_ids:
            payments = con.execute(select(TABLE_OBJECTS["payments"]).where(TABLE_OBJECTS["payments"].c.order_id.in_(own_order_ids)).order_by(TABLE_OBJECTS["payments"].c.id.desc())).mappings().all()
        else:
            payments = []
    return {"customer": {"name": customer["name"], "phone": phone, "email": customer.get("email")}, "orders": [dict(x) for x in orders], "measurements": [dict(x) for x in measurements], "payments": [dict(x) for x in payments]}
