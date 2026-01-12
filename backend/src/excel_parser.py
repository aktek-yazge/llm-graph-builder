"""
Excel Parser Module

Generic Excel parser for maintenance/CMMS data.
Supports WAT Motor maintenance Excel format.

Sütun Mapping:
- TaskId -> task_id
- Durum -> status
- Talep oluşturan -> requester_name
- Ekipman ismi -> equipment_name
- Oluşturma zamanı -> created_at
- Atama zamanı -> assigned_at
- Başlama zamanı -> started_at
- Tamamlama zamanı -> completed_at
- Kapama zamanı -> closed_at
- Toplam durdurma(dk) -> total_downtime_min
- Atanan teknisyen ismi -> technician_name
- Duruş etkisi -> impact_type
- Toplam zaman(dk) -> total_time_min
- Maliyet merkezi -> cost_center
- Talep notu -> request_note
- Bakımcı notu -> technician_note
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# =============================================================================
# COLUMN MAPPING
# =============================================================================

# Turkish column names -> English field names
COLUMN_MAPPING = {
    "TaskId": "task_id",
    "Durum": "status",
    "Talep oluşturan": "requester_name",
    "Ekipman ismi": "equipment_name",
    "Oluşturma zamanı": "created_at",
    "Atama zamanı": "assigned_at",
    "Başlama zamanı": "started_at",
    "Tamamlama zamanı": "completed_at",
    "Kapama zamanı": "closed_at",
    "Toplam durdurma(dk)": "total_downtime_min",
    "Atanan teknisyen ismi": "technician_name",
    "Duruş etkisi": "impact_type",
    "Toplam zaman(dk)": "total_time_min",
    "Maliyet merkezi": "cost_center",
    "Talep notu": "request_note",
    "Bakımcı notu": "technician_note",
}

# Required columns (must exist in Excel)
REQUIRED_COLUMNS = ["TaskId", "Ekipman ismi"]


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class MaintenanceTask(BaseModel):
    """Maintenance Task data model"""
    
    task_id: str = Field(..., description="Unique task identifier")
    status: Optional[str] = Field(None, description="Task status (Tamamlandı, Beklemede, etc.)")
    requester_name: Optional[str] = Field(None, description="Person who created the request")
    equipment_name: str = Field(..., description="Equipment name")
    created_at: Optional[datetime] = Field(None, description="Request creation time")
    assigned_at: Optional[datetime] = Field(None, description="Assignment time")
    started_at: Optional[datetime] = Field(None, description="Work start time")
    completed_at: Optional[datetime] = Field(None, description="Work completion time")
    closed_at: Optional[datetime] = Field(None, description="Task close time")
    total_downtime_min: Optional[float] = Field(None, description="Total downtime in minutes")
    technician_name: Optional[str] = Field(None, description="Assigned technician name")
    impact_type: Optional[str] = Field(None, description="Downtime impact type (Hat Duruşu, etc.)")
    total_time_min: Optional[float] = Field(None, description="Total time in minutes")
    cost_center: Optional[str] = Field(None, description="Cost center / department")
    request_note: Optional[str] = Field(None, description="Problem description")
    technician_note: Optional[str] = Field(None, description="Work done description")
    
    @field_validator("task_id", mode="before")
    @classmethod
    def convert_task_id(cls, v):
        """Convert task_id to string"""
        if v is None:
            raise ValueError("task_id cannot be None")
        return str(v).strip()
    
    @field_validator("status", "requester_name", "equipment_name", "technician_name", 
                    "impact_type", "cost_center", "request_note", "technician_note", mode="before")
    @classmethod
    def clean_string(cls, v):
        """Clean string fields"""
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        s = str(v).strip()
        return s if s and s != "-" else None
    
    @field_validator("total_downtime_min", "total_time_min", mode="before")
    @classmethod
    def clean_numeric(cls, v):
        """Clean numeric fields"""
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        if isinstance(v, str):
            v = v.strip()
            if v == "-" or v == "":
                return None
            try:
                return float(v.replace(",", "."))
            except ValueError:
                return None
        return float(v) if v else None
    
    @field_validator("created_at", "assigned_at", "started_at", "completed_at", "closed_at", mode="before")
    @classmethod
    def parse_datetime(cls, v):
        """Parse datetime fields"""
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            v = v.strip()
            if v == "-" or v == "" or v.startswith("1900-01"):
                return None
            try:
                # Try common formats
                for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                    try:
                        return datetime.strptime(v.split(".")[0] if "ffffff" in v else v, fmt)
                    except ValueError:
                        continue
                return None
            except Exception:
                return None
        return None
    
    def get_embedding_text(self) -> str:
        """Generate text for embedding from notes"""
        parts = []
        if self.request_note:
            parts.append(f"Arıza: {self.request_note}")
        if self.technician_note:
            parts.append(f"Çözüm: {self.technician_note}")
        return ". ".join(parts) if parts else ""


class ParseResult(BaseModel):
    """Result of Excel parsing"""
    
    tasks: List[MaintenanceTask] = Field(default_factory=list)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# PARSER FUNCTIONS
# =============================================================================

def parse_maintenance_excel(
    file_path: str,
    sheet_name: str | int = 0,
    skip_rows: int = 0,
) -> ParseResult:
    """
    Parse WAT Motor maintenance Excel file.
    
    Args:
        file_path: Path to Excel file
        sheet_name: Sheet name or index (default: first sheet)
        skip_rows: Number of rows to skip at the beginning
        
    Returns:
        ParseResult with tasks, errors, and stats
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {file_path}")
    
    logger.info(f"📂 Reading Excel file: {file_path}")
    
    # Read Excel
    try:
        df = pd.read_excel(
            file_path,
            sheet_name=sheet_name,
            skiprows=skip_rows,
            engine="openpyxl"
        )
    except Exception as e:
        raise ValueError(f"Failed to read Excel file: {e}")
    
    logger.info(f"📊 Found {len(df)} rows, {len(df.columns)} columns")
    logger.info(f"📋 Columns: {list(df.columns)}")
    
    # Validate required columns
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns: {missing_cols}")
    
    # Rename columns
    df = df.rename(columns=COLUMN_MAPPING)
    
    # Parse rows
    tasks: List[MaintenanceTask] = []
    errors: List[Dict[str, Any]] = []
    
    for idx, row in df.iterrows():
        try:
            task = MaintenanceTask(**row.to_dict())
            tasks.append(task)
        except Exception as e:
            errors.append({
                "row": idx + 2 + skip_rows,  # Excel row number (1-indexed + header)
                "task_id": row.get("task_id", "unknown"),
                "error": str(e)
            })
    
    # Calculate stats
    stats = _calculate_stats(tasks, df)
    
    result = ParseResult(tasks=tasks, errors=errors, stats=stats)
    
    logger.info(f"✅ Parsed {len(tasks)} tasks, {len(errors)} errors")
    
    return result


def _calculate_stats(tasks: List[MaintenanceTask], df: pd.DataFrame) -> Dict[str, Any]:
    """Calculate parsing statistics"""
    
    # Unique values
    unique_equipment = set(t.equipment_name for t in tasks if t.equipment_name)
    unique_technicians = set(t.technician_name for t in tasks if t.technician_name)
    unique_requesters = set(t.requester_name for t in tasks if t.requester_name)
    unique_cost_centers = set(t.cost_center for t in tasks if t.cost_center)
    unique_statuses = set(t.status for t in tasks if t.status)
    unique_impacts = set(t.impact_type for t in tasks if t.impact_type)
    
    # Tasks with notes
    tasks_with_request_note = sum(1 for t in tasks if t.request_note)
    tasks_with_technician_note = sum(1 for t in tasks if t.technician_note)
    tasks_with_any_note = sum(1 for t in tasks if t.request_note or t.technician_note)
    
    return {
        "total_rows": len(df),
        "parsed_tasks": len(tasks),
        "unique_equipment": len(unique_equipment),
        "unique_technicians": len(unique_technicians),
        "unique_requesters": len(unique_requesters),
        "unique_cost_centers": len(unique_cost_centers),
        "unique_statuses": len(unique_statuses),
        "unique_impacts": len(unique_impacts),
        "tasks_with_request_note": tasks_with_request_note,
        "tasks_with_technician_note": tasks_with_technician_note,
        "tasks_with_any_note": tasks_with_any_note,
        "equipment_list": sorted(unique_equipment),
        "technician_list": sorted(unique_technicians),
        "requester_list": sorted(unique_requesters),
        "cost_center_list": sorted(unique_cost_centers),
        "status_list": sorted(unique_statuses),
        "impact_list": sorted(unique_impacts),
    }


def print_parse_summary(result: ParseResult) -> None:
    """Print parsing summary to console"""
    
    print("\n" + "=" * 60)
    print("📊 EXCEL PARSE SUMMARY")
    print("=" * 60)
    
    stats = result.stats
    
    print(f"\n📁 Rows: {stats['total_rows']}")
    print(f"✅ Parsed Tasks: {stats['parsed_tasks']}")
    print(f"❌ Errors: {len(result.errors)}")
    
    print(f"\n📦 Unique Entities:")
    print(f"   Equipment: {stats['unique_equipment']}")
    print(f"   Technicians: {stats['unique_technicians']}")
    print(f"   Requesters: {stats['unique_requesters']}")
    print(f"   Cost Centers: {stats['unique_cost_centers']}")
    print(f"   Statuses: {stats['unique_statuses']}")
    print(f"   Impact Types: {stats['unique_impacts']}")
    
    print(f"\n📝 Notes:")
    print(f"   With Request Note: {stats['tasks_with_request_note']}")
    print(f"   With Technician Note: {stats['tasks_with_technician_note']}")
    print(f"   With Any Note (for embedding): {stats['tasks_with_any_note']}")
    
    if result.errors:
        print(f"\n⚠️ Errors (first 5):")
        for err in result.errors[:5]:
            print(f"   Row {err['row']}: {err['error'][:80]}")
    
    print("\n" + "=" * 60)


# =============================================================================
# MAIN (for testing)
# =============================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python excel_parser.py <excel_file>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    
    try:
        result = parse_maintenance_excel(file_path)
        print_parse_summary(result)
        
        # Print sample tasks
        if result.tasks:
            print("\n📋 Sample Tasks (first 3):")
            for task in result.tasks[:3]:
                print(f"\n   Task {task.task_id}:")
                print(f"   - Equipment: {task.equipment_name}")
                print(f"   - Status: {task.status}")
                print(f"   - Technician: {task.technician_name}")
                print(f"   - Request: {task.request_note[:50] if task.request_note else '-'}...")
                print(f"   - Embedding text: {task.get_embedding_text()[:80]}...")
                
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
