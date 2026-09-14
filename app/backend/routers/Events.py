import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.Events import EventsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/Events", tags=["Events"])


# ---------- Pydantic Schemas ----------
class EventsData(BaseModel):
    """Entity data schema (for create/update)"""
    ai_command: str = None
    ai_output_json: str = None
    ai_root_cause: str = None
    ai_solution: str = None
    candidates_json: str = None
    cluster: str = None
    confidence: float = None
    degraded_reason: str = None
    error_type: str = None
    event_id: str
    fingerprint: str = None
    rag_ms: float = None
    rag_score: float = None
    rag_status: str = None
    raw_log: str = None
    service_name: str
    severity: str
    std_ms: float = None
    status: str = None
    template: str = None
    topology: str = None


class EventsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    ai_command: Optional[str] = None
    ai_output_json: Optional[str] = None
    ai_root_cause: Optional[str] = None
    ai_solution: Optional[str] = None
    candidates_json: Optional[str] = None
    cluster: Optional[str] = None
    confidence: Optional[float] = None
    degraded_reason: Optional[str] = None
    error_type: Optional[str] = None
    event_id: Optional[str] = None
    fingerprint: Optional[str] = None
    rag_ms: Optional[float] = None
    rag_score: Optional[float] = None
    rag_status: Optional[str] = None
    raw_log: Optional[str] = None
    service_name: Optional[str] = None
    severity: Optional[str] = None
    std_ms: Optional[float] = None
    status: Optional[str] = None
    template: Optional[str] = None
    topology: Optional[str] = None


class EventsResponse(BaseModel):
    """Entity response schema"""
    id: int
    ai_command: Optional[str] = None
    ai_output_json: Optional[str] = None
    ai_root_cause: Optional[str] = None
    ai_solution: Optional[str] = None
    candidates_json: Optional[str] = None
    cluster: Optional[str] = None
    confidence: Optional[float] = None
    degraded_reason: Optional[str] = None
    error_type: Optional[str] = None
    event_id: str
    fingerprint: Optional[str] = None
    rag_ms: Optional[float] = None
    rag_score: Optional[float] = None
    rag_status: Optional[str] = None
    raw_log: Optional[str] = None
    service_name: str
    severity: str
    std_ms: Optional[float] = None
    status: Optional[str] = None
    template: Optional[str] = None
    topology: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class EventsListResponse(BaseModel):
    """List response schema"""
    items: List[EventsResponse]
    total: int
    skip: int
    limit: int


class EventsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[EventsData]


class EventsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: EventsUpdateData


class EventsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[EventsBatchUpdateItem]


class EventsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=EventsListResponse)
async def query_Eventss(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query Eventss with filtering, sorting, and pagination"""
    logger.debug(f"Querying Eventss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = EventsService(db)
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
        logger.debug(f"Found {result['total']} Eventss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid Events query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying Eventss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=EventsListResponse)
async def query_Eventss_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query Eventss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying Eventss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = EventsService(db)
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
        logger.debug(f"Found {result['total']} Eventss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid Events query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying Eventss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=EventsResponse)
async def get_Events(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single Events by ID"""
    logger.debug(f"Fetching Events with id: {id}, fields={fields}")
    
    service = EventsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Events with id {id} not found")
            raise HTTPException(status_code=404, detail="Events not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching Events {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=EventsResponse, status_code=201)
async def create_Events(
    data: EventsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new Events"""
    logger.debug(f"Creating new Events with data: {data}")
    
    service = EventsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create Events")
        
        logger.info(f"Events created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating Events: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating Events: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[EventsResponse], status_code=201)
async def create_Eventss_batch(
    request: EventsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple Eventss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} Eventss")
    
    service = EventsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} Eventss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[EventsResponse])
async def update_Eventss_batch(
    request: EventsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple Eventss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} Eventss")
    
    service = EventsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} Eventss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=EventsResponse)
async def update_Events(
    id: int,
    data: EventsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing Events"""
    logger.debug(f"Updating Events {id} with data: {data}")

    service = EventsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Events with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Events not found")
        
        logger.info(f"Events {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating Events {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating Events {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_Eventss_batch(
    request: EventsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple Eventss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} Eventss")
    
    service = EventsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} Eventss successfully")
        return {"message": f"Successfully deleted {deleted_count} Eventss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_Events(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single Events by ID"""
    logger.debug(f"Deleting Events with id: {id}")
    
    service = EventsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Events with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Events not found")
        
        logger.info(f"Events {id} deleted successfully")
        return {"message": "Events deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting Events {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")