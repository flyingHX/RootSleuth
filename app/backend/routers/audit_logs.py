import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.audit_logs import Audit_logsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/audit_logs", tags=["audit_logs"])


# ---------- Pydantic Schemas ----------
class Audit_logsData(BaseModel):
    """Entity data schema (for create/update)"""
    action: str
    actor: str
    after_json: str = None
    before_json: str = None
    ip: str = None
    target_id: str
    target_type: str
    ua: str = None


class Audit_logsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    action: Optional[str] = None
    actor: Optional[str] = None
    after_json: Optional[str] = None
    before_json: Optional[str] = None
    ip: Optional[str] = None
    target_id: Optional[str] = None
    target_type: Optional[str] = None
    ua: Optional[str] = None


class Audit_logsResponse(BaseModel):
    """Entity response schema"""
    id: int
    action: str
    actor: str
    after_json: Optional[str] = None
    before_json: Optional[str] = None
    ip: Optional[str] = None
    target_id: str
    target_type: str
    ua: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Audit_logsListResponse(BaseModel):
    """List response schema"""
    items: List[Audit_logsResponse]
    total: int
    skip: int
    limit: int


class Audit_logsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Audit_logsData]


class Audit_logsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Audit_logsUpdateData


class Audit_logsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Audit_logsBatchUpdateItem]


class Audit_logsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Audit_logsListResponse)
async def query_audit_logss(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query audit_logss with filtering, sorting, and pagination"""
    logger.debug(f"Querying audit_logss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Audit_logsService(db)
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
        logger.debug(f"Found {result['total']} audit_logss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid audit_logs query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying audit_logss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Audit_logsListResponse)
async def query_audit_logss_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query audit_logss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying audit_logss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Audit_logsService(db)
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
        logger.debug(f"Found {result['total']} audit_logss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid audit_logs query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying audit_logss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Audit_logsResponse)
async def get_audit_logs(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single audit_logs by ID"""
    logger.debug(f"Fetching audit_logs with id: {id}, fields={fields}")
    
    service = Audit_logsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Audit_logs with id {id} not found")
            raise HTTPException(status_code=404, detail="Audit_logs not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching audit_logs {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Audit_logsResponse, status_code=201)
async def create_audit_logs(
    data: Audit_logsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new audit_logs"""
    logger.debug(f"Creating new audit_logs with data: {data}")
    
    service = Audit_logsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create audit_logs")
        
        logger.info(f"Audit_logs created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating audit_logs: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating audit_logs: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Audit_logsResponse], status_code=201)
async def create_audit_logss_batch(
    request: Audit_logsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple audit_logss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} audit_logss")
    
    service = Audit_logsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} audit_logss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Audit_logsResponse])
async def update_audit_logss_batch(
    request: Audit_logsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple audit_logss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} audit_logss")
    
    service = Audit_logsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} audit_logss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Audit_logsResponse)
async def update_audit_logs(
    id: int,
    data: Audit_logsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing audit_logs"""
    logger.debug(f"Updating audit_logs {id} with data: {data}")

    service = Audit_logsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Audit_logs with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Audit_logs not found")
        
        logger.info(f"Audit_logs {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating audit_logs {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating audit_logs {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_audit_logss_batch(
    request: Audit_logsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple audit_logss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} audit_logss")
    
    service = Audit_logsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} audit_logss successfully")
        return {"message": f"Successfully deleted {deleted_count} audit_logss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_audit_logs(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single audit_logs by ID"""
    logger.debug(f"Deleting audit_logs with id: {id}")
    
    service = Audit_logsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Audit_logs with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Audit_logs not found")
        
        logger.info(f"Audit_logs {id} deleted successfully")
        return {"message": "Audit_logs deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting audit_logs {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")