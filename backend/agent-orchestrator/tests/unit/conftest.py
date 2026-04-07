import pytest
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("AZURE_SERVICE_BUS_CONNECTION_STRING", "Endpoint=sb://localhost;SharedAccessKeyName=test;SharedAccessKey=dGVzdA==;")
os.environ.setdefault("AZURE_STORAGE_CONNECTION_STRING", "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://localhost:10000/devstoreaccount1;")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
