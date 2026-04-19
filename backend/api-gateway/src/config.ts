import { z } from 'zod';
import dotenv from 'dotenv';

dotenv.config();

const configSchema = z.object({
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  PORT: z.coerce.number().default(8000),
  DATABASE_URL: z.string().min(1),
  AZURE_STORAGE_CONNECTION_STRING: z.string().min(1),
  AZURE_STORAGE_CONTAINER_NAME: z.string().default('videos'),
  AZURE_SERVICE_BUS_CONNECTION_STRING: z.string().min(1),
  AZURE_ENTRA_TENANT_ID: z.string().default(''),
  AZURE_ENTRA_CLIENT_ID: z.string().default(''),
  AZURE_ENTRA_JWKS_URI: z.string().default(''),
  FRONT_DOOR_ENDPOINT: z.string().default(''),
  FRONT_DOOR_SECRET: z.string().default(''),
  AGENT_ORCHESTRATOR_URL: z.string().default('http://agent-orchestrator:8001'),
  AGENT_API_KEY: z.string().default(''),
  LOCAL_DEV_SKIP_AUTH: z.string().transform(v => v === 'true').default('false'),
  OUTPUT_URL_MODE: z.enum(['local', 'frontdoor']).default('frontdoor'),
  BLOB_PROXY_BASE_URL: z.string().default('http://localhost:8000'),
  APPLICATIONINSIGHTS_CONNECTION_STRING: z.string().default(''),
});

const parsed = configSchema.safeParse(process.env);

if (!parsed.success) {
  console.error('Invalid environment configuration:', parsed.error.format());
  process.exit(1);
}

export const config = parsed.data;
export type Config = typeof config;
