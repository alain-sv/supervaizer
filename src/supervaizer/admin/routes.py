# Copyright (c) 2024-2025 Alain Prasquier - Supervaize.com. All rights reserved.
#
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at
# https://mozilla.org/MPL/2.0/.

import os
import asyncio
import json
import time
import psutil
from pathlib import Path
from typing import Dict, Optional, AsyncGenerator, List
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, Depends, Query, Security
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import APIKeyHeader
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

from fastapi.responses import Response
from supervaizer.__version__ import API_VERSION
from supervaizer.storage import (
    StorageManager,
    create_job_repository,
    create_case_repository,
)
from supervaizer.common import log
from supervaizer.lifecycle import EntityStatus

# Global log queue for streaming
log_queue: asyncio.Queue[Dict[str, str]] = asyncio.Queue()

# Server start time for uptime calculation
# This will be set when the server actually starts
SERVER_START_TIME = time.time()


def set_server_start_time(start_time: float) -> None:
    """Set the server start time for uptime calculation."""
    global SERVER_START_TIME
    SERVER_START_TIME = start_time


def add_log_to_queue(timestamp: str, level: str, message: str) -> None:
    """Add a log message to the streaming queue."""
    try:
        log_data = {"timestamp": timestamp, "level": level, "message": message}
        # Non-blocking put - if queue is full, skip the message
        try:
            log_queue.put_nowait(log_data)
        except asyncio.QueueFull:
            pass  # Skip if queue is full
    except Exception:
        pass  # Silently ignore errors to avoid breaking logging


# Initialize templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# API key authentication
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class AdminStats(BaseModel):
    """Statistics for admin dashboard."""

    jobs: Dict[str, int]
    cases: Dict[str, int]
    collections: int


class ServerStatus(BaseModel):
    """Server status and metrics."""

    status: str
    uptime: str
    uptime_seconds: int
    memory_usage: str
    memory_usage_mb: float
    memory_percent: float
    cpu_percent: float
    active_connections: int
    agents_count: int
    host: str
    port: int
    environment: str
    database_type: str
    storage_path: str


class ServerConfiguration(BaseModel):
    """Server configuration details."""

    host: str
    port: int
    api_version: str
    environment: str
    database_type: str
    storage_path: str
    agents: List[Dict[str, str]]


class EntityFilter(BaseModel):
    """Filter parameters for entity queries."""

    status: Optional[str] = None
    agent_name: Optional[str] = None
    search: Optional[str] = None
    sort: str = "-created_at"
    limit: int = 50
    skip: int = 0


async def verify_admin_access(
    api_key: Optional[str] = Security(api_key_header),
) -> bool:
    """Verify admin access via API key."""
    # Direct environment check
    expected_key = os.getenv("SUPERVAIZER_API_KEY")

    if expected_key is None:
        # API key authentication is disabled
        return True

    if api_key != expected_key:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "APIKey"},
        )

    return True


def format_uptime(seconds: int) -> str:
    """Format uptime seconds into human readable string."""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


def get_server_status() -> ServerStatus:
    """Get current server status and metrics."""
    # Get server info from storage - required, no fallback
    from supervaizer.server import get_server_info_from_storage

    server_info = get_server_info_from_storage()
    if not server_info:
        raise HTTPException(
            status_code=503,
            detail="Server information not available in storage. Server may not be properly initialized.",
        )

    # Calculate uptime from stored start time
    uptime_seconds = int(time.time() - server_info.start_time)
    uptime_str = format_uptime(uptime_seconds)

    # Get memory usage
    memory = psutil.virtual_memory()
    process = psutil.Process()
    process_memory = process.memory_info().rss / 1024 / 1024  # MB

    # Get CPU usage
    cpu_percent = psutil.cpu_percent(interval=0.1)

    # Get network connections (approximate active connections)
    try:
        connections = len(psutil.net_connections(kind="inet"))
    except (psutil.AccessDenied, OSError):
        # This is a system limitation, not a missing data issue
        connections = 0

    return ServerStatus(
        status="online",
        uptime=uptime_str,
        uptime_seconds=uptime_seconds,
        memory_usage=f"{process_memory:.1f} MB",
        memory_usage_mb=process_memory,
        memory_percent=memory.percent,
        cpu_percent=cpu_percent,
        active_connections=connections,
        agents_count=len(server_info.agents),
        host=server_info.host,
        port=server_info.port,
        environment=server_info.environment,
        database_type="TinyDB",
        storage_path=os.getenv("DATA_STORAGE_PATH", "./data"),
    )


def get_server_configuration(storage: StorageManager) -> ServerConfiguration:
    """Get server configuration details."""
    # Get server info from storage - required, no fallback
    from supervaizer.server import get_server_info_from_storage

    server_info = get_server_info_from_storage()
    if not server_info:
        raise HTTPException(
            status_code=503,
            detail="Server configuration not available in storage. Server may not be properly initialized.",
        )

    return ServerConfiguration(
        host=server_info.host,
        port=server_info.port,
        api_version=server_info.api_version,
        environment=server_info.environment,
        database_type="TinyDB",
        storage_path=storage.db_path,
        agents=server_info.agents,
    )


def create_admin_routes() -> APIRouter:
    """Create and configure admin routes."""
    router = APIRouter(tags=["admin"])

    # Initialize storage manager
    storage = StorageManager()
    job_repo = create_job_repository()
    case_repo = create_case_repository()

    @router.get("/", response_class=HTMLResponse)
    async def admin_dashboard(
        request: Request, authorized: bool = Depends(verify_admin_access)
    ) -> Response:
        """Admin dashboard page."""
        try:
            # Get stats
            stats = get_dashboard_stats(storage)

            return templates.TemplateResponse(
                "dashboard.html",
                {
                    "request": request,
                    "api_version": API_VERSION,
                    "stats": stats,
                    "system_status": "Online",
                    "db_name": "TinyDB",
                    "data_storage_path": storage.db_path,
                },
            )
        except Exception as e:
            log.error(f"Admin dashboard error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/jobs", response_class=HTMLResponse)
    async def admin_jobs_page(
        request: Request, authorized: bool = Depends(verify_admin_access)
    ) -> Response:
        """Jobs management page."""
        return templates.TemplateResponse(
            "jobs_list.html",
            {
                "request": request,
                "api_version": API_VERSION,
            },
        )

    @router.get("/cases", response_class=HTMLResponse)
    async def admin_cases_page(
        request: Request, authorized: bool = Depends(verify_admin_access)
    ) -> Response:
        """Cases management page."""
        return templates.TemplateResponse(
            "cases_list.html",
            {
                "request": request,
                "api_version": API_VERSION,
            },
        )

    @router.get("/server", response_class=HTMLResponse)
    async def admin_server_page(
        request: Request, authorized: bool = Depends(verify_admin_access)
    ) -> Response:
        """Server status and configuration page."""
        try:
            # Get initial server data
            server_status = get_server_status()
            server_config = get_server_configuration(storage)

            return templates.TemplateResponse(
                "server.html",
                {
                    "request": request,
                    "api_version": API_VERSION,
                    "server_status": server_status,
                    "server_config": server_config,
                },
            )
        except Exception as e:
            log.error(f"Admin server page error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/agents", response_class=HTMLResponse)
    async def admin_agents_page(
        request: Request, authorized: bool = Depends(verify_admin_access)
    ) -> Response:
        """Agents management page."""
        try:
            from supervaizer.server import get_server_info_from_storage

            server_info = get_server_info_from_storage()
            if not server_info:
                raise HTTPException(
                    status_code=503, detail="Server information not available"
                )

            return templates.TemplateResponse(
                "agents.html",
                {
                    "request": request,
                    "api_version": API_VERSION,
                    "agents": server_info.agents,
                },
            )
        except Exception as e:
            log.error(f"Admin agents page error: {e}")
            raise HTTPException(
                status_code=503, detail="Server information unavailable"
            )

    @router.get("/console", response_class=HTMLResponse)
    async def admin_console_page(
        request: Request, authorized: bool = Depends(verify_admin_access)
    ) -> Response:
        """Interactive console page."""
        return templates.TemplateResponse(
            "console.html",
            {
                "request": request,
                "api_version": API_VERSION,
            },
        )

    # API Routes
    @router.get("/api/stats")
    async def get_stats(authorized: bool = Depends(verify_admin_access)) -> AdminStats:
        """Get system statistics."""
        return get_dashboard_stats(storage)

    @router.get("/api/server/status")
    async def get_server_status_api(
        request: Request, authorized: bool = Depends(verify_admin_access)
    ) -> Response:
        """Get current server status for HTMX refresh."""
        try:
            server_status = get_server_status()

            return templates.TemplateResponse(
                "server_status_cards.html",
                {
                    "request": request,
                    "server_status": server_status,
                },
            )
        except Exception as e:
            log.error(f"Get server status API error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/agents")
    async def get_agents_api(
        request: Request,
        status: Optional[str] = Query(None),
        agent_type: Optional[str] = Query(None),
        search: Optional[str] = Query(None),
        sort: str = Query("-created_at"),
        authorized: bool = Depends(verify_admin_access),
    ) -> Response:
        """Get agents with filtering for HTMX refresh."""
        try:
            from supervaizer.server import get_server_info_from_storage

            server_info = get_server_info_from_storage()
            if not server_info:
                raise HTTPException(
                    status_code=503, detail="Server information not available"
                )

            agents = server_info.agents

            # Apply filters
            filtered_agents = []
            for agent in agents:
                # Status filter (we'll add this to agent data later)
                if status and status != "all":
                    # For now, assume all agents are active since we don't have status
                    if status != "active":
                        continue

                # Agent type filter
                if agent_type and agent_type != "":
                    # Default to "conversational" if no type specified
                    agent_agent_type = agent.get("type", "conversational")
                    if agent_type.lower() != agent_agent_type.lower():
                        continue

                # Search filter
                if search:
                    search_lower = search.lower()
                    if not (
                        search_lower in agent.get("name", "").lower()
                        or search_lower in agent.get("description", "").lower()
                    ):
                        continue

                filtered_agents.append(agent)

            # Sort agents
            if sort.startswith("-"):
                reverse = True
                sort_key = sort[1:]
            else:
                reverse = False
                sort_key = sort

            if sort_key == "name":
                filtered_agents.sort(key=lambda x: x.get("name", ""), reverse=reverse)
            elif sort_key == "created_at":
                # For now, maintain original order since we don't have created_at
                pass

            return templates.TemplateResponse(
                "agents_grid.html",
                {
                    "request": request,
                    "agents": filtered_agents,
                },
            )

        except Exception as e:
            log.error(f"Get agents API error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/jobs")
    async def get_jobs_api(
        request: Request,
        status: Optional[str] = Query(None),
        agent_name: Optional[str] = Query(None),
        search: Optional[str] = Query(None),
        sort: str = Query("-created_at"),
        limit: int = Query(50, le=100),
        skip: int = Query(0, ge=0),
        authorized: bool = Depends(verify_admin_access),
    ) -> Response:
        """Get jobs with filtering and pagination."""
        try:
            # Get all jobs
            jobs_data = storage.get_objects("Job")

            # Apply filters
            filtered_jobs = []
            for job_data in jobs_data:
                # Status filter
                if status and job_data.get("status") != status:
                    continue

                # Agent name filter
                if (
                    agent_name
                    and agent_name.lower() not in job_data.get("agent_name", "").lower()
                ):
                    continue

                # Search filter
                if search:
                    search_lower = search.lower()
                    if not (
                        search_lower in job_data.get("name", "").lower()
                        or search_lower in job_data.get("id", "").lower()
                    ):
                        continue

                filtered_jobs.append(job_data)

            # Sort jobs
            if sort.startswith("-"):
                reverse = True
                sort_key = sort[1:]
            else:
                reverse = False
                sort_key = sort

            if sort_key in ["created_at", "name", "status"]:
                filtered_jobs.sort(key=lambda x: x.get(sort_key, ""), reverse=reverse)

            # Apply pagination
            total = len(filtered_jobs)
            jobs_page = filtered_jobs[skip : skip + limit]

            # Format for display
            jobs = []
            for job_data in jobs_page:
                job = {
                    "id": job_data.get("id", ""),
                    "name": job_data.get("name", ""),
                    "agent_name": job_data.get("agent_name", ""),
                    "status": job_data.get("status", ""),
                    "created_at": job_data.get("created_at", ""),
                    "finished_at": job_data.get("finished_at"),
                    "case_count": len(job_data.get("case_ids", [])),
                }
                jobs.append(job)

            return templates.TemplateResponse(
                "jobs_table.html",
                {
                    "request": request,
                    "jobs": jobs,
                    "total": total,
                    "limit": limit,
                    "skip": skip,
                    "has_next": skip + limit < total,
                    "has_prev": skip > 0,
                },
            )

        except Exception as e:
            log.error(f"Get jobs API error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/jobs/{job_id}")
    async def get_job_details(
        request: Request, job_id: str, authorized: bool = Depends(verify_admin_access)
    ) -> Response:
        """Get detailed job information."""
        try:
            job_data = storage.get_object_by_id("Job", job_id)
            if not job_data:
                raise HTTPException(status_code=404, detail="Job not found")

            # Get related cases
            cases_data = storage.get_cases_for_job(job_id)

            return templates.TemplateResponse(
                "job_detail.html",
                {
                    "request": request,
                    "job": job_data,
                    "cases": cases_data,
                },
            )

        except HTTPException:
            raise
        except Exception as e:
            log.error(f"Get job details error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/cases")
    async def get_cases_api(
        request: Request,
        status: Optional[str] = Query(None),
        job_id: Optional[str] = Query(None),
        search: Optional[str] = Query(None),
        sort: str = Query("-created_at"),
        limit: int = Query(50, le=100),
        skip: int = Query(0, ge=0),
        authorized: bool = Depends(verify_admin_access),
    ) -> Response:
        """Get cases with filtering and pagination."""
        try:
            # Get all cases
            cases_data = storage.get_objects("Case")

            # Apply filters
            filtered_cases = []
            for case_data in cases_data:
                # Status filter
                if status and case_data.get("status") != status:
                    continue

                # Job ID filter
                if job_id and case_data.get("job_id") != job_id:
                    continue

                # Search filter
                if search:
                    search_lower = search.lower()
                    if not (
                        search_lower in case_data.get("name", "").lower()
                        or search_lower in case_data.get("id", "").lower()
                        or search_lower in case_data.get("description", "").lower()
                    ):
                        continue

                filtered_cases.append(case_data)

            # Sort cases
            if sort.startswith("-"):
                reverse = True
                sort_key = sort[1:]
            else:
                reverse = False
                sort_key = sort

            if sort_key in ["created_at", "name", "status", "total_cost"]:
                if sort_key == "total_cost":
                    filtered_cases.sort(
                        key=lambda x: x.get(sort_key, 0), reverse=reverse
                    )
                else:
                    filtered_cases.sort(
                        key=lambda x: x.get(sort_key, ""), reverse=reverse
                    )

            # Apply pagination
            total = len(filtered_cases)
            cases_page = filtered_cases[skip : skip + limit]

            # Format for display
            cases = []
            for case_data in cases_page:
                case = {
                    "id": case_data.get("id", ""),
                    "name": case_data.get("name", ""),
                    "description": case_data.get("description", ""),
                    "status": case_data.get("status", ""),
                    "job_id": case_data.get("job_id", ""),
                    "created_at": case_data.get("created_at", ""),
                    "finished_at": case_data.get("finished_at"),
                    "total_cost": case_data.get("total_cost", 0.0),
                }
                cases.append(case)

            return templates.TemplateResponse(
                "cases_table.html",
                {
                    "request": request,
                    "cases": cases,
                    "total": total,
                    "limit": limit,
                    "skip": skip,
                    "has_next": skip + limit < total,
                    "has_prev": skip > 0,
                },
            )

        except Exception as e:
            log.error(f"Get cases API error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/cases/{case_id}")
    async def get_case_details(
        request: Request, case_id: str, authorized: bool = Depends(verify_admin_access)
    ) -> Response:
        """Get detailed case information."""
        try:
            case_data = storage.get_object_by_id("Case", case_id)
            if not case_data:
                raise HTTPException(status_code=404, detail="Case not found")

            # Get parent job if exists
            job_data = None
            if case_data.get("job_id"):
                job_data = storage.get_object_by_id("Job", case_data["job_id"])

            return templates.TemplateResponse(
                "case_detail.html",
                {
                    "request": request,
                    "case": case_data,
                    "job": job_data,
                },
            )

        except HTTPException:
            raise
        except Exception as e:
            log.error(f"Get case details error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/jobs/{job_id}/status")
    async def update_job_status(
        job_id: str,
        status_data: Dict[str, str],
        authorized: bool = Depends(verify_admin_access),
    ) -> Dict[str, str]:
        """Update job status."""
        try:
            new_status = status_data.get("status")
            if not new_status or new_status not in [s.value for s in EntityStatus]:
                raise HTTPException(status_code=400, detail="Invalid status")

            job_data = storage.get_object_by_id("Job", job_id)
            if not job_data:
                raise HTTPException(status_code=404, detail="Job not found")

            # Update job status
            job_data["status"] = new_status
            if new_status in ["completed", "failed", "cancelled"]:
                job_data["finished_at"] = datetime.now().isoformat()

            storage.save_object("Job", job_data)

            return {"message": "Job status updated successfully"}

        except HTTPException:
            raise
        except Exception as e:
            log.error(f"Update job status error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/cases/{case_id}/status")
    async def update_case_status(
        case_id: str,
        status_data: Dict[str, str],
        authorized: bool = Depends(verify_admin_access),
    ) -> Dict[str, str]:
        """Update case status."""
        try:
            new_status = status_data.get("status")
            if not new_status or new_status not in [s.value for s in EntityStatus]:
                raise HTTPException(status_code=400, detail="Invalid status")

            case_data = storage.get_object_by_id("Case", case_id)
            if not case_data:
                raise HTTPException(status_code=404, detail="Case not found")

            # Update case status
            case_data["status"] = new_status
            if new_status in ["completed", "failed", "cancelled"]:
                case_data["finished_at"] = datetime.now().isoformat()

            storage.save_object("Case", case_data)

            return {"message": "Case status updated successfully"}

        except HTTPException:
            raise
        except Exception as e:
            log.error(f"Update case status error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/api/jobs/{job_id}")
    async def delete_job(
        job_id: str, authorized: bool = Depends(verify_admin_access)
    ) -> Dict[str, str]:
        """Delete a job and its related cases."""
        try:
            # Delete related cases first
            cases_data = storage.get_cases_for_job(job_id)
            for case_data in cases_data:
                storage.delete_object("Case", case_data["id"])

            # Delete the job
            deleted = storage.delete_object("Job", job_id)
            if not deleted:
                raise HTTPException(status_code=404, detail="Job not found")

            return {"message": "Job and related cases deleted successfully"}

        except HTTPException:
            raise
        except Exception as e:
            log.error(f"Delete job error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/api/cases/{case_id}")
    async def delete_case(
        case_id: str, authorized: bool = Depends(verify_admin_access)
    ) -> Dict[str, str]:
        """Delete a case."""
        try:
            deleted = storage.delete_object("Case", case_id)
            if not deleted:
                raise HTTPException(status_code=404, detail="Case not found")

            return {"message": "Case deleted successfully"}

        except HTTPException:
            raise
        except Exception as e:
            log.error(f"Delete case error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/recent-activity")
    async def get_recent_activity(
        request: Request, authorized: bool = Depends(verify_admin_access)
    ) -> Response:
        """Get recent entity activity."""
        try:
            # Get recent jobs and cases
            recent_jobs = storage.get_objects("Job")[-5:]  # Last 5 jobs
            recent_cases = storage.get_objects("Case")[-5:]  # Last 5 cases

            # Combine and sort by created_at
            activities = []
            for job in recent_jobs:
                activities.append({
                    "type": "job",
                    "id": job.get("id"),
                    "name": job.get("name"),
                    "status": job.get("status"),
                    "created_at": job.get("created_at"),
                    "agent_name": job.get("agent_name"),
                })

            for case in recent_cases:
                activities.append({
                    "type": "case",
                    "id": case.get("id"),
                    "name": case.get("name"),
                    "status": case.get("status"),
                    "created_at": case.get("created_at"),
                    "job_id": case.get("job_id"),
                })

            # Sort by created_at descending
            activities.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
            activities = activities[:10]  # Top 10 recent activities

            return templates.TemplateResponse(
                "recent_activity.html",
                {
                    "request": request,
                    "activities": activities,
                },
            )

        except Exception as e:
            log.error(f"Get recent activity error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/log-stream")
    async def log_stream(
        authorized: bool = Depends(verify_admin_access),
    ) -> EventSourceResponse:
        """Stream log messages via Server-Sent Events."""

        async def generate_log_events() -> AsyncGenerator[str, None]:
            try:
                while True:
                    # Wait for a log message
                    log_message = await log_queue.get()
                    # Format as SSE event
                    event_data = json.dumps(log_message)
                    yield f"data: {event_data}\n\n"
            except asyncio.CancelledError:
                # Client disconnected
                pass
            except Exception as e:
                # Send error and close
                error_data = json.dumps({
                    "timestamp": datetime.now().isoformat(),
                    "level": "ERROR",
                    "message": f"Log stream error: {str(e)}",
                })
                yield f"data: {error_data}\n\n"

        return EventSourceResponse(generate_log_events())

    return router


def get_dashboard_stats(storage: StorageManager) -> AdminStats:
    """Get statistics for dashboard."""
    try:
        # Get all jobs and cases
        all_jobs = storage.get_objects("Job")
        all_cases = storage.get_objects("Case")

        # Calculate job stats
        job_total = len(all_jobs)
        job_running = len([
            j for j in all_jobs if j.get("status") in ["in_progress", "awaiting"]
        ])
        job_completed = len([j for j in all_jobs if j.get("status") == "completed"])
        job_failed = len([
            j for j in all_jobs if j.get("status") in ["failed", "cancelled"]
        ])

        # Calculate case stats
        case_total = len(all_cases)
        case_running = len([
            c for c in all_cases if c.get("status") in ["in_progress", "awaiting"]
        ])
        case_completed = len([c for c in all_cases if c.get("status") == "completed"])
        case_failed = len([
            c for c in all_cases if c.get("status") in ["failed", "cancelled"]
        ])

        # TinyDB collections count (tables)
        collections_count = len(storage._db.tables())

        return AdminStats(
            jobs={
                "total": job_total,
                "running": job_running,
                "completed": job_completed,
                "failed": job_failed,
            },
            cases={
                "total": case_total,
                "running": case_running,
                "completed": case_completed,
                "failed": case_failed,
            },
            collections=collections_count,
        )

    except Exception as e:
        log.error(f"Get dashboard stats error: {e}")
        return AdminStats(
            jobs={"total": 0, "running": 0, "completed": 0, "failed": 0},
            cases={"total": 0, "running": 0, "completed": 0, "failed": 0},
            collections=0,
        )
