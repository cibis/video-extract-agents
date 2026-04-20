from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from app.crew import run_crew
from app.db import update_job_status
import uuid

router = APIRouter()


class RunRequest(BaseModel):
    prompt: str
    video_url: str = ""
    video_urls: list[str] = []
    job_id: str = ""
    user_id: str = ""
    session_id: str = ""
    parent_job_id: str = ""
    # Optional extra asset URLs to pass when no session_id is available (e.g. direct API calls / tests)
    asset_urls: list[str] = []


class RunResponse(BaseModel):
    output_url: str
    job_id: str


@router.post("/run", response_model=RunResponse)
async def run(request: RunRequest, http_request: Request):
    job_id = request.job_id or str(uuid.uuid4())
    user_id = http_request.headers.get("X-User-Id", request.user_id)
    session_id = http_request.headers.get("X-Session-Id", request.session_id) or None
    parent_job_id = http_request.headers.get("X-Parent-Job-Id", request.parent_job_id) or None

    # Normalise video list: prefer video_urls, fall back to video_url
    video_urls = request.video_urls or ([request.video_url] if request.video_url else [])

    try:
        output_url = await run_crew(
            prompt=request.prompt,
            video_urls=video_urls,
            job_id=job_id,
            user_id=user_id,
            session_id=session_id,
            parent_job_id=parent_job_id,
            extra_asset_urls=request.asset_urls or [],
        )

        if request.job_id:
            await update_job_status(job_id, "completed", output_url=output_url)

        response = JSONResponse(
            content={"output_url": output_url, "job_id": job_id}
        )
        response.headers["x-job-id"] = job_id
        return response
    except Exception as exc:
        if request.job_id:
            await update_job_status(job_id, "failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
