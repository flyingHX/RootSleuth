import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.rule_versions import Rule_versionsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/rule_versions", tags=["rule_versions"])


# ---------- Pydantic Schemas ----------
class Rule_versionsData(BaseModel):
    """Entity data schema (for create/update)"""
    change_note: str = None
    content: str
    created_by: str = None
    status: str = None
    version: int


class Rule_versionsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    change_note: Optional[str] = None
    content: Optional[str] = None
    created_by: Optional[str] = None
    status: Optional[str] = None
    version: Optional[int] = None


class Rule_versionsResponse(BaseModel):
    """Entity response schema"""
    id: int
    change_note: Optional[str] = None
    content: str
    created_by: Optional[str] = None
    status: Optional[str] = None
    version: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Rule_versionsListResponse(BaseModel):
    """List response schema"""
    items: List[Rule_versionsResponse]
    total: int
    skip: int
    limit: int


class Rule_versionsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Rule_versionsData]


class Rule_versionsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Rule_versionsUpdateData


class Rule_versionsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Rule_versionsBatchUpdateItem]


class Rule_versionsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Rule_versionsListResponse)
async def query_rule_versionss(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query rule_versionss with filtering, sorting, and pagination"""
    logger.debug(f"Querying rule_versionss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Rule_versionsService(db)
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
        logger.debug(f"Found {result['total']} rule_versionss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid rule_versions query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying rule_versionss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Rule_versionsListResponse)
async def query_rule_versionss_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query rule_versionss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying rule_versionss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Rule_versionsService(db)
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
        logger.debug(f"Found {result['total']} rule_versionss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid rule_versions query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying rule_versionss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Rule_versionsResponse)
async def get_rule_versions(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single rule_versions by ID"""
    logger.debug(f"Fetching rule_versions with id: {id}, fields={fields}")
    
    service = Rule_versionsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Rule_versions with id {id} not found")
            raise HTTPException(status_code=404, detail="Rule_versions not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching rule_versions {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Rule_versionsResponse, status_code=201)
async def create_rule_versions(
    data: Rule_versionsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new rule_versions"""
    logger.debug(f"Creating new rule_versions with data: {data}")
    
    service = Rule_versionsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create rule_versions")
        
        logger.info(f"Rule_versions created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating rule_versions: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating rule_versions: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Rule_versionsResponse], status_code=201)
async def create_rule_versionss_batch(
    request: Rule_versionsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple rule_versionss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} rule_versionss")
    
    service = Rule_versionsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} rule_versionss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Rule_versionsResponse])
async def update_rule_versionss_batch(
    request: Rule_versionsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple rule_versionss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} rule_versionss")
    
    service = Rule_versionsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} rule_versionss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Rule_versionsResponse)
async def update_rule_versions(
    id: int,
    data: Rule_versionsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing rule_versions"""
    logger.debug(f"Updating rule_versions {id} with data: {data}")

    service = Rule_versionsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Rule_versions with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Rule_versions not found")
        
        logger.info(f"Rule_versions {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating rule_versions {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating rule_versions {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_rule_versionss_batch(
    request: Rule_versionsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple rule_versionss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} rule_versionss")
    
    service = Rule_versionsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} rule_versionss successfully")
        return {"message": f"Successfully deleted {deleted_count} rule_versionss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_rule_versions(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single rule_versions by ID"""
    logger.debug(f"Deleting rule_versions with id: {id}")
    
    service = Rule_versionsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Rule_versions with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Rule_versions not found")
        
        logger.info(f"Rule_versions {id} deleted successfully")
        return {"message": "Rule_versions deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting rule_versions {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")