import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.agent_sessions import Agent_sessionsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/agent_sessions", tags=["agent_sessions"])


# ---------- Pydantic Schemas ----------
class Agent_sessionsData(BaseModel):
    """Entity data schema (for create/update)"""
    session_type: str
    event_id: int = None
    status: str
    model: str = None
    iterations: int = None
    duration_ms: float = None
    tool_trace: str = None
    result_json: str = None
    error_message: str = None
    actor: str = None
    summary: str = None


class Agent_sessionsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    session_type: Optional[str] = None
    event_id: Optional[int] = None
    status: Optional[str] = None
    model: Optional[str] = None
    iterations: Optional[int] = None
    duration_ms: Optional[float] = None
    tool_trace: Optional[str] = None
    result_json: Optional[str] = None
    error_message: Optional[str] = None
    actor: Optional[str] = None
    summary: Optional[str] = None


class Agent_sessionsResponse(BaseModel):
    """Entity response schema"""
    id: int
    session_type: str
    event_id: Optional[int] = None
    status: str
    model: Optional[str] = None
    iterations: Optional[int] = None
    duration_ms: Optional[float] = None
    tool_trace: Optional[str] = None
    result_json: Optional[str] = None
    error_message: Optional[str] = None
    actor: Optional[str] = None
    summary: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Agent_sessionsListResponse(BaseModel):
    """List response schema"""
    items: List[Agent_sessionsResponse]
    total: int
    skip: int
    limit: int


class Agent_sessionsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Agent_sessionsData]


class Agent_sessionsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Agent_sessionsUpdateData


class Agent_sessionsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Agent_sessionsBatchUpdateItem]


class Agent_sessionsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Agent_sessionsListResponse)
async def query_agent_sessionss(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query agent_sessionss with filtering, sorting, and pagination"""
    logger.debug(f"Querying agent_sessionss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Agent_sessionsService(db)
    try:
        # Parse query JSON if provided
        query_dict = None
        if query:
            try:
                query_dict = json.loads(query)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid query JSON format")
        
        result = await service.get_list(
            skip=skip, 
            limit=limit,
            query_dict=query_dict,
            sort=sort,
        )
        logger.debug(f"Found {result['total']} agent_sessionss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid agent_sessions query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying agent_sessionss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Agent_sessionsListResponse)
async def query_agent_sessionss_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query agent_sessionss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying agent_sessionss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Agent_sessionsService(db)
    try:
        # Parse query JSON if provided
        query_dict = None
        if query:
            try:
                query_dict = json.loads(query)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid query JSON format")

        result = await service.get_list(
            skip=skip,
            limit=limit,
            query_dict=query_dict,
            sort=sort
        )
        logger.debug(f"Found {result['total']} agent_sessionss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid agent_sessions query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying agent_sessionss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Agent_sessionsResponse)
async def get_agent_sessions(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single agent_sessions by ID"""
    logger.debug(f"Fetching agent_sessions with id: {id}, fields={fields}")
    
    service = Agent_sessionsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Agent_sessions with id {id} not found")
            raise HTTPException(status_code=404, detail="Agent_sessions not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching agent_sessions {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Agent_sessionsResponse, status_code=201)
async def create_agent_sessions(
    data: Agent_sessionsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new agent_sessions"""
    logger.debug(f"Creating new agent_sessions with data: {data}")
    
    service = Agent_sessionsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create agent_sessions")
        
        logger.info(f"Agent_sessions created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating agent_sessions: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating agent_sessions: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Agent_sessionsResponse], status_code=201)
async def create_agent_sessionss_batch(
    request: Agent_sessionsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple agent_sessionss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} agent_sessionss")
    
    service = Agent_sessionsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} agent_sessionss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Agent_sessionsResponse])
async def update_agent_sessionss_batch(
    request: Agent_sessionsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple agent_sessionss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} agent_sessionss")
    
    service = Agent_sessionsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} agent_sessionss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Agent_sessionsResponse)
async def update_agent_sessions(
    id: int,
    data: Agent_sessionsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing agent_sessions"""
    logger.debug(f"Updating agent_sessions {id} with data: {data}")

    service = Agent_sessionsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Agent_sessions with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Agent_sessions not found")
        
        logger.info(f"Agent_sessions {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating agent_sessions {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating agent_sessions {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_agent_sessionss_batch(
    request: Agent_sessionsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple agent_sessionss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} agent_sessionss")
    
    service = Agent_sessionsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} agent_sessionss successfully")
        return {"message": f"Successfully deleted {deleted_count} agent_sessionss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_agent_sessions(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single agent_sessions by ID"""
    logger.debug(f"Deleting agent_sessions with id: {id}")
    
    service = Agent_sessionsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Agent_sessions with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Agent_sessions not found")
        
        logger.info(f"Agent_sessions {id} deleted successfully")
        return {"message": "Agent_sessions deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting agent_sessions {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")