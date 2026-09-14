import json
import logging
from typing import List, Optional

from datetime import datetime, date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.oncall_reports import Oncall_reportsService

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entities/oncall_reports", tags=["oncall_reports"])


# ---------- Pydantic Schemas ----------
class Oncall_reportsData(BaseModel):
    """Entity data schema (for create/update)"""
    time_window: str
    event_count: int = None
    critical_count: int = None
    warning_count: int = None
    info_count: int = None
    affected_systems: str = None
    report_json: str = None
    chatops_text: str = None
    session_id: int = None
    actor: str = None


class Oncall_reportsUpdateData(BaseModel):
    """Update entity data (partial updates allowed)"""
    time_window: Optional[str] = None
    event_count: Optional[int] = None
    critical_count: Optional[int] = None
    warning_count: Optional[int] = None
    info_count: Optional[int] = None
    affected_systems: Optional[str] = None
    report_json: Optional[str] = None
    chatops_text: Optional[str] = None
    session_id: Optional[int] = None
    actor: Optional[str] = None


class Oncall_reportsResponse(BaseModel):
    """Entity response schema"""
    id: int
    time_window: str
    event_count: Optional[int] = None
    critical_count: Optional[int] = None
    warning_count: Optional[int] = None
    info_count: Optional[int] = None
    affected_systems: Optional[str] = None
    report_json: Optional[str] = None
    chatops_text: Optional[str] = None
    session_id: Optional[int] = None
    actor: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Oncall_reportsListResponse(BaseModel):
    """List response schema"""
    items: List[Oncall_reportsResponse]
    total: int
    skip: int
    limit: int


class Oncall_reportsBatchCreateRequest(BaseModel):
    """Batch create request"""
    items: List[Oncall_reportsData]


class Oncall_reportsBatchUpdateItem(BaseModel):
    """Batch update item"""
    id: int
    updates: Oncall_reportsUpdateData


class Oncall_reportsBatchUpdateRequest(BaseModel):
    """Batch update request"""
    items: List[Oncall_reportsBatchUpdateItem]


class Oncall_reportsBatchDeleteRequest(BaseModel):
    """Batch delete request"""
    ids: List[int]


# ---------- Routes ----------
@router.get("", response_model=Oncall_reportsListResponse)
async def query_oncall_reportss(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Query oncall_reportss with filtering, sorting, and pagination"""
    logger.debug(f"Querying oncall_reportss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")
    
    service = Oncall_reportsService(db)
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
        logger.debug(f"Found {result['total']} oncall_reportss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid oncall_reports query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying oncall_reportss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/all", response_model=Oncall_reportsListResponse)
async def query_oncall_reportss_all(
    query: str = Query(None, description='Query conditions as JSON, e.g. {"id":2} or {"id":{"$gte":2}}'),
    sort: str = Query(None, description="Sort field (prefix with '-' for descending)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=2000, description="Max number of records to return"),
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    # Query oncall_reportss with filtering, sorting, and pagination without user limitation
    logger.debug(f"Querying oncall_reportss: query={query}, sort={sort}, skip={skip}, limit={limit}, fields={fields}")

    service = Oncall_reportsService(db)
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
        logger.debug(f"Found {result['total']} oncall_reportss")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Invalid oncall_reports query: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error querying oncall_reportss: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/{id}", response_model=Oncall_reportsResponse)
async def get_oncall_reports(
    id: int,
    fields: str = Query(None, description="Comma-separated list of fields to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get a single oncall_reports by ID"""
    logger.debug(f"Fetching oncall_reports with id: {id}, fields={fields}")
    
    service = Oncall_reportsService(db)
    try:
        result = await service.get_by_id(id)
        if not result:
            logger.warning(f"Oncall_reports with id {id} not found")
            raise HTTPException(status_code=404, detail="Oncall_reports not found")
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching oncall_reports {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("", response_model=Oncall_reportsResponse, status_code=201)
async def create_oncall_reports(
    data: Oncall_reportsData,
    db: AsyncSession = Depends(get_db),
):
    """Create a new oncall_reports"""
    logger.debug(f"Creating new oncall_reports with data: {data}")
    
    service = Oncall_reportsService(db)
    try:
        result = await service.create(data.model_dump())
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create oncall_reports")
        
        logger.info(f"Oncall_reports created successfully with id: {result.id}")
        return result
    except ValueError as e:
        logger.error(f"Validation error creating oncall_reports: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating oncall_reports: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/batch", response_model=List[Oncall_reportsResponse], status_code=201)
async def create_oncall_reportss_batch(
    request: Oncall_reportsBatchCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create multiple oncall_reportss in a single request"""
    logger.debug(f"Batch creating {len(request.items)} oncall_reportss")
    
    service = Oncall_reportsService(db)
    results = []
    
    try:
        for item_data in request.items:
            result = await service.create(item_data.model_dump())
            if result:
                results.append(result)
        
        logger.info(f"Batch created {len(results)} oncall_reportss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch create: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch create failed: {str(e)}")


@router.put("/batch", response_model=List[Oncall_reportsResponse])
async def update_oncall_reportss_batch(
    request: Oncall_reportsBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update multiple oncall_reportss in a single request"""
    logger.debug(f"Batch updating {len(request.items)} oncall_reportss")
    
    service = Oncall_reportsService(db)
    results = []
    
    try:
        for item in request.items:
            # Only include non-None values for partial updates
            update_dict = {k: v for k, v in item.updates.model_dump().items() if v is not None}
            result = await service.update(item.id, update_dict)
            if result:
                results.append(result)
        
        logger.info(f"Batch updated {len(results)} oncall_reportss successfully")
        return results
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch update failed: {str(e)}")


@router.put("/{id}", response_model=Oncall_reportsResponse)
async def update_oncall_reports(
    id: int,
    data: Oncall_reportsUpdateData,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing oncall_reports"""
    logger.debug(f"Updating oncall_reports {id} with data: {data}")

    service = Oncall_reportsService(db)
    try:
        # Only include non-None values for partial updates
        update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
        result = await service.update(id, update_dict)
        if not result:
            logger.warning(f"Oncall_reports with id {id} not found for update")
            raise HTTPException(status_code=404, detail="Oncall_reports not found")
        
        logger.info(f"Oncall_reports {id} updated successfully")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Validation error updating oncall_reports {id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating oncall_reports {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/batch")
async def delete_oncall_reportss_batch(
    request: Oncall_reportsBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple oncall_reportss by their IDs"""
    logger.debug(f"Batch deleting {len(request.ids)} oncall_reportss")
    
    service = Oncall_reportsService(db)
    deleted_count = 0
    
    try:
        for item_id in request.ids:
            success = await service.delete(item_id)
            if success:
                deleted_count += 1
        
        logger.info(f"Batch deleted {deleted_count} oncall_reportss successfully")
        return {"message": f"Successfully deleted {deleted_count} oncall_reportss", "deleted_count": deleted_count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in batch delete: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch delete failed: {str(e)}")


@router.delete("/{id}")
async def delete_oncall_reports(
    id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single oncall_reports by ID"""
    logger.debug(f"Deleting oncall_reports with id: {id}")
    
    service = Oncall_reportsService(db)
    try:
        success = await service.delete(id)
        if not success:
            logger.warning(f"Oncall_reports with id {id} not found for deletion")
            raise HTTPException(status_code=404, detail="Oncall_reports not found")
        
        logger.info(f"Oncall_reports {id} deleted successfully")
        return {"message": "Oncall_reports deleted successfully", "id": id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting oncall_reports {id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")