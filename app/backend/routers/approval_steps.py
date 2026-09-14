import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.approval_steps import Approval_stepsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/approval_steps", tags=["approval_steps"])


# ---------- Pydantic Schemas ----------
class Approval_stepsData(BaseModel):
    """Entity data schema (for create/update)"""
    action: str = None
    acted_at: str = None
    approver: str = None
    approver_role: str
    comment: str = None
    request_id: int
    step_no: int


class Approval_stepsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    action: Optional[str] = None
    acted_at: Optional[str] = None
    approver: Optional[str] = None
    approver_role: Optional[str] = None
    comment: Optional[str] = None
    request_id: Optional[int] = None
    step_no: Optional[int] = None


class Approval_stepsResponse(BaseModel):
    """Entity response schema"""
    id: int
    action: Optional[str] = None
    acted_at: Optional[str] = None
    approver: Optional[str] = None
    approver_role: str
    comment: Optional[str] = None
    request_id: int
    step_no: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Approval_stepsListResponse(BaseModel):
    """List response schema"""
    items: List[Approval_stepsResponse]
    total: int
    skip: int
    limit: int


class Approval_stepsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Approval_stepsData]


class Approval_stepsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Approval_stepsUpdateData


class Approval_stepsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Approval_stepsBatchUpdateItem]


class Approval_stepsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Approval_stepsListResponse)
async def query_approval_stepss(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query approval_stepss with filtering, sorting, and pagination"""
    logger.debug(f"Querying approval_stepss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Approval_stepsService(db)
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
        logger.debug(f"Found {result['total']} approval_stepss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid approval_steps query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying approval_stepss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Approval_stepsListResponse)
async def query_approval_stepss_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query approval_stepss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying approval_stepss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Approval_stepsService(db)
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
        logger.debug(f"Found {result['total']} approval_stepss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid approval_steps query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying approval_stepss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Approval_stepsResponse)
async def get_approval_steps(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single approval_steps by ID"""
    logger.debug(f"Fetching approval_steps with id: {id}, fields={fields}")
    
    service = Approval_stepsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Approval_steps with id {id} not found")
            raise HTTPException(status_code=404, detail="Approval_steps not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching approval_steps {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Approval_stepsResponse, status_code=201)
async def create_approval_steps(
    data: Approval_stepsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new approval_steps"""
    logger.debug(f"Creating new approval_steps with data: {data}")
    
    service = Approval_stepsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create approval_steps")
        
        logger.info(f"Approval_steps created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating approval_steps: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating approval_steps: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Approval_stepsResponse], status_code=201)
async def create_approval_stepss_batch(
    request: Approval_stepsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple approval_stepss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} approval_stepss")
    
    service = Approval_stepsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} approval_stepss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Approval_stepsResponse])
async def update_approval_stepss_batch(
    request: Approval_stepsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple approval_stepss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} approval_stepss")
    
    service = Approval_stepsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} approval_stepss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Approval_stepsResponse)
async def update_approval_steps(
    id: int,
    data: Approval_stepsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing approval_steps"""
    logger.debug(f"Updating approval_steps {id} with data: {data}")

    service = Approval_stepsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Approval_steps with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Approval_steps not found")
        
        logger.info(f"Approval_steps {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating approval_steps {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating approval_steps {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_approval_stepss_batch(
    request: Approval_stepsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple approval_stepss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} approval_stepss")
    
    service = Approval_stepsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} approval_stepss successfully")
        return {"message": f"Successfully deleted {deleted_count} approval_stepss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_approval_steps(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single approval_steps by ID"""
    logger.debug(f"Deleting approval_steps with id: {id}")
    
    service = Approval_stepsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Approval_steps with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Approval_steps not found")
        
        logger.info(f"Approval_steps {id} deleted successfully")
        return {"message": "Approval_steps deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting approval_steps {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")