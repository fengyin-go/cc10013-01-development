"""端点接入清单接口。

两组资源：
- /api/admission/collect-endpoints 采集端点配置（批量准入的多选输入）
- /api/admission/records          端点接入清单数据集（准入回执与隔离原因同表可见）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ActionResult,
    BatchAdmitPayload,
    ConflictResolvePayload,
    PageResult,
)
from app.services.admission import AdmissionService

router = APIRouter(prefix="/api/admission", tags=["端点接入"])

service = AdmissionService()

COLLECT_FIELDS = [
    "端点编号", "接口地址", "请求方法", "认证模板", "认证类型",
    "认证请求头", "认证密钥", "迁移批次", "处理状态",
]
ADMISSION_FIELDS = [
    "端点编号", "接口地址", "规范化接口地址", "请求方法", "迁移批次",
    "认证模板", "认证类型", "认证请求头", "认证密钥",
    "准入状态", "隔离原因", "准入回执", "处理时间",
]


def _check_size(size: int) -> None:
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")


# ---------------- 采集端点配置 ----------------
@router.get("/collect-endpoints", response_model=PageResult[dict])
def list_collect_endpoints(
    keyword: str | None = Query(default=None, description="按端点编号检索"),
    status: str | None = Query(default=None, description="未处理、已处理、已跳过"),
    batch: str | None = Query(default=None, description="按迁移批次过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """列出可多选的采集端点配置，支持端点编号、处理状态、迁移批次过滤。"""
    _check_size(size)
    items, total = service.list_collect_endpoints(
        keyword=keyword, status=status, batch=batch, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/collect-endpoints/{entry_id}", response_model=dict)
def get_collect_endpoint(entry_id: int) -> dict:
    """读取单条采集端点配置明细。"""
    entry = service.get_collect_endpoint(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"采集端点配置 {entry_id} 不存在")
    return entry


# ---------------- 批量准入动作 ----------------
@router.post("/batch-admit")
def batch_admit(payload: BatchAdmitPayload) -> dict[str, Any]:
    """对多选采集端点执行统一准入，生成端点接入清单数据集并回写接口管理待办区。"""
    return service.batch_admit(payload.source_ids)


# ---------------- 端点接入清单数据集 ----------------
@router.get("/records", response_model=PageResult[dict])
def list_admissions(
    keyword: str | None = Query(default=None, description="按端点编号检索，如 EP-201"),
    status: str | None = Query(default=None, description="已接入、待裁决、已隔离"),
    batch: str | None = Query(default=None, description="按迁移批次过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """接入清单：准入回执和隔离原因随每条记录一起返回。"""
    _check_size(size)
    items, total = service.list_admissions(
        keyword=keyword, status=status, batch=batch, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/records/{entry_id}", response_model=dict)
def get_admission(entry_id: int) -> dict:
    """读取单条准入记录（含认证参数快照、候选认证模板与隔离原因）。"""
    entry = service.get_admission(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"准入记录 {entry_id} 不存在")
    return entry


@router.post("/records/{entry_id}/resolve", response_model=ActionResult)
def resolve_conflict(entry_id: int, payload: ConflictResolvePayload) -> ActionResult:
    """对「待裁决」记录做冲突裁决：采纳模板接入或退回隔离。"""
    entry, message = service.resolve_conflict(
        entry_id, payload.decision.strip(), payload.chosen_template
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_admissions(
    status: str | None = None,
    batch: str | None = None,
) -> dict[str, Any]:
    """导出端点接入清单全量数据，便于运营人员按端点编号核对。"""
    items, total = service.list_admissions(status=status, batch=batch, page=1, size=10000)
    return {"module": "admission", "total": total, "items": items}
