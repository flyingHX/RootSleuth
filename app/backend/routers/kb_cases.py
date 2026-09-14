import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.kb_cases import Kb_casesService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/kb_cases", tags=["kb_cases"])


# ---------- Pydantic Schemas ----------
class Kb_casesData(BaseModel):
    """Entity data schema (for create/update)"""
    alert_template: str = None
    case_id: str
    cluster: str = None
    error_type: str
    feedback_score: float = None
    fingerprint: str = None
    root_cause: str = None
    service_name: str
    solution: str = None
    status: str = None
    topology_snapshot: str = None
    version: int = None


class Kb_casesUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    alert_template: Optional[str] = None
    case_id: Optional[str] = None
    cluster: Optional[str] = None
    error_type: Optional[str] = None
    feedback_score: Optional[float] = None
    fingerprint: Optional[str] = None
    root_cause: Optional[str] = None
    service_name: Optional[str] = None
    solution: Optional[str] = None
    status: Optional[str] = None
    topology_snapshot: Optional[str] = None
    version: Optional[int] = None


class Kb_casesResponse(BaseModel):
    """Entity response schema"""
    id: int
    alert_template: Optional[str] = None
    case_id: str
    cluster: Optional[str] = None
    error_type: str
    feedback_score: Optional[float] = None
    fingerprint: Optional[str] = None
    root_cause: Optional[str] = None
    service_name: str
    solution: Optional[str] = None
    status: Optional[str] = None
    topology_snapshot: Optional[str] = None
    version: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Kb_casesListResponse(BaseModel):
    """List response schema"""
    items: List[Kb_casesResponse]
    total: int
    skip: int
    limit: int


class Kb_casesBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Kb_casesData]


class Kb_casesBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Kb_casesUpdateData


class Kb_casesBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Kb_casesBatchUpdateItem]


class Kb_casesBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Kb_casesListResponse)
async def query_kb_casess(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query kb_casess with filtering, sorting, and pagination"""
    logger.debug(f"Querying kb_casess: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Kb_casesService(db)
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
        logger.debug(f"Found {result['total']} kb_casess")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid kb_cases query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying kb_casess: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Kb_casesListResponse)
async def query_kb_casess_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query kb_casess with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying kb_casess: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Kb_casesService(db)
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
        logger.debug(f"Found {result['total']} kb_casess")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid kb_cases query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying kb_casess: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Kb_casesResponse)
async def get_kb_cases(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single kb_cases by ID"""
    logger.debug(f"Fetching kb_cases with id: {id}, fields={fields}")
    
    service = Kb_casesService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Kb_cases with id {id} not found")
            raise HTTPException(status_code=404, detail="Kb_cases not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching kb_cases {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Kb_casesResponse, status_code=201)
async def create_kb_cases(
    data: Kb_casesData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new kb_cases"""
    logger.debug(f"Creating new kb_cases with data: {data}")
    
    service = Kb_casesService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create kb_cases")
        
        logger.info(f"Kb_cases created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating kb_cases: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating kb_cases: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Kb_casesResponse], status_code=201)
async def create_kb_casess_batch(
    request: Kb_casesBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple kb_casess in a single request"""
    logger.debug(f"Batch creating {len(request.items)} kb_casess")
    
    service = Kb_casesService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} kb_casess successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Kb_casesResponse])
async def update_kb_casess_batch(
    request: Kb_casesBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple kb_casess in a single request"""
    logger.debug(f"Batch updating {len(request.items)} kb_casess")
    
    service = Kb_casesService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} kb_casess successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Kb_casesResponse)
async def update_kb_cases(
    id: int,
    data: Kb_casesUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing kb_cases"""
    logger.debug(f"Updating kb_cases {id} with data: {data}")

    service = Kb_casesService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Kb_cases with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Kb_cases not found")
        
        logger.info(f"Kb_cases {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating kb_cases {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating kb_cases {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_kb_casess_batch(
    request: Kb_casesBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple kb_casess by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} kb_casess")
    
    service = Kb_casesService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} kb_casess successfully")
        return {"message": f"Successfully deleted {deleted_count} kb_casess", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_kb_cases(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single kb_cases by ID"""
    logger.debug(f"Deleting kb_cases with id: {id}")
    
    service = Kb_casesService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Kb_cases with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Kb_cases not found")
        
        logger.info(f"Kb_cases {id} deleted successfully")
        return {"message": "Kb_cases deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting kb_cases {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")