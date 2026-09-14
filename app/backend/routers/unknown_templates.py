import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.unknown_templates import Unknown_templatesService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/unknown_templates", tags=["unknown_templates"])


# ---------- Pydantic Schemas ----------
class Unknown_templatesData(BaseModel):
    """Entity data schema (for create/update)"""
    last_seen_service: str = None
    sample_count: int = None
    status: str = None
    suggested_error_type: str = None
    template: str


class Unknown_templatesUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    last_seen_service: Optional[str] = None
    sample_count: Optional[int] = None
    status: Optional[str] = None
    suggested_error_type: Optional[str] = None
    template: Optional[str] = None


class Unknown_templatesResponse(BaseModel):
    """Entity response schema"""
    id: int
    last_seen_service: Optional[str] = None
    sample_count: Optional[int] = None
    status: Optional[str] = None
    suggested_error_type: Optional[str] = None
    template: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Unknown_templatesListResponse(BaseModel):
    """List response schema"""
    items: List[Unknown_templatesResponse]
    total: int
    skip: int
    limit: int


class Unknown_templatesBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Unknown_templatesData]


class Unknown_templatesBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Unknown_templatesUpdateData


class Unknown_templatesBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Unknown_templatesBatchUpdateItem]


class Unknown_templatesBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Unknown_templatesListResponse)
async def query_unknown_templatess(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query unknown_templatess with filtering, sorting, and pagination"""
    logger.debug(f"Querying unknown_templatess: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Unknown_templatesService(db)
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
        logger.debug(f"Found {result['total']} unknown_templatess")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid unknown_templates query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying unknown_templatess: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Unknown_templatesListResponse)
async def query_unknown_templatess_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query unknown_templatess with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying unknown_templatess: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Unknown_templatesService(db)
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
        logger.debug(f"Found {result['total']} unknown_templatess")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid unknown_templates query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying unknown_templatess: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Unknown_templatesResponse)
async def get_unknown_templates(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single unknown_templates by ID"""
    logger.debug(f"Fetching unknown_templates with id: {id}, fields={fields}")
    
    service = Unknown_templatesService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Unknown_templates with id {id} not found")
            raise HTTPException(status_code=404, detail="Unknown_templates not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching unknown_templates {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Unknown_templatesResponse, status_code=201)
async def create_unknown_templates(
    data: Unknown_templatesData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new unknown_templates"""
    logger.debug(f"Creating new unknown_templates with data: {data}")
    
    service = Unknown_templatesService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create unknown_templates")
        
        logger.info(f"Unknown_templates created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating unknown_templates: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating unknown_templates: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Unknown_templatesResponse], status_code=201)
async def create_unknown_templatess_batch(
    request: Unknown_templatesBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple unknown_templatess in a single request"""
    logger.debug(f"Batch creating {len(request.items)} unknown_templatess")
    
    service = Unknown_templatesService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} unknown_templatess successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Unknown_templatesResponse])
async def update_unknown_templatess_batch(
    request: Unknown_templatesBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple unknown_templatess in a single request"""
    logger.debug(f"Batch updating {len(request.items)} unknown_templatess")
    
    service = Unknown_templatesService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} unknown_templatess successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Unknown_templatesResponse)
async def update_unknown_templates(
    id: int,
    data: Unknown_templatesUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing unknown_templates"""
    logger.debug(f"Updating unknown_templates {id} with data: {data}")

    service = Unknown_templatesService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Unknown_templates with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Unknown_templates not found")
        
        logger.info(f"Unknown_templates {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating unknown_templates {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating unknown_templates {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_unknown_templatess_batch(
    request: Unknown_templatesBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple unknown_templatess by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} unknown_templatess")
    
    service = Unknown_templatesService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} unknown_templatess successfully")
        return {"message": f"Successfully deleted {deleted_count} unknown_templatess", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_unknown_templates(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single unknown_templates by ID"""
    logger.debug(f"Deleting unknown_templates with id: {id}")
    
    service = Unknown_templatesService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Unknown_templates with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Unknown_templates not found")
        
        logger.info(f"Unknown_templates {id} deleted successfully")
        return {"message": "Unknown_templates deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting unknown_templates {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")