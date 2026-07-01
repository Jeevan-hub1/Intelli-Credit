"""Package setup for Intelli-Credit (CreditDNA)."""

from setuptools import find_packages, setup

setup(
    name="intelli-credit",
    version="1.0.0",
    description="AI-powered corporate credit decisioning engine for the Indian lending ecosystem",
    author="Intelli-Credit Team",
    license="Proprietary",
    python_requires=">=3.10",
    packages=find_packages(exclude=("tests", "tests.*")),
    install_requires=[
        "fastapi>=0.110.0",
        "uvicorn>=0.27.0",
        "pydantic>=2.6.0",
        "pydantic-settings>=2.2.0",
        "python-multipart>=0.0.9",
        "SQLAlchemy>=2.0.0",
        "PyJWT>=2.8.0",
        "cryptography>=42.0.0",
    ],
    extras_require={
        "pdf": ["reportlab>=4.0.0"],
        "graph": ["networkx>=3.2"],
        "research": ["httpx>=0.27.0"],
        "databricks": ["databricks-sql-connector>=3.0"],
        "dev": ["pytest>=8.0.0", "pytest-cov>=4.1.0", "black", "ruff", "mypy"],
    },
    entry_points={
        "console_scripts": [
            "intelli-credit=main:run",
        ],
    },
)
