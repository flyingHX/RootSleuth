import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.console_configs import Console_configsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/console_configs", tags=["console_configs"])


# ---------- Pydantic Schemas ----------
class Console_configsData(BaseModel):
    """Entity data schema (for create/update)"""
    config_key: str
    config_value: str
    description: str = None


class Console_configsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    config_key: Optional[str] = None
    config_value: Optional[str] = None
    description: Optional[str] = None


class Console_configsResponse(BaseModel):
    """Entity response schema"""
    id: int
    config_key: str
    config_value: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Console_configsListResponse(BaseModel):
    """List response schema"""
    items: List[Console_configsResponse]
    total: int
    skip: int
    limit: int


class Console_configsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Console_configsData]


class Console_configsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Console_configsUpdateData


class Console_configsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Console_configsBatchUpdateItem]


class Console_configsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Console_configsListResponse)
async def query_console_configss(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query console_configss with filtering, sorting, and pagination"""
    logger.debug(f"Querying console_configss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Console_configsService(db)
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
        logger.debug(f"Found {result['total']} console_configss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid console_configs query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying console_configss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Console_configsListResponse)
async def query_console_configss_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query console_configss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying console_configss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Console_configsService(db)
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
        logger.debug(f"Found {result['total']} console_configss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid console_configs query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying console_configss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Console_configsResponse)
async def get_console_configs(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single console_configs by ID"""
    logger.debug(f"Fetching console_configs with id: {id}, fields={fields}")
    
    service = Console_configsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Console_configs with id {id} not found")
            raise HTTPException(status_code=404, detail="Console_configs not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching console_configs {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Console_configsResponse, status_code=201)
async def create_console_configs(
    data: Console_configsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new console_configs"""
    logger.debug(f"Creating new console_configs with data: {data}")
    
    service = Console_configsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create console_configs")
        
        logger.info(f"Console_configs created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating console_configs: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating console_configs: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Console_configsResponse], status_code=201)
async def create_console_configss_batch(
    request: Console_configsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple console_configss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} console_configss")
    
    service = Console_configsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} console_configss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Console_configsResponse])
async def update_console_configss_batch(
    request: Console_configsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple console_configss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} console_configss")
    
    service = Console_configsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} console_configss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Console_configsResponse)
async def update_console_configs(
    id: int,
    data: Console_configsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing console_configs"""
    logger.debug(f"Updating console_configs {id} with data: {data}")

    service = Console_configsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Console_configs with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Console_configs not found")
        
        logger.info(f"Console_configs {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating console_configs {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating console_configs {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_console_configss_batch(
    request: Console_configsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple console_configss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} console_configss")
    
    service = Console_configsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} console_configss successfully")
        return {"message": f"Successfully deleted {deleted_count} console_configss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_console_configs(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single console_configs by ID"""
    logger.debug(f"Deleting console_configs with id: {id}")
    
    service = Console_configsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Console_configs with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Console_configs not found")
        
        logger.info(f"Console_configs {id} deleted successfully")
        return {"message": "Console_configs deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting console_configs {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")