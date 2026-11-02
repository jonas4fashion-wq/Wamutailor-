# WamuTailor Fashion Management

Deployment foundation for the WamuTailor Fashion management system.

## Modules
- Customers
- Measurements
- Orders
- Payments
- Inventory

## Stack
- FastAPI
- SQLAlchemy
- Render Postgres (Blueprint managed)
- HTTP Basic admin authentication (temporary initial access layer)

## Local run
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Render deployment
The Blueprint provisions a web service and a Free Postgres database, wires DATABASE_URL, sets ADMIN_USERNAME=admin, and generates ADMIN_PASSWORD. After Blueprint sync, open the service's Environment page and reveal/copy the generated ADMIN_PASSWORD for your own secure records. Never share it in chat.

## IMPORTANT: Free database expiry
Render Free Postgres databases expire after 30 days. This setup is for testing only. Upgrade to a paid database before relying on it for business records and set up verified backups. Free web services can spin down when idle. See Render's current documentation: https://render.com/docs/free

## Security
This is an early testing release, not yet approved for production customer information. Before use with real client measurements, contacts, orders or payment records, complete security review, HTTPS verification, staff roles, audit logs, validation, backup/restore testing and data retention policy. Do not commit secrets or customer records.
