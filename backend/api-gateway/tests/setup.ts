// Provide required env vars before any module is loaded in tests
process.env.NODE_ENV = 'test';
process.env.DATABASE_URL = 'postgresql://test:test@localhost:5433/test';
process.env.AZURE_STORAGE_CONNECTION_STRING = 'DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://localhost:10000/devstoreaccount1;';
process.env.AZURE_SERVICE_BUS_CONNECTION_STRING = 'Endpoint=sb://localhost:5672;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=test;';
process.env.LOCAL_DEV_SKIP_AUTH = 'true';
process.env.OUTPUT_URL_MODE = 'local';
