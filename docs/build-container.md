cd c:/PROJECTS/video-extract-agents
docker compose -f infrastructure/docker-compose/docker-compose.yml build --no-cache angular-shell librechat
docker compose -f infrastructure/docker-compose/docker-compose.yml up -d angular-shell librechat

docker compose -f infrastructure/docker-compose/docker-compose.yml build angular-shell 2>&1 | tail -5 && docker compose -f infrastructure/docker-compose/docker-compose.yml up -d angular-shell 2>&1 | tail -10

docker compose -f infrastructure/docker-compose/docker-compose.yml build --no-cache agent-orchestrator mcp-server-analysis 
docker compose -f infrastructure/docker-compose/docker-compose.yml up -d --force-recreate agent-orchestrator mcp-server-analysis 

docker compose -f infrastructure/docker-compose/docker-compose.yml up -d --force-recreate mcp-server-analysis 

docker exec docker-compose-postgresql-1 psql -U postgres -d videoextract -c "
TRUNCATE TABLE outputs, job_steps, jobs, session_assets, sessions, video_keyframe_index, videos, assets RESTART IDENTITY CASCADE;
"

# Start stack (background)
cd infrastructure/docker-compose;docker compose up -d --build

# Stop stack
docker compose down

docker compose up --build