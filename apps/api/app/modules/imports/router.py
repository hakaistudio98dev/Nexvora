import json
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.context import Ctx
from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.entitlements import check_permission
from app.core.errors import AppError
from app.modules.imports import service
from app.modules.imports.service import SPECS

router = APIRouter(prefix="/imports", tags=["imports"])
MAX_BYTES = 5 * 1024 * 1024


class CommitIn(BaseModel):
    skip_errors: bool = False


@router.get("")
async def kinds(p: Principal = Depends(require("import:run"))):
    return [{"kind": s.kind, "label": s.label, "required": s.required, "optional": s.optional,
             "allowed": p.can(s.permission)} for s in SPECS.values()]


@router.get("/{kind}/template.csv")
async def template(kind: str, p: Principal = Depends(require("import:run"))):
    if kind not in SPECS:
        raise AppError(404, "NOT_FOUND", "Jenis impor tidak dikenal")
    return Response(service.template_csv(kind), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="contoh-impor-{kind}.csv"'})


@router.post("/{kind}/preview", status_code=201)
async def preview(kind: str, file: UploadFile = File(...), p: Principal = Depends(require("import:run")),
                  s: AsyncSession = Depends(get_session)):
    """Periksa file tanpa mengubah apa pun. Kembalikan ringkasan, contoh baris, dan daftar kesalahan."""
    spec = SPECS.get(kind)
    if spec is None:
        raise AppError(404, "NOT_FOUND", "Jenis impor tidak dikenal")
    if not p.can(spec.permission):
        raise AppError(403, "FORBIDDEN", f"Anda tidak punya izin untuk impor {spec.label.lower()}")
    raw = await file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise AppError(413, "FILE_TOO_LARGE", "Ukuran file maksimal 5 MB; bagi menjadi beberapa file")
    rows, errors = service.parse_csv(raw, spec)
    valid, more = await service.validate(s, p.tenant_id, kind, rows)
    errors = sorted(errors + more, key=lambda e: e["row"])
    job = (await s.execute(text("""
        INSERT INTO import_jobs(tenant_id, kind, filename, total_rows, valid_rows, rows, errors, created_by)
        VALUES (:t, :k, :f, :tot, :val, CAST(:r AS jsonb), CAST(:e AS jsonb), :u) RETURNING id, created_at"""),
        {"t": p.tenant_id, "k": kind, "f": (file.filename or "")[:200], "tot": len(rows) + len(errors),
         "val": len(valid), "r": json.dumps(valid), "e": json.dumps(errors[:200]), "u": p.user_id})).one()
    return {"id": str(job.id), "kind": kind, "label": spec.label, "status": "PREVIEW",
            "total_rows": len(rows) + len(errors), "valid_rows": len(valid), "error_rows": len(errors),
            "sample": valid[:10], "errors": errors[:50], "created_at": job.created_at}


@router.post("/{job_id}/commit")
async def commit(job_id: UUID, body: CommitIn, p: Principal = Depends(require("import:run")),
                 s: AsyncSession = Depends(get_session)):
    job = (await s.execute(text("SELECT * FROM import_jobs WHERE id = :i AND tenant_id = :t FOR UPDATE"),
                           {"i": job_id, "t": p.tenant_id})).first()
    if job is None:
        raise AppError(404, "NOT_FOUND", "Pekerjaan impor tidak ditemukan")
    if job.status == "COMMITTED":
        return {"id": str(job_id), "status": "COMMITTED", "result": job.result, "already": True}
    if job.status != "PREVIEW":
        raise AppError(409, "INVALID_STATE", f"Impor berstatus {job.status}")
    spec = SPECS[job.kind]
    if not p.can(spec.permission):
        raise AppError(403, "FORBIDDEN", "Anda tidak punya izin untuk impor ini")
    check_permission(p.entitlement, spec.permission)
    if job.errors and not body.skip_errors:
        raise AppError(409, "HAS_ERRORS", f"Masih ada {len(job.errors)} baris bermasalah. Perbaiki filenya, "
                                          "atau jalankan dengan pilihan “lewati baris bermasalah”.")
    result = await service.commit(s, Ctx.from_principal(p), job.kind, list(job.rows))
    await s.execute(text("""UPDATE import_jobs SET status = 'COMMITTED', committed_at = now(),
                            result = CAST(:r AS jsonb) WHERE id = :i"""), {"i": job_id, "r": json.dumps(result)})
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="import.committed",
                       entity_type="import", entity_id=job_id,
                       after={"kind": job.kind, "file": job.filename, "baris_valid": job.valid_rows,
                              "dilewati": len(job.errors), "hasil": result},
                       correlation_id=p.correlation_id, ip=p.ip)
    extra = {}
    if job.kind == "stock":
        from app.modules.orders.service import retry_holds  # noqa: PLC0415
        extra["order_dialokasikan"] = await retry_holds(s, Ctx.from_principal(p), p.tenant_id)
    return {"id": str(job_id), "status": "COMMITTED", "result": {**result, **extra},
            "skipped": len(job.errors), "already": False}


@router.get("/history")
async def history(p: Principal = Depends(require("import:run")), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(text("""
        SELECT id, kind, filename, status, total_rows, valid_rows, result, created_at, committed_at
        FROM import_jobs WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 20"""), {"t": p.tenant_id})).all()
    return [{"id": str(r.id), "kind": r.kind, "label": SPECS[r.kind].label, "filename": r.filename,
             "status": r.status, "total_rows": r.total_rows, "valid_rows": r.valid_rows, "result": r.result,
             "created_at": r.created_at, "committed_at": r.committed_at} for r in rows]


