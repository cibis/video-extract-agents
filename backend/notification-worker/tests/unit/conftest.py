import os
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("AZURE_SERVICE_BUS_CONNECTION_STRING", "Endpoint=sb://localhost;SharedAccessKeyName=test;SharedAccessKey=dGVzdA==;")
os.environ.setdefault("NOTIFICATION_MODE", "stdout")
