"""Várias contas do Chatwoot no mesmo Kanban (uma por agência do Turvia).

Cada requisição diz de qual conta ela é por ``conta`` + ``chave`` (query string ou
cabeçalhos ``X-Kanban-Conta``/``X-Kanban-Chave``). A chave é um HMAC do id da conta
com ``KANBAN_SECRET``: quem só conhece a URL de uma conta não abre a de outra trocando
o número. O token da API de cada conta fica na tabela ``contas``, cadastrado pelo
Turvia em ``PUT /contas/{account_id}``.

Sem ``conta`` na requisição vale a conta única das variáveis de ambiente
(``CHATWOOT_ACCOUNT_ID``/``CHATWOOT_BOT_TOKEN``), como antes.
"""

import hashlib
import hmac
from contextvars import ContextVar
from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class Conta:
    account_id: int
    token: str


_conta: ContextVar[Conta | None] = ContextVar("conta", default=None)


class SemContaError(RuntimeError):
    """Nenhuma conta na requisição e nenhuma conta padrão configurada."""


def conta_padrao() -> Conta | None:
    if settings.chatwoot_account_id and settings.chatwoot_bot_token:
        return Conta(settings.chatwoot_account_id, settings.chatwoot_bot_token)
    return None


def conta_atual() -> Conta:
    conta = _conta.get() or conta_padrao()
    if conta is None:
        raise SemContaError("Nenhuma conta do Chatwoot selecionada")
    return conta


def conta_da_requisicao() -> Conta | None:
    """A conta que veio na requisição (sem cair na padrão)."""
    return _conta.get()


def account_id_atual() -> int:
    return conta_atual().account_id


def definir_conta(conta: Conta | None):
    return _conta.set(conta)


def restaurar_conta(token) -> None:
    _conta.reset(token)


def chave_da_conta(account_id: int) -> str:
    return hmac.new(
        settings.kanban_secret.encode(), f"conta:{account_id}".encode(), hashlib.sha256
    ).hexdigest()


def chave_valida(account_id: int, chave: str) -> bool:
    if not settings.kanban_secret or not chave:
        return False
    return hmac.compare_digest(chave_da_conta(account_id), chave)
