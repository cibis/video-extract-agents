```
docker compose -f infrastructure/docker-compose/docker-compose.yml build angular-shell librechat agent-orchestrator mcp-server-analysis mcp-server-processing
```
or from scratch(SLOW)
```
docker compose -f infrastructure/docker-compose/docker-compose.yml build --no-cache angular-shell librechat agent-orchestrator mcp-server-analysis mcp-server-processing
```

restart
```
docker compose -f infrastructure/docker-compose/docker-compose.yml up -d --force-recreate angular-shell librechat agent-orchestrator mcp-server-analysis mcp-server-processing 
```

test containers re-build
```
scripts/run-e2e-local.sh --build
```

docker exec docker-compose-postgresql-1 psql -U postgres -d videoextract -c "
TRUNCATE TABLE outputs, job_steps, jobs, session_assets, sessions, video_keyframe_index, videos, assets RESTART IDENTITY CASCADE;
"

# Start stack (background)
cd infrastructure/docker-compose;docker compose up -d --build

# Stop stack
docker compose down

docker compose up --build