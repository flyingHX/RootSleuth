import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.kb_merge_proposals import Kb_merge_proposalsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/kb_merge_proposals", tags=["kb_merge_proposals"])


# ---------- Pydantic Schemas ----------
class Kb_merge_proposalsData(BaseModel):
    """Entity data schema (for create/update)"""
    approval_request_id: int = None
    created_by: str = None
    master_case_id: str
    merged_case_ids: str
    merge_strategy_json: str = None
    reason: str = None
    status: str = None


class Kb_merge_proposalsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    approval_request_id: Optional[int] = None
    created_by: Optional[str] = None
    master_case_id: Optional[str] = None
    merged_case_ids: Optional[str] = None
    merge_strategy_json: Optional[str] = None
    reason: Optional[str] = None
    status: Optional[str] = None


class Kb_merge_proposalsResponse(BaseModel):
    """Entity response schema"""
    id: int
    approval_request_id: Optional[int] = None
    created_by: Optional[str] = None
    master_case_id: str
    merged_case_ids: str
    merge_strategy_json: Optional[str] = None
    reason: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Kb_merge_proposalsListResponse(BaseModel):
    """List response schema"""
    items: List[Kb_merge_proposalsResponse]
    total: int
    skip: int
    limit: int


class Kb_merge_proposalsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Kb_merge_proposalsData]


class Kb_merge_proposalsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Kb_merge_proposalsUpdateData


class Kb_merge_proposalsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Kb_merge_proposalsBatchUpdateItem]


class Kb_merge_proposalsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Kb_merge_proposalsListResponse)
async def query_kb_merge_proposalss(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query kb_merge_proposalss with filtering, sorting, and pagination"""
    logger.debug(f"Querying kb_merge_proposalss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Kb_merge_proposalsService(db)
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
        logger.debug(f"Found {result['total']} kb_merge_proposalss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid kb_merge_proposals query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying kb_merge_proposalss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Kb_merge_proposalsListResponse)
async def query_kb_merge_proposalss_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query kb_merge_proposalss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying kb_merge_proposalss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Kb_merge_proposalsService(db)
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
        logger.debug(f"Found {result['total']} kb_merge_proposalss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid kb_merge_proposals query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying kb_merge_proposalss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Kb_merge_proposalsResponse)
async def get_kb_merge_proposals(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single kb_merge_proposals by ID"""
    logger.debug(f"Fetching kb_merge_proposals with id: {id}, fields={fields}")
    
    service = Kb_merge_proposalsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Kb_merge_proposals with id {id} not found")
            raise HTTPException(status_code=404, detail="Kb_merge_proposals not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching kb_merge_proposals {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Kb_merge_proposalsResponse, status_code=201)
async def create_kb_merge_proposals(
    data: Kb_merge_proposalsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new kb_merge_proposals"""
    logger.debug(f"Creating new kb_merge_proposals with data: {data}")
    
    service = Kb_merge_proposalsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create kb_merge_proposals")
        
        logger.info(f"Kb_merge_proposals created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating kb_merge_proposals: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating kb_merge_proposals: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Kb_merge_proposalsResponse], status_code=201)
async def create_kb_merge_proposalss_batch(
    request: Kb_merge_proposalsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple kb_merge_proposalss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} kb_merge_proposalss")
    
    service = Kb_merge_proposalsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} kb_merge_proposalss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Kb_merge_proposalsResponse])
async def update_kb_merge_proposalss_batch(
    request: Kb_merge_proposalsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple kb_merge_proposalss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} kb_merge_proposalss")
    
    service = Kb_merge_proposalsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} kb_merge_proposalss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Kb_merge_proposalsResponse)
async def update_kb_merge_proposals(
    id: int,
    data: Kb_merge_proposalsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing kb_merge_proposals"""
    logger.debug(f"Updating kb_merge_proposals {id} with data: {data}")

    service = Kb_merge_proposalsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Kb_merge_proposals with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Kb_merge_proposals not found")
        
        logger.info(f"Kb_merge_proposals {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating kb_merge_proposals {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating kb_merge_proposals {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_kb_merge_proposalss_batch(
    request: Kb_merge_proposalsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple kb_merge_proposalss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} kb_merge_proposalss")
    
    service = Kb_merge_proposalsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} kb_merge_proposalss successfully")
        return {"message": f"Successfully deleted {deleted_count} kb_merge_proposalss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_kb_merge_proposals(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single kb_merge_proposals by ID"""
    logger.debug(f"Deleting kb_merge_proposals with id: {id}")
    
    service = Kb_merge_proposalsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Kb_merge_proposals with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Kb_merge_proposals not found")
        
        logger.info(f"Kb_merge_proposals {id} deleted successfully")
        return {"message": "Kb_merge_proposals deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting kb_merge_proposals {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")