from fastapi import APIRouter, HTTPException, Request, Response

from tracerisk.assistant.model import AssistantModelError
from tracerisk.assistant.schemas import AssistantAnswerRequest, AssistantAnswerResponse
from tracerisk.assistant.service import (
    AssistantContextChanged,
    AssistantInvestigationNotFound,
    answer_question,
)
from tracerisk.auth.router import Session
from tracerisk.investigations.router import InvestigationReader

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post(
    "/answers",
    response_model=AssistantAnswerResponse,
    responses={
        409: {"description": "Current Run is unready or its context has changed"},
        502: {"description": "Model call or selection validation failed"},
        504: {"description": "Model call timed out"},
    },
)
async def answer(
    payload: AssistantAnswerRequest,
    request: Request,
    response: Response,
    _user: InvestigationReader,
    session: Session,
) -> AssistantAnswerResponse:
    # The role check shares this request dependency. Release its connection too.
    await session.close()
    try:
        result = await answer_question(
            request.app.state.session_factory, request.app.state.settings, payload
        )
    except AssistantInvestigationNotFound:
        raise HTTPException(status_code=404, detail="Investigation not found") from None
    except AssistantContextChanged:
        raise HTTPException(
            status_code=409,
            detail="当前运行尚未就绪或调查上下文已更新，请刷新后重新提问。",
        ) from None
    except AssistantModelError as error:
        if error.category == "timeout":
            raise HTTPException(
                status_code=504, detail="问答模型调用超时，请重试。"
            ) from None
        raise HTTPException(
            status_code=502, detail="问答模型调用或引用校验失败，请重试。"
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return result
