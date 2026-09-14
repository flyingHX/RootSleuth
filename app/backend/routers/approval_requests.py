import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.approval_requests import Approval_requestsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/approval_requests", tags=["approval_requests"])


# ---------- Pydantic Schemas ----------
class Approval_requestsData(BaseModel):
    """Entity data schema (for create/update)"""
    applicant: str
    applicant_role: str = None
    biz_id: str = None
    biz_type: str
    current_step: int = None
    published_at: str = None
    reason: str = None
    risk_level: str = None
    status: str = None
    title: str
    total_steps: int = None


class Approval_requestsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    applicant: Optional[str] = None
    applicant_role: Optional[str] = None
    biz_id: Optional[str] = None
    biz_type: Optional[str] = None
    current_step: Optional[int] = None
    published_at: Optional[str] = None
    reason: Optional[str] = None
    risk_level: Optional[str] = None
    status: Optional[str] = None
    title: Optional[str] = None
    total_steps: Optional[int] = None


class Approval_requestsResponse(BaseModel):
    """Entity response schema"""
    id: int
    applicant: str
    applicant_role: Optional[str] = None
    biz_id: Optional[str] = None
    biz_type: str
    current_step: Optional[int] = None
    published_at: Optional[str] = None
    reason: Optional[str] = None
    risk_level: Optional[str] = None
    status: Optional[str] = None
    title: str
    total_steps: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Approval_requestsListResponse(BaseModel):
    """List response schema"""
    items: List[Approval_requestsResponse]
    total: int
    skip: int
    limit: int


class Approval_requestsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Approval_requestsData]


class Approval_requestsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Approval_requestsUpdateData


class Approval_requestsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Approval_requestsBatchUpdateItem]


class Approval_requestsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Approval_requestsListResponse)
async def query_approval_requestss(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query approval_requestss with filtering, sorting, and pagination"""
    logger.debug(f"Querying approval_requestss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Approval_requestsService(db)
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
        logger.debug(f"Found {result['total']} approval_requestss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid approval_requests query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying approval_requestss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Approval_requestsListResponse)
async def query_approval_requestss_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query approval_requestss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying approval_requestss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Approval_requestsService(db)
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
        logger.debug(f"Found {result['total']} approval_requestss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid approval_requests query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying approval_requestss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Approval_requestsResponse)
async def get_approval_requests(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single approval_requests by ID"""
    logger.debug(f"Fetching approval_requests with id: {id}, fields={fields}")
    
    service = Approval_requestsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Approval_requests with id {id} not found")
            raise HTTPException(status_code=404, detail="Approval_requests not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching approval_requests {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Approval_requestsResponse, status_code=201)
async def create_approval_requests(
    data: Approval_requestsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new approval_requests"""
    logger.debug(f"Creating new approval_requests with data: {data}")
    
    service = Approval_requestsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create approval_requests")
        
        logger.info(f"Approval_requests created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating approval_requests: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating approval_requests: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Approval_requestsResponse], status_code=201)
async def create_approval_requestss_batch(
    request: Approval_requestsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple approval_requestss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} approval_requestss")
    
    service = Approval_requestsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} approval_requestss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Approval_requestsResponse])
async def update_approval_requestss_batch(
    request: Approval_requestsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple approval_requestss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} approval_requestss")
    
    service = Approval_requestsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} approval_requestss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Approval_requestsResponse)
async def update_approval_requests(
    id: int,
    data: Approval_requestsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing approval_requests"""
    logger.debug(f"Updating approval_requests {id} with data: {data}")

    service = Approval_requestsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Approval_requests with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Approval_requests not found")
        
        logger.info(f"Approval_requests {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating approval_requests {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating approval_requests {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_approval_requestss_batch(
    request: Approval_requestsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple approval_requestss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} approval_requestss")
    
    service = Approval_requestsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} approval_requestss successfully")
        return {"message": f"Successfully deleted {deleted_count} approval_requestss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_approval_requests(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single approval_requests by ID"""
    logger.debug(f"Deleting approval_requests with id: {id}")
    
    service = Approval_requestsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Approval_requests with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Approval_requests not found")
        
        logger.info(f"Approval_requests {id} deleted successfully")
        return {"message": "Approval_requests deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting approval_requests {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")