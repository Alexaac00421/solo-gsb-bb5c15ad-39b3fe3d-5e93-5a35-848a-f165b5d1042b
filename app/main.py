"""HTTP API 层。"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from . import service
from .config import get_db_init_max_attempts
from .db import Base, engine, get_session
from .schemas import (
    AdjudicateRequest,
    AdjudicationResponse,
    PublishRequest,
    PublishResponse,
    RulesetDetail,
    RulesetSummary,
)


def _init_db_with_retry() -> None:
    """启动时建表；数据库尚未就绪时重试等待。"""
    attempts = get_db_init_max_attempts()
    for attempt in range(1, attempts + 1):
        try:
            Base.metadata.create_all(engine)
            return
        except OperationalError:
            if attempt == attempts:
                raise
            time.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db_with_retry()
    yield


app = FastAPI(
    title="政策裁决服务",
    version="1.0.0",
    description=(
        "以完整规则集为单位发布版本化政策规则，并在固定版本内进行裁决。"
        "规则支持地区/类别通配、半开有效时间段与整数优先级；"
        "发布时自动检测同业务键同优先级下结论不一致且可同时命中的冲突规则。"
    ),
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """字段缺失/非法时返回明确的中文错误与字段定位。"""
    return JSONResponse(
        status_code=422,
        content={
            "detail": "请求参数校验失败（字段缺失或取值非法）",
            "errors": jsonable_encoder(exc.errors()),
        },
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/rulesets",
    status_code=status.HTTP_201_CREATED,
    response_model=PublishResponse,
    summary="发布完整规则集，返回新版本号",
)
def publish_ruleset(req: PublishRequest, session: Session = Depends(get_session)):
    try:
        version = service.publish_ruleset(session, req.rules)
    except service.ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "规则集存在冲突，本次发布已整体失败，未产生新版本",
                "conflicts": exc.conflicts,
            },
        )
    return {"version": version}


@app.get(
    "/rulesets",
    response_model=List[RulesetSummary],
    summary="列出全部已发布版本",
)
def list_rulesets(session: Session = Depends(get_session)):
    return service.list_rulesets(session)


@app.get(
    "/rulesets/{version}",
    response_model=RulesetDetail,
    summary="查看指定版本的完整规则集",
)
def get_ruleset(version: int, session: Session = Depends(get_session)):
    try:
        return service.get_ruleset(session, version)
    except service.VersionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"规则集版本 {exc.version} 不存在",
        )


@app.post(
    "/adjudications",
    response_model=AdjudicationResponse,
    summary="裁决：在固定版本内取最高优先级结论",
)
def adjudicate(req: AdjudicateRequest, session: Session = Depends(get_session)):
    try:
        return service.adjudicate(session, req)
    except service.VersionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"规则集版本 {exc.version} 不存在",
        )
    except service.NoRulesetPublishedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="尚未发布任何规则集，无法裁决",
        )
