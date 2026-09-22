import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

log = logging.getLogger("nexvora")


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _body(request: Request, code: str, message: str, details=None) -> dict:  # noqa: ANN001
    b = {"error": {"code": code, "message": message,
                   "correlation_id": getattr(request.state, "correlation_id", None)}}
    if details is not None:
        b["error"]["details"] = details
    return b


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(_body(request, exc.code, exc.message), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(_body(request, "VALIDATION_ERROR", "Input tidak valid", details), status_code=422)

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError):
        # Detail SQL tidak dikirim ke klien, hanya ke log server
        log.warning("integrity error cid=%s: %s", getattr(request.state, "correlation_id", None),
                    str(getattr(exc, "orig", exc)).splitlines()[0])
        return JSONResponse(_body(request, "CONFLICT", "Data bentrok dengan data yang sudah ada"), status_code=409)

    @app.exception_handler(StaleDataError)
    async def _stale(request: Request, exc: StaleDataError):
        return JSONResponse(_body(request, "CONCURRENT_UPDATE",
                                  "Data baru saja diubah oleh proses lain. Muat ulang lalu coba lagi."), status_code=409)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error cid=%s", getattr(request.state, "correlation_id", None))
        return JSONResponse(_body(request, "INTERNAL_ERROR", "Terjadi kesalahan pada server"), status_code=500)
