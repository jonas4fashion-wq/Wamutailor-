# WamuTailor Fashion Management

Initial deployment foundation for the WamuTailor Fashion management application.

## Current scope

- FastAPI application
- Responsive black-and-gold dashboard
- `/health` service health endpoint
- `/api/status` application status endpoint
- Render Blueprint configuration

## Run locally

Python 3.11 or newer is recommended.

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Install and run:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 and http://127.0.0.1:8000/docs.

## Deploy on Render

1. Sign in to Render.
2. Create a new Blueprint and connect this GitHub repository.
3. Render reads `render.yaml` and creates the web service.
4. Confirm the service is healthy at `/health`.

## Production readiness

This repository is only the deployment foundation. Do not enter real customer data yet. Before business use, implement and test PostgreSQL persistence, staff authentication, role-based access, secure secret management, backups, audit logs, input validation and payment-record safeguards. Existing local JSON files are deliberately excluded from Git by `.gitignore`; do not commit customer records, measurements, credentials or payment data.
