import os
import secrets
import hashlib
import hmac
import base64
import json
import time
from pathlib import Path
from io import BytesIO
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import Column, Integer, MetaData, Table, Text, LargeBinary, create_engine, select, text

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
    "measurements": {"fields": ["customer_name", "customer_phone", "measured_by", "measurement_date", "neck", "shoulder", "chest", "waist", "hip", "sleeve", "shirt_length", "trouser_length", "unit"], "required": ["customer_name", "customer_phone"]},
    "orders": {"fields": ["customer_name", "customer_phone", "product", "quantity", "unit_price", "total", "deposit", "balance", "material_cost", "labor_cost", "other_cost", "production_cost", "profit", "delivery_date", "status", "notes"], "required": ["customer_name", "customer_phone", "product"]},
    "payments": {"fields": ["order_id", "customer_name", "amount", "method", "payment_date", "reference", "notes"], "required": ["order_id", "amount"]},
    "inventory": {"fields": ["item_name", "unit", "quantity", "unit_cost", "minimum_stock", "notes"], "required": ["item_name", "unit"]},
    "products": {"fields": ["name", "sku", "category", "description", "price", "currency", "stock_quantity", "active"], "required": ["name", "price"]},
    "staff": {"fields": ["name", "phone", "email", "role", "skills", "employment_type", "hire_date", "base_salary", "emergency_contact", "status", "notes"], "required": ["name", "role"]},
    "tasks": {"fields": ["task_name", "staff_name", "order_id", "customer_name", "start_date", "due_date", "priority", "status", "notes"], "required": ["task_name", "staff_name"]},
    "expenses": {"fields": ["category", "description", "amount", "expense_date", "payment_method", "reference", "notes"], "required": ["category", "description", "amount"]},
    "attendance": {"fields": ["staff_name", "attendance_date", "check_in", "check_out", "status", "notes"], "required": ["staff_name", "attendance_date", "status"]},
    "leave_requests": {"fields": ["staff_name", "leave_type", "start_date", "end_date", "days", "status", "reason", "decision_notes"], "required": ["staff_name", "leave_type", "start_date", "end_date"]},
    "payroll": {"fields": ["staff_name", "pay_period", "basic_salary", "allowances", "deductions", "net_pay", "payment_date", "payment_method", "reference", "status", "notes"], "required": ["staff_name", "pay_period", "basic_salary"]},
}
TABLE_OBJECTS = {}
for table_name, spec in TABLES.items():
    TABLE_OBJECTS[table_name] = Table(
        table_name, metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        *[Column(field, Text, nullable=True) for field in spec["fields"]],
        Column("created_at", Text, server_default=text("CURRENT_TIMESTAMP")),
        *([Column("portal_password_hash", Text, nullable=True)] if table_name == "customers" else []),
        *([Column("photo_data", LargeBinary, nullable=True), Column("photo_content_type", Text, nullable=True)] if table_name == "products" else []),
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
        if engine.dialect.name == "sqlite":
            measurement_cols = [row[1] for row in con.execute(text("PRAGMA table_info(measurements)"))]
        else:
            measurement_cols = [row[0] for row in con.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'measurements'"))]
        for measurement_col in ("measured_by", "measurement_date"):
            if measurement_col not in measurement_cols:
                con.execute(text(f"ALTER TABLE measurements ADD COLUMN {measurement_col} TEXT"))
        if engine.dialect.name == "sqlite":
            staff_cols = [row[1] for row in con.execute(text("PRAGMA table_info(staff)"))]
        else:
            staff_cols = [row[0] for row in con.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'staff'"))]
        for staff_col in ("email", "employment_type", "hire_date", "base_salary", "emergency_contact"):
            if staff_col not in staff_cols:
                con.execute(text(f"ALTER TABLE staff ADD COLUMN {staff_col} TEXT"))
        if engine.dialect.name == "sqlite":
            product_cols = [row[1] for row in con.execute(text("PRAGMA table_info(products)"))]
        else:
            product_cols = [row[0] for row in con.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'products'"))]
        if "photo_data" not in product_cols:
            con.execute(text("ALTER TABLE products ADD COLUMN photo_data BYTEA" if engine.dialect.name != "sqlite" else "ALTER TABLE products ADD COLUMN photo_data BLOB"))
        if "photo_content_type" not in product_cols:
            con.execute(text("ALTER TABLE products ADD COLUMN photo_content_type TEXT"))
        if "stock_quantity" not in product_cols:
            con.execute(text("ALTER TABLE products ADD COLUMN stock_quantity TEXT DEFAULT '0'"))
        if engine.dialect.name == "sqlite":
            order_cols = [row[1] for row in con.execute(text("PRAGMA table_info(orders)"))]
        else:
            order_cols = [row[0] for row in con.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'orders'"))]
        for cost_col in ("material_cost", "labor_cost", "other_cost", "production_cost", "profit"):
            if cost_col not in order_cols:
                con.execute(text(f"ALTER TABLE orders ADD COLUMN {cost_col} TEXT"))




def calculate_order_costs(data: dict[str, Any]) -> dict[str, str]:
    def amount(key: str) -> float:
        try:
            return max(0.0, float(str(data.get(key, "0") or "0").replace(",", "").strip()))
        except (TypeError, ValueError):
            return 0.0
    material, labor, other = amount("material_cost"), amount("labor_cost"), amount("other_cost")
    production = material + labor + other
    return {"material_cost": str(material), "labor_cost": str(labor), "other_cost": str(other), "production_cost": str(production), "profit": str(amount("total") - production)}


def sync_order_payment_totals(con, order_id: str) -> None:
    """Recalculate an order's paid and outstanding amounts from its payment ledger."""
    try:
        numeric_id = int(str(order_id).strip())
    except (TypeError, ValueError):
        raise HTTPException(422, "A valid order ID is required for a payment.")
    order_table = TABLE_OBJECTS["orders"]
    payment_table = TABLE_OBJECTS["payments"]
    order = con.execute(select(order_table).where(order_table.c.id == numeric_id)).mappings().first()
    if not order:
        raise HTTPException(404, "Order not found for this payment.")
    payments = con.execute(select(payment_table.c.amount).where(payment_table.c.order_id == str(numeric_id))).all()
    paid = 0.0
    for item in payments:
        try:
            amount = float(str(item.amount or 0).replace(",", "").strip())
        except (TypeError, ValueError):
            amount = 0.0
        if amount < 0:
            raise HTTPException(422, "Payment amounts cannot be negative.")
        paid += amount
    try:
        total = float(str(order.get("total") or 0).replace(",", "").strip())
    except (TypeError, ValueError):
        total = 0.0
    con.execute(order_table.update().where(order_table.c.id == numeric_id).values(
        deposit=str(paid), balance=str(max(0.0, total - paid))
    ))


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
def home_page():
    home_file = BASE_DIR / "static" / "home.html"
    if not home_file.exists():
        return JSONResponse(status_code=503, content={"detail": "WamuTailor home page is not installed."})
    return FileResponse(home_file)


@app.get("/admin", include_in_schema=False)
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
        query = select(*[column for column in table.c if column.name not in ("photo_data",)])
        rows = con.execute(query.order_by(table.c.id.desc()).limit(300)).mappings().all()
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
    if module == "orders":
        data.update(calculate_order_costs({**payload, **data}))
    if module == "payroll":
        def payroll_amount(key):
            try:
                value = float(str(payload.get(key, "0") or "0").replace(",", "").strip())
            except (TypeError, ValueError):
                raise HTTPException(422, f"Enter a valid {key.replace('_', ' ')} amount.")
            if value < 0:
                raise HTTPException(422, "Payroll amounts cannot be negative.")
            return value
        data["basic_salary"] = str(payroll_amount("basic_salary"))
        data["allowances"] = str(payroll_amount("allowances"))
        data["deductions"] = str(payroll_amount("deductions"))
        data["net_pay"] = str(max(0, payroll_amount("basic_salary") + payroll_amount("allowances") - payroll_amount("deductions")))
    if module == "payments":
        try:
            amount = float(str(data.get("amount", "0")).replace(",", "").strip())
        except (TypeError, ValueError):
            raise HTTPException(422, "Enter a valid payment amount.")
        if amount <= 0:
            raise HTTPException(422, "Payment amount must be greater than zero.")
        data["amount"] = str(amount)
    if not data:
        raise HTTPException(422, "No supported fields supplied")
    table = TABLE_OBJECTS[module]
    with engine.begin() as con:
        result = con.execute(table.insert().values(**data))
        record_id = result.inserted_primary_key[0]
        if module == "payments":
            sync_order_payment_totals(con, data["order_id"])
        row = con.execute(select(table).where(table.c.id == record_id)).mappings().one()
    return dict(row)


@app.put("/api/{module}/{record_id}")
def update_record(module: str, record_id: int, payload: dict[str, Any], _: str = Depends(require_admin)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    data = {field: str(payload[field]).strip() for field in TABLES[module]["fields"] if field in payload}
    if module == "payroll":
        with engine.connect() as con:
            existing_payroll = con.execute(select(TABLE_OBJECTS["payroll"]).where(TABLE_OBJECTS["payroll"].c.id == record_id)).mappings().first()
        if not existing_payroll:
            raise HTTPException(404, "Record not found")
        merged = {**dict(existing_payroll), **payload}
        def payroll_amount(key):
            try:
                value = float(str(merged.get(key, "0") or "0").replace(",", "").strip())
            except (TypeError, ValueError):
                raise HTTPException(422, f"Enter a valid {key.replace('_', ' ')} amount.")
            if value < 0:
                raise HTTPException(422, "Payroll amounts cannot be negative.")
            return value
        data["basic_salary"] = str(payroll_amount("basic_salary"))
        data["allowances"] = str(payroll_amount("allowances"))
        data["deductions"] = str(payroll_amount("deductions"))
        data["net_pay"] = str(max(0, payroll_amount("basic_salary") + payroll_amount("allowances") - payroll_amount("deductions")))
    if module == "orders":
        with engine.connect() as con:
            existing = con.execute(select(TABLE_OBJECTS["orders"]).where(TABLE_OBJECTS["orders"].c.id == record_id)).mappings().first()
        if not existing:
            raise HTTPException(404, "Record not found")
        data.update(calculate_order_costs({**dict(existing), **payload, **data}))
    if module == "payments" and "amount" in data:
        try:
            amount = float(str(data["amount"]).replace(",", "").strip())
        except (TypeError, ValueError):
            raise HTTPException(422, "Enter a valid payment amount.")
        if amount <= 0:
            raise HTTPException(422, "Payment amount must be greater than zero.")
        data["amount"] = str(amount)
    if not data:
        raise HTTPException(422, "No supported fields supplied")
    table = TABLE_OBJECTS[module]
    with engine.begin() as con:
        old_payment = None
        if module == "payments":
            old_payment = con.execute(select(table).where(table.c.id == record_id)).mappings().first()
            if not old_payment:
                raise HTTPException(404, "Record not found")
        result = con.execute(table.update().where(table.c.id == record_id).values(**data))
        if result.rowcount == 0:
            raise HTTPException(404, "Record not found")
        if module == "payments":
            sync_order_payment_totals(con, str(old_payment["order_id"]))
            new_order_id = str(data.get("order_id", old_payment["order_id"]))
            if new_order_id != str(old_payment["order_id"]):
                sync_order_payment_totals(con, new_order_id)
        row = con.execute(select(table).where(table.c.id == record_id)).mappings().one()
    return dict(row)


@app.delete("/api/{module}/{record_id}")
def delete_record(module: str, record_id: int, _: str = Depends(require_admin)):
    if module not in TABLES:
        raise HTTPException(404, "Unknown module")
    table = TABLE_OBJECTS[module]
    with engine.begin() as con:
        old_payment = None
        if module == "payments":
            old_payment = con.execute(select(table).where(table.c.id == record_id)).mappings().first()
            if not old_payment:
                raise HTTPException(404, "Record not found")
        result = con.execute(table.delete().where(table.c.id == record_id))
        if result.rowcount == 0:
            raise HTTPException(404, "Record not found")
        if old_payment:
            sync_order_payment_totals(con, str(old_payment["order_id"]))
    return {"deleted": True, "id": record_id}



@app.get("/api/admin/reports/production-costs")
def production_cost_report(_: str = Depends(require_admin)):
    table = TABLE_OBJECTS["orders"]
    with engine.connect() as con:
        rows = con.execute(select(table)).mappings().all()
    totals = {"orders": len(rows), "revenue": 0.0, "material_cost": 0.0, "labor_cost": 0.0, "other_cost": 0.0, "production_cost": 0.0, "profit": 0.0, "deposits": 0.0, "balance_due": 0.0}
    for row in rows:
        for key in ("total", "material_cost", "labor_cost", "other_cost", "production_cost", "profit", "deposit", "balance"):
            try:
                value = float(str(row.get(key) or 0).replace(",", ""))
            except (TypeError, ValueError):
                value = 0.0
            totals[{"total":"revenue", "deposit":"deposits", "balance":"balance_due"}.get(key, key)] += value
    expense_table = TABLE_OBJECTS["expenses"]
    with engine.connect() as con:
        expense_rows = con.execute(select(expense_table.c.amount)).all()
    operating_expenses = 0.0
    for item in expense_rows:
        try:
            operating_expenses += float(str(item.amount or 0).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
    totals["operating_expenses"] = operating_expenses
    totals["net_profit"] = totals["profit"] - operating_expenses
    return totals

@app.get("/api/admin/reports/monthly-analysis")
def monthly_analysis(_: str = Depends(require_admin)):
    orders_table = TABLE_OBJECTS["orders"]
    expenses_table = TABLE_OBJECTS["expenses"]
    payments_table = TABLE_OBJECTS["payments"]
    with engine.connect() as con:
        orders = con.execute(select(orders_table.c.total, orders_table.c.production_cost, orders_table.c.profit, orders_table.c.created_at)).all()
        expenses = con.execute(select(expenses_table.c.amount, expenses_table.c.expense_date, expenses_table.c.created_at)).all()
        payments = con.execute(select(payments_table.c.amount, payments_table.c.payment_date, payments_table.c.created_at)).all()
    months = {}
    def month_bucket(value):
        stamp = str(value or "")
        return stamp[:7] if len(stamp) >= 7 and stamp[4:5] == "-" else ""
    def amount(value):
        try:
            return float(str(value or 0).replace(",", "").strip())
        except (TypeError, ValueError):
            return 0.0
    def bucket(key):
        if key not in months:
            months[key] = {"month": key, "revenue": 0.0, "production_cost": 0.0, "gross_profit": 0.0, "operating_expenses": 0.0, "net_profit_estimate": 0.0, "payments_received": 0.0}
        return months[key]
    for row in orders:
        key = month_bucket(row.created_at)
        if key:
            item = bucket(key)
            item["revenue"] += amount(row.total)
            item["production_cost"] += amount(row.production_cost)
            item["gross_profit"] += amount(row.profit)
    for row in expenses:
        key = month_bucket(row.expense_date) or month_bucket(row.created_at)
        if key:
            bucket(key)["operating_expenses"] += amount(row.amount)
    for row in payments:
        key = month_bucket(row.payment_date) or month_bucket(row.created_at)
        if key:
            bucket(key)["payments_received"] += amount(row.amount)
    result = sorted(months.values(), key=lambda item: item["month"])[-12:]
    for item in result:
        item["net_profit_estimate"] = item["gross_profit"] - item["operating_expenses"]
    return {"months": result, "months_count": len(result), "note": "Order revenue and production costs are grouped by order creation month; expenses and payments use their recorded dates when available."}


@app.get("/api/admin/reports/low-stock")
def low_stock_report(_: str = Depends(require_admin)):
    table = TABLE_OBJECTS["inventory"]
    with engine.connect() as con:
        rows = con.execute(select(table).order_by(table.c.item_name.asc())).mappings().all()
    alerts = []
    for row in rows:
        try:
            quantity = float(str(row.get("quantity") or 0).replace(",", "").strip())
            minimum = float(str(row.get("minimum_stock") or 0).replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        if quantity <= minimum:
            item = dict(row)
            item["quantity_numeric"] = quantity
            item["minimum_stock_numeric"] = minimum
            item["alert"] = "OUT OF STOCK" if quantity <= 0 else "LOW STOCK"
            alerts.append(item)
    return {"alert_count": len(alerts), "out_of_stock": sum(1 for x in alerts if x["quantity_numeric"] <= 0), "low_stock": sum(1 for x in alerts if x["quantity_numeric"] > 0), "items": alerts}


@app.get("/api/admin/reports/staff-performance")
def staff_performance_report(_: str = Depends(require_admin)):
    staff_table = TABLE_OBJECTS["staff"]
    task_table = TABLE_OBJECTS["tasks"]
    attendance_table = TABLE_OBJECTS["attendance"]
    payroll_table = TABLE_OBJECTS["payroll"]
    with engine.connect() as con:
        staff_rows = con.execute(select(staff_table)).mappings().all()
        task_rows = con.execute(select(task_table)).mappings().all()
        attendance_rows = con.execute(select(attendance_table)).mappings().all()
        payroll_rows = con.execute(select(payroll_table)).mappings().all()
    def norm(value):
        return str(value or "").strip().casefold()
    names = {norm(row.get("name")): str(row.get("name") or "") for row in staff_rows if row.get("name")}
    for row in task_rows + attendance_rows + payroll_rows:
        name = str(row.get("staff_name") or "").strip()
        if name and norm(name) not in names:
            names[norm(name)] = name
    report = []
    for key, name in names.items():
        assigned = [row for row in task_rows if norm(row.get("staff_name")) == key]
        completed = [row for row in assigned if norm(row.get("status")) in ("done", "completed", "finished")]
        attendance = [row for row in attendance_rows if norm(row.get("staff_name")) == key]
        present = sum(1 for row in attendance if norm(row.get("status")) in ("present", "late"))
        late = sum(1 for row in attendance if norm(row.get("status")) == "late")
        paid = 0.0
        for row in payroll_rows:
            if norm(row.get("staff_name")) == key and norm(row.get("status")) == "paid":
                try:
                    paid += float(str(row.get("net_pay") or 0).replace(",", "").strip())
                except (TypeError, ValueError):
                    pass
        report.append({
            "staff_name": name,
            "tasks_assigned": len(assigned),
            "tasks_completed": len(completed),
            "tasks_open": len(assigned) - len(completed),
            "attendance_days_present": present,
            "late_days": late,
            "payroll_paid_total": paid,
        })
    report.sort(key=lambda item: item["staff_name"].casefold())
    return {"employees": len(report), "staff": report}


@app.get("/api/admin/reports/expenses")
def expense_report(_: str = Depends(require_admin)):
    table = TABLE_OBJECTS["expenses"]
    with engine.connect() as con:
        rows = con.execute(select(table)).mappings().all()
    total = 0.0
    categories = {}
    for row in rows:
        try:
            amount = float(str(row.get("amount") or 0).replace(",", "").strip())
        except (TypeError, ValueError):
            amount = 0.0
        total += amount
        category = str(row.get("category") or "Uncategorized")
        categories[category] = categories.get(category, 0.0) + amount
    return {"count": len(rows), "total": total, "by_category": categories}



@app.get("/api/admin/reports/overview")
def business_overview(_: str = Depends(require_admin)):
    """Compact operational snapshot for the admin dashboard."""
    def number(value):
        try:
            return float(str(value or 0).replace(",", "").strip())
        except (TypeError, ValueError):
            return 0.0

    with engine.connect() as con:
        customers = con.execute(select(TABLE_OBJECTS["customers"].c.id)).all()
        orders = con.execute(select(TABLE_OBJECTS["orders"])).mappings().all()
        payments = con.execute(select(TABLE_OBJECTS["payments"].c.order_id, TABLE_OBJECTS["payments"].c.amount)).all()
        inventory = con.execute(select(TABLE_OBJECTS["inventory"])).mappings().all()
        tasks = con.execute(select(TABLE_OBJECTS["tasks"])).mappings().all()
        staff = con.execute(select(TABLE_OBJECTS["staff"].c.id)).all()

    paid_by_order = {}
    for payment in payments:
        key = str(payment.order_id or "")
        paid_by_order[key] = paid_by_order.get(key, 0.0) + max(0.0, number(payment.amount))

    open_orders = [o for o in orders if str(o.get("status") or "").strip().lower() not in ("delivered", "cancelled", "complete", "completed")]
    balance_due = 0.0
    for order in orders:
        paid = paid_by_order.get(str(order["id"]), number(order.get("deposit")))
        balance_due += max(0.0, number(order.get("total")) - paid)

    low_stock = []
    for item in inventory:
        qty = number(item.get("quantity"))
        minimum = number(item.get("minimum_stock"))
        if qty <= minimum:
            low_stock.append({
                "item_name": item.get("item_name") or "Unnamed item",
                "quantity": qty,
                "minimum_stock": minimum,
                "unit": item.get("unit") or "",
            })

    today = time.strftime("%Y-%m-%d")
    from datetime import date, timedelta
    today_date = date.fromisoformat(today)
    due_soon_date = (today_date + timedelta(days=7)).isoformat()
    overdue_orders = [
        {"id": o["id"], "customer_name": o.get("customer_name") or "", "product": o.get("product") or "", "delivery_date": o.get("delivery_date") or "", "status": o.get("status") or ""}
        for o in open_orders
        if o.get("delivery_date") and str(o.get("delivery_date"))[:10] < today
    ]
    due_soon_orders = [
        {"id": o["id"], "customer_name": o.get("customer_name") or "", "product": o.get("product") or "", "delivery_date": o.get("delivery_date") or "", "status": o.get("status") or ""}
        for o in open_orders
        if o.get("delivery_date") and today <= str(o.get("delivery_date"))[:10] <= due_soon_date
    ]
    overdue_tasks = [
        {"task_name": t.get("task_name") or "Untitled task", "staff_name": t.get("staff_name") or "", "due_date": t.get("due_date") or ""}
        for t in tasks
        if t.get("due_date") and str(t.get("due_date")) < today
        and str(t.get("status") or "").strip().lower() not in ("done", "completed", "finished")
    ]

    return {
        "customers": len(customers),
        "orders_total": len(orders),
        "orders_open": len(open_orders),
        "orders_delivered": len(orders) - len(open_orders),
        "payments_received": round(sum(paid_by_order.values()), 2),
        "balance_due": round(balance_due, 2),
        "low_stock_count": len(low_stock),
        "low_stock_items": low_stock[:8],
        "staff_count": len(staff),
        "tasks_active": sum(1 for t in tasks if str(t.get("status") or "").strip().lower() not in ("done", "completed", "finished")),
        "tasks_overdue": len(overdue_tasks),
        "overdue_tasks": len(overdue_tasks),
        "overdue_tasks_list": overdue_tasks[:8],
        "overdue_orders_count": len(overdue_orders),
        "overdue_orders": overdue_orders[:8],
        "due_soon_orders_count": len(due_soon_orders),
        "due_soon_orders": due_soon_orders[:8],
        "as_of": today,
    }

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


@app.get("/api/admin/customers/{customer_id}/profile")
def customer_profile(customer_id: int, _: str = Depends(require_admin)):
    customers = TABLE_OBJECTS["customers"]
    with engine.connect() as con:
        customer = con.execute(select(customers).where(customers.c.id == customer_id)).mappings().first()
        if not customer:
            raise HTTPException(404, "Customer not found")
        phone = customer["phone"]
        orders = con.execute(select(TABLE_OBJECTS["orders"]).where(TABLE_OBJECTS["orders"].c.customer_phone == phone).order_by(TABLE_OBJECTS["orders"].c.id.desc())).mappings().all()
        measurements = con.execute(select(TABLE_OBJECTS["measurements"]).where(TABLE_OBJECTS["measurements"].c.customer_phone == phone).order_by(TABLE_OBJECTS["measurements"].c.id.desc())).mappings().all()
        order_ids = [str(order["id"]) for order in orders]
        payments = con.execute(select(TABLE_OBJECTS["payments"]).where(TABLE_OBJECTS["payments"].c.order_id.in_(order_ids)).order_by(TABLE_OBJECTS["payments"].c.id.desc())).mappings().all() if order_ids else []
    return {
        "customer": {k: customer.get(k) for k in ["id", "name", "phone", "email", "address", "notes"]},
        "orders": [dict(x) for x in orders],
        "measurements": [dict(x) for x in measurements],
        "payments": [dict(x) for x in payments],
    }


@app.post("/api/admin/products/{product_id}/photo")
async def upload_product_photo(product_id: int, photo: UploadFile = File(...), _: str = Depends(require_admin)):
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if photo.content_type not in allowed:
        raise HTTPException(415, "Only JPG, PNG or WEBP images are allowed.")
    data = await photo.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise HTTPException(413, "Photo must be 4 MB or smaller.")
    if not data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF")):
        raise HTTPException(415, "The uploaded file is not a valid supported image.")
    table = TABLE_OBJECTS["products"]
    with engine.begin() as con:
        result = con.execute(table.update().where(table.c.id == product_id).values(photo_data=data, photo_content_type=photo.content_type))
        if result.rowcount == 0:
            raise HTTPException(404, "Product not found")
    return {"uploaded": True, "product_id": product_id, "photo_url": f"/api/products/{product_id}/photo"}


@app.get("/api/client/catalog")
def client_catalog():
    table = TABLE_OBJECTS["products"]
    with engine.connect() as con:
        rows = con.execute(
            select(*[column for column in table.c if column.name not in ("photo_data", "photo_content_type", "created_at")])
        ).mappings().all()
    catalog = []
    for row in rows:
        item = dict(row)
        active = str(item.get("active") or "yes").strip().lower()
        try:
            stock = float(item.get("stock_quantity") or 0)
        except (TypeError, ValueError):
            stock = 0
        if active == "no" or stock <= 0:
            continue
        item["photo_url"] = f"/api/products/{row['id']}/photo"
        item["in_stock"] = True
        catalog.append(item)
    return catalog


@app.get("/api/products/{product_id}/photo")
def get_product_photo(product_id: int):
    from fastapi.responses import Response
    table = TABLE_OBJECTS["products"]
    with engine.connect() as con:
        row = con.execute(select(table.c.photo_data, table.c.photo_content_type).where(table.c.id == product_id)).first()
    if not row or not row.photo_data:
        raise HTTPException(404, "Product photo not found")
    return Response(content=row.photo_data, media_type=row.photo_content_type, headers={"Cache-Control": "public, max-age=3600"})


@app.post("/api/client/change-pin")
def change_client_pin(payload: dict[str, Any], customer: dict = Depends(require_client)):
    current_pin = str(payload.get("current_pin", ""))
    new_pin = str(payload.get("new_pin", ""))
    if not verify_password(current_pin, customer.get("portal_password_hash", "")):
        raise HTTPException(401, "Current PIN is incorrect")
    if len(new_pin) < 10:
        raise HTTPException(422, "New password/PIN must be at least 10 characters for account safety.")
    table = TABLE_OBJECTS["customers"]
    with engine.begin() as con:
        con.execute(table.update().where(table.c.id == customer["id"]).values(portal_password_hash=hash_password(new_pin)))
    return {"changed": True, "message": "Your sign-in password has been changed. Sign in again."}


@app.get("/policies", include_in_schema=False)
def policies_page():
    file = BASE_DIR / "static" / "policies.html"
    if not file.exists():
        return JSONResponse(status_code=503, content={"detail": "Policies page is not installed."})
    return FileResponse(file)
