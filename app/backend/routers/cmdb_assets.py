import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.cmdb_assets import Cmdb_assetsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/cmdb_assets", tags=["cmdb_assets"])


# ---------- Pydantic Schemas ----------
class Cmdb_assetsData(BaseModel):
    """Entity data schema (for create/update)"""
    hostname: str
    ip: str
    system_name: str
    service_name: str
    cluster: str = None
    environment: str = None
    owner: str = None
    owner_email: str = None
    dependencies: str = None
    log_path: str = None
    status: str = None
    description: str = None


class Cmdb_assetsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    hostname: Optional[str] = None
    ip: Optional[str] = None
    system_name: Optional[str] = None
    service_name: Optional[str] = None
    cluster: Optional[str] = None
    environment: Optional[str] = None
    owner: Optional[str] = None
    owner_email: Optional[str] = None
    dependencies: Optional[str] = None
    log_path: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None


class Cmdb_assetsResponse(BaseModel):
    """Entity response schema"""
    id: int
    hostname: str
    ip: str
    system_name: str
    service_name: str
    cluster: Optional[str] = None
    environment: Optional[str] = None
    owner: Optional[str] = None
    owner_email: Optional[str] = None
    dependencies: Optional[str] = None
    log_path: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Cmdb_assetsListResponse(BaseModel):
    """List response schema"""
    items: List[Cmdb_assetsResponse]
    total: int
    skip: int
    limit: int


class Cmdb_assetsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Cmdb_assetsData]


class Cmdb_assetsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Cmdb_assetsUpdateData


class Cmdb_assetsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Cmdb_assetsBatchUpdateItem]


class Cmdb_assetsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Cmdb_assetsListResponse)
async def query_cmdb_assetss(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query cmdb_assetss with filtering, sorting, and pagination"""
    logger.debug(f"Querying cmdb_assetss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Cmdb_assetsService(db)
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
        logger.debug(f"Found {result['total']} cmdb_assetss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid cmdb_assets query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying cmdb_assetss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Cmdb_assetsListResponse)
async def query_cmdb_assetss_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query cmdb_assetss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying cmdb_assetss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Cmdb_assetsService(db)
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
        logger.debug(f"Found {result['total']} cmdb_assetss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid cmdb_assets query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying cmdb_assetss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Cmdb_assetsResponse)
async def get_cmdb_assets(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single cmdb_assets by ID"""
    logger.debug(f"Fetching cmdb_assets with id: {id}, fields={fields}")
    
    service = Cmdb_assetsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Cmdb_assets with id {id} not found")
            raise HTTPException(status_code=404, detail="Cmdb_assets not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching cmdb_assets {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Cmdb_assetsResponse, status_code=201)
async def create_cmdb_assets(
    data: Cmdb_assetsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new cmdb_assets"""
    logger.debug(f"Creating new cmdb_assets with data: {data}")
    
    service = Cmdb_assetsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create cmdb_assets")
        
        logger.info(f"Cmdb_assets created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating cmdb_assets: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating cmdb_assets: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Cmdb_assetsResponse], status_code=201)
async def create_cmdb_assetss_batch(
    request: Cmdb_assetsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple cmdb_assetss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} cmdb_assetss")
    
    service = Cmdb_assetsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} cmdb_assetss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Cmdb_assetsResponse])
async def update_cmdb_assetss_batch(
    request: Cmdb_assetsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple cmdb_assetss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} cmdb_assetss")
    
    service = Cmdb_assetsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} cmdb_assetss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Cmdb_assetsResponse)
async def update_cmdb_assets(
    id: int,
    data: Cmdb_assetsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing cmdb_assets"""
    logger.debug(f"Updating cmdb_assets {id} with data: {data}")

    service = Cmdb_assetsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Cmdb_assets with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Cmdb_assets not found")
        
        logger.info(f"Cmdb_assets {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating cmdb_assets {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating cmdb_assets {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_cmdb_assetss_batch(
    request: Cmdb_assetsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple cmdb_assetss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} cmdb_assetss")
    
    service = Cmdb_assetsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} cmdb_assetss successfully")
        return {"message": f"Successfully deleted {deleted_count} cmdb_assetss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_cmdb_assets(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single cmdb_assets by ID"""
    logger.debug(f"Deleting cmdb_assets with id: {id}")
    
    service = Cmdb_assetsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Cmdb_assets with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Cmdb_assets not found")
        
        logger.info(f"Cmdb_assets {id} deleted successfully")
        return {"message": "Cmdb_assets deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting cmdb_assets {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")