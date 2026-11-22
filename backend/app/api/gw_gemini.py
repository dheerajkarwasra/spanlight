"""Native Gemini routes using the same key, admission and execution paths as other surfaces."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from app.api.gw import (
    GatewayKey,
    ModelsKey,
    forwardable_headers,
    get_runtime,
    serve_model_call,
    trailing_slash,
)
from app.api.gw_errors import request_id_of
from app.core.observability import GATEWAY_REQUESTS
from app.gateway.models_endpoint import answer_models

router = APIRouter(prefix="/gw/v1beta", tags=["gateway"])
_NATIVE_BODY_DOC = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": {"type": "object", "additionalProperties": True}}
        },
    }
}


@router.post("/models/{model}:generateContent", openapi_extra=_NATIVE_BODY_DOC)
async def generate(request: Request, model: str, key: GatewayKey) -> Response:
    """Generate content on the Gemini route using the model or alias in the URL."""
    return await serve_model_call(request, "generate_content", key, path_model=model)


@router.post("/models/{model}:streamGenerateContent", openapi_extra=_NATIVE_BODY_DOC)
async def stream_generate(request: Request, model: str, key: GatewayKey) -> Response:
    """Stream Gemini content as server-sent events."""
    return await serve_model_call(request, "generate_content", key, path_model=model, stream=True)


@router.get("/models")
async def models(
    request: Request,
    key: ModelsKey,
    page_size: Annotated[int | None, Query(alias="pageSize", ge=1, le=1000)] = None,
    page_token: Annotated[
        str | None, Query(alias="pageToken", min_length=1, max_length=4096)
    ] = None,
) -> Response:
    """List the Gemini models visible to the gateway key's route."""
    runtime = get_runtime(request)
    pagination = {
        k: v for k, v in {"pageSize": page_size, "pageToken": page_token}.items() if v is not None
    }
    answer = await answer_models(
        key,
        "google",
        http=runtime.http,
        settings=runtime.settings,
        client_headers=forwardable_headers(request.headers),
        request_id=request_id_of(request),
        pagination=pagination,
    )
    GATEWAY_REQUESTS.labels(
        "gemini_models", "upstream_error" if answer.upstream_error else "ok"
    ).inc()
    return Response(answer.body, status_code=answer.status, headers=answer.headers)


router.add_api_route(
    "/{path:path}/",
    trailing_slash,
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
