# Intelli-Credit System (CreditDNA)

AI-powered corporate credit decisioning engine designed for the Indian lending ecosystem.

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
