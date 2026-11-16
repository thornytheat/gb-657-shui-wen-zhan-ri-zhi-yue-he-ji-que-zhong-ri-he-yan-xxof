"""业务异常 → HTTP 映射。业务代码只抛这些，不直接构造 HTTPException。"""
from __future__ import annotations


class MeterbackError(Exception):
    status_code = 400
    code = "E_UNKNOWN"

    def __init__(self, message: str, *, code: str | None = None,
                 detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        if code:
            self.code = code

    def payload(self) -> dict:
        return {"error": self.code, "message": self.message, "detail": self.detail}


class BadRequest(MeterbackError):
    status_code = 400
    code = "E_BAD_REQUEST"


class NotFound(MeterbackError):
    status_code = 404
    code = "E_NOT_FOUND"


class Conflict(MeterbackError):
    status_code = 409
    code = "E_CONFLICT"


class UnprocessableInput(MeterbackError):
    """文件级失败：空文件 / 仅表头 / 全空行 / 表头缺列，各带不同原因码。"""

    status_code = 422
    code = "E_INPUT"


class ContractInvalid(MeterbackError):
    status_code = 422
    code = "E_CONTRACT_INVALID"


def install_handlers(app) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(MeterbackError)
    async def _hydrocalendar(request: Request, exc: MeterbackError):  # noqa: ANN001
        return JSONResponse(status_code=exc.status_code, content=exc.payload())
