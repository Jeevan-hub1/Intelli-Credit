# Intelli-Credit System (CreditDNA)

AI-powered corporate credit decisioning engine designed for the Indian lending ecosystem.

> **Implementation status:** This repository now contains a complete, runnable
> reference implementation of all 30 requirements in `requirements.md`
> (FastAPI service + domain engine + tests). See **Quick Start** and
> **Implementation Notes** below.

## Quick Start

```bash
# From the repository root (absolute imports require running from here)
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run the API (creates a local SQLite DB and seeds a default admin on startup)
uvicorn main:app --reload
# Interactive API docs:  http://localhost:8000/docs
# Default dev credentials: admin / admin123  (override via ADMIN_USERNAME / ADMIN_PASSWORD)

# Run the test suite (31 tests covering scoring, fraud, parsing, CAM, EWS, API)
pytest tests/ -q
```

No external infrastructure is required to run locally: PostgreSQL falls back to
SQLite, MinIO/S3 falls back to encrypted local storage, and the Databricks Delta
feature store falls back to a local versioned (time-travel) store. Configure the
real services via environment variables in `.env` (see `.env.example`).


## Overview

Intelli-Credit provides end-to-end credit analysis automation while maintaining human oversight through a Credit Officer review workflow. The system ingests multiple document types, performs fraud detection, calculates creditworthiness using the Five Cs framework with temporal decay, generates explainable Credit Appraisal Memos (CAMs), and provides ongoing portfolio monitoring.

## Key Features

- **Multi-format Document Ingestion**: PDF, Excel, CSV, images
- **Financial Statement Parsing**: Indian GAAP and Ind AS support
- **GST Return Analysis**: GSTR-2A and GSTR-3B reconciliation
- **Bank Statement Analysis**: Transaction categorization and conduct metrics
- **Fraud Detection**: Circular trading and fake ITC detection
- **Five Cs Scoring**: Character, Capacity, Capital, Collateral, Conditions
- **Temporal Decay**: Recent data weighted higher than historical data
- **CAM Generation**: Comprehensive Credit Appraisal Memos with explainability
- **Early Warning System**: Portfolio monitoring with alerts
- **Databricks Integration**: Delta Lake feature store and unified analytics

## Architecture

The system follows a microservices architecture with:
- **FastAPI**: REST API framework
- **PostgreSQL**: Metadata and audit logs
- **MinIO/S3**: Document object storage
- **Databricks Delta Lake**: Feature store and data warehouse
- **Apache Spark**: Distributed data processing
- **Celery + Redis**: Asynchronous task queue

## Project Structure

```
intelli-credit/
├── config/              # Configuration and settings
│   ├── settings.py      # Application settings
│   ├── database.py      # PostgreSQL configuration
│   ├── storage.py       # MinIO/S3 configuration
│   └── databricks.py    # Databricks and Delta Lake configuration
├── models/              # Data models
│   ├── base.py          # Base models and enums
│   ├── application.py   # Application and document models
│   ├── financial.py     # Financial statement models
│   ├── gst.py           # GST data models
│   ├── bank.py          # Bank statement models
│   ├── scoring.py       # Credit scoring models
│   ├── cam.py           # CAM models
│   └── db_models.py     # SQLAlchemy database models
├── services/            # Business logic services
├── utils/               # Utility functions
│   └── logging.py       # Structured logging
├── tests/               # Test suite
├── requirements.txt     # Python dependencies
├── setup.py             # Package setup
└── README.md            # This file
```

## Setup Instructions

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- MinIO or S3-compatible storage
- Redis 6+
- Databricks workspace (optional for full functionality)

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd intelli-credit
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Initialize database**
   ```bash
   python -c "from config.database import init_db; init_db()"
   ```

6. **Set up MinIO bucket**
   - Start MinIO server
   - The application will automatically create the bucket on first run

7. **Configure Databricks** (optional)
   - Set up Databricks workspace
   - Create cluster
   - Update .env with Databricks credentials

### Running the Application

```bash
# Start the API server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Start Celery worker (in separate terminal)
celery -A services.celery_app worker --loglevel=info
```

## Configuration

All configuration is managed through environment variables. See `.env.example` for available options.

Key configurations:
- **Database**: PostgreSQL connection settings
- **Storage**: MinIO/S3 credentials and bucket name
- **Databricks**: Workspace URL, token, and cluster ID
- **Security**: Secret keys and encryption settings
- **External APIs**: MCA21 and eCourts API credentials

## Data Models

### Core Models

- **Application**: Credit application with borrower details
- **Document**: Uploaded documents with classification
- **FinancialStatement**: Parsed financial statements
- **GSTData**: GST return data and reconciliation
- **BankStatement**: Bank transactions and conduct metrics
- **CreditScore**: Five Cs scores with explainability
- **CAM**: Credit Appraisal Memo with all sections

### Database Models

- **DBApplication**: Application metadata
- **DBDocument**: Document metadata
- **DBUser**: User accounts and roles
- **DBAuditLog**: Audit trail (7-year retention)
- **DBModelVersion**: Model version tracking
- **DBQualitativeNote**: Credit officer observations

## Security

- **Encryption**: AES-256 for stored documents and data
- **RBAC**: Role-based access control (Analyst, Credit_Officer, Administrator)
- **Audit Logging**: All operations logged with 7-year retention
- **OAuth 2.0**: Token-based API authentication

## Compliance

- **RBI Guidelines**: 7-year audit log retention
- **Data Privacy**: Encryption at rest and in transit
- **Regulatory Reporting**: NPA classification and sector exposure reports

## Development

### Running Tests

```bash
pytest tests/ -v --cov=.
```

### Code Quality

```bash
# Format code
black .

# Lint code
flake8 .

# Type checking
mypy .
```

## License

Proprietary - All rights reserved

## Support

For support and questions, contact the development team.


## Implementation Notes

### Module map

| Layer | Modules | Key requirements |
|-------|---------|------------------|
| Config | `config/settings.py`, `database.py`, `storage.py`, `databricks.py` | 19, 27 |
| Utilities | `utils/temporal.py` (decay), `numeric.py`, `logging.py` | 14.4, 10.4, 28.3 |
| Parsing | `services/document_parser.py`, `financial_parser.py`, `gst_parser.py`, `bank_parser.py` | 1-4, 24 |
| Fraud / external | `services/fraud_detector.py`, `external_apis.py` | 5-8 |
| Scoring | `services/ratios.py`, `scoring.py` | 9-14, 26 |
| CAM / viz | `services/cam_generator.py`, `graph_viz.py` | 15, 16, 29, 30 |
| Monitoring | `services/ews_monitor.py` | 17, 18 |
| Security | `services/security.py`, `audit.py` | 19, 23, 25 |
| Platform | `services/feature_store.py`, `research_agent.py`, `reports.py` | 22, 27, 28 |
| Orchestration | `services/credit_engine.py` | 14, 20 |
| API | `main.py`, `api/routes.py`, `deps.py`, `schemas.py`, `store.py` | 21-23 |

### Document parsing architecture

Document ingestion classifies and validates uploads, while the financial/GST/bank
parsers operate on **structured records** (dicts) produced by an upstream OCR/LLM
extraction layer. This keeps the deterministic, testable business logic (accounting-
identity validation, ITC reconciliation, transaction categorization, conduct metrics)
decoupled from the pluggable raw-text extraction step. The structured contract for
each parser is documented in its module docstring.

### Optional integrations (graceful fallbacks)

- **PDF / graph image** — `reportlab` + `matplotlib` produce the CAM PDF and the
  300 DPI circular-trading graph (Req 15.1, 29.5); without them the CAM is stored
  as text and the graph as a structured spec.
- **Object storage** — set `STORAGE_BACKEND=minio`; otherwise encrypted files are
  written to `./_storage`.
- **Databricks Delta Lake** — set `DATABRICKS_ENABLED=true` with host/token/path;
  otherwise a local partitioned, versioned JSON store provides the same API
  (lineage + time-travel) under `./_delta_feature_store`.
- **Research agent** — uses `httpx` for live crawling when enabled, or ranks and
  classifies findings supplied by an upstream search connector.

### Security & compliance

- Documents and sensitive fields are encrypted with AES-256 (Fernet).
- Passwords are hashed with PBKDF2-HMAC-SHA256; API auth uses OAuth2 JWT bearer
  tokens with three RBAC roles (Analyst, Credit_Officer, Administrator).
- Every state-changing action is written to a 7-year-retention audit log.
- API requests are rate-limited (default 100/min/client) and malformed requests
  return HTTP 400 with structured error detail.

### Requirements traceability

All 30 requirements in `requirements.md` are implemented; thresholds called out in
the acceptance criteria (e.g. ITC mismatch > 5%, DSCR < 1.25, D/E > 3.0, LTV > 75%
after type-specific haircuts, circular-trading ratio > 15%, classification
confidence < 85%) are encoded as named constants in the relevant service modules
and covered by the test suite in `tests/`.
