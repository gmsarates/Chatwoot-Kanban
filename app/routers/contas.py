"""Cadastro das contas do Chatwoot pelo Turvia (protegido por KANBAN_SECRET)."""

import hmac

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.contas import chave_da_conta
from app.database import remover_conta, salvar_conta

router = APIRouter(prefix="/contas", tags=["contas"])


class ContaRequest(BaseModel):
    token: str
    nome: str | None = None


def _exigir_segredo(segredo: str | None) -> None:
    if not settings.kanban_secret or not segredo:
        raise HTTPException(status_code=401, detail="segredo inválido")
    if not hmac.compare_digest(settings.kanban_secret, segredo):
        raise HTTPException(status_code=401, detail="segredo inválido")


@router.put("/{account_id}")
async def cadastrar_conta(
    account_id: int,
    body: ContaRequest,
    x_kanban_segredo: str | None = Header(default=None),
):
    """Cadastra (ou troca o token de) uma conta e devolve a chave dela para a URL."""
    _exigir_segredo(x_kanban_segredo)
    await salvar_conta(account_id, body.token, body.nome)
    return {"account_id": account_id, "chave": chave_da_conta(account_id)}


@router.delete("/{account_id}")
async def descadastrar_conta(
    account_id: int, x_kanban_segredo: str | None = Header(default=None)
):
    _exigir_segredo(x_kanban_segredo)
    await remover_conta(account_id)
    return {"ok": True}
