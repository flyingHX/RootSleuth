import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.kb_change_sets import Kb_change_setsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/kb_change_sets", tags=["kb_change_sets"])


# ---------- Pydantic Schemas ----------
class Kb_change_setsData(BaseModel):
    """Entity data schema (for create/update)"""
    after_json: str = None
    approval_request_id: int = None
    before_json: str = None
    case_id: str
    change_type: str
    created_by: str
    diff_json: str = None
    reason: str = None
    status: str = None
    version: int = None


class Kb_change_setsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    after_json: Optional[str] = None
    approval_request_id: Optional[int] = None
    before_json: Optional[str] = None
    case_id: Optional[str] = None
    change_type: Optional[str] = None
    created_by: Optional[str] = None
    diff_json: Optional[str] = None
    reason: Optional[str] = None
    status: Optional[str] = None
    version: Optional[int] = None


class Kb_change_setsResponse(BaseModel):
    """Entity response schema"""
    id: int
    after_json: Optional[str] = None
    approval_request_id: Optional[int] = None
    before_json: Optional[str] = None
    case_id: str
    change_type: str
    created_by: str
    diff_json: Optional[str] = None
    reason: Optional[str] = None
    status: Optional[str] = None
    version: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Kb_change_setsListResponse(BaseModel):
    """List response schema"""
    items: List[Kb_change_setsResponse]
    total: int
    skip: int
    limit: int


class Kb_change_setsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Kb_change_setsData]


class Kb_change_setsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Kb_change_setsUpdateData


class Kb_change_setsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Kb_change_setsBatchUpdateItem]


class Kb_change_setsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Kb_change_setsListResponse)
async def query_kb_change_setss(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query kb_change_setss with filtering, sorting, and pagination"""
    logger.debug(f"Querying kb_change_setss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Kb_change_setsService(db)
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
        logger.debug(f"Found {result['total']} kb_change_setss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid kb_change_sets query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying kb_change_setss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Kb_change_setsListResponse)
async def query_kb_change_setss_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query kb_change_setss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying kb_change_setss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Kb_change_setsService(db)
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
        logger.debug(f"Found {result['total']} kb_change_setss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid kb_change_sets query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying kb_change_setss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Kb_change_setsResponse)
async def get_kb_change_sets(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single kb_change_sets by ID"""
    logger.debug(f"Fetching kb_change_sets with id: {id}, fields={fields}")
    
    service = Kb_change_setsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Kb_change_sets with id {id} not found")
            raise HTTPException(status_code=404, detail="Kb_change_sets not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching kb_change_sets {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Kb_change_setsResponse, status_code=201)
async def create_kb_change_sets(
    data: Kb_change_setsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new kb_change_sets"""
    logger.debug(f"Creating new kb_change_sets with data: {data}")
    
    service = Kb_change_setsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create kb_change_sets")
        
        logger.info(f"Kb_change_sets created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating kb_change_sets: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating kb_change_sets: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Kb_change_setsResponse], status_code=201)
async def create_kb_change_setss_batch(
    request: Kb_change_setsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple kb_change_setss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} kb_change_setss")
    
    service = Kb_change_setsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} kb_change_setss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Kb_change_setsResponse])
async def update_kb_change_setss_batch(
    request: Kb_change_setsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple kb_change_setss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} kb_change_setss")
    
    service = Kb_change_setsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} kb_change_setss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Kb_change_setsResponse)
async def update_kb_change_sets(
    id: int,
    data: Kb_change_setsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing kb_change_sets"""
    logger.debug(f"Updating kb_change_sets {id} with data: {data}")

    service = Kb_change_setsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Kb_change_sets with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Kb_change_sets not found")
        
        logger.info(f"Kb_change_sets {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating kb_change_sets {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating kb_change_sets {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_kb_change_setss_batch(
    request: Kb_change_setsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple kb_change_setss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} kb_change_setss")
    
    service = Kb_change_setsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} kb_change_setss successfully")
        return {"message": f"Successfully deleted {deleted_count} kb_change_setss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_kb_change_sets(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single kb_change_sets by ID"""
    logger.debug(f"Deleting kb_change_sets with id: {id}")
    
    service = Kb_change_setsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Kb_change_sets with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Kb_change_sets not found")
        
        logger.info(f"Kb_change_sets {id} deleted successfully")
        return {"message": "Kb_change_sets deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting kb_change_sets {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")