from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.chatwoot_client import chatwoot_client
from app.contas import chave_valida, conta_padrao, definir_conta, restaurar_conta
from app.database import buscar_conta, close_pool, init_pool
from app.routers import api, contas, conversations, kanban, webhooks


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_pool()
    await chatwoot_client.init()
    yield
    await chatwoot_client.close()
    await close_pool()


app = FastAPI(title="Chatwoot-Kanban", lifespan=lifespan)

# Rotas que falam com uma conta do Chatwoot (o cadastro de contas e o /health não)
_PREFIXOS_COM_CONTA = ("/kanban", "/api", "/debug", "/webhooks")
# O cron roda para todas as contas quando não vem uma específica
_SEM_CONTA_OBRIGATORIA = ("/kanban/cron/tick",)


@app.middleware("http")
async def selecionar_conta(request: Request, call_next):
    """Define a conta do Chatwoot da requisição a partir de conta + chave."""
    caminho = request.url.path
    if not caminho.startswith(_PREFIXOS_COM_CONTA):
        return await call_next(request)

    conta_txt = request.query_params.get("conta") or request.headers.get(
        "x-kanban-conta"
    )
    chave = request.query_params.get("chave") or request.headers.get(
        "x-kanban-chave", ""
    )
    request.state.conta_por_chave = False
    if not conta_txt:
        livre = caminho in _SEM_CONTA_OBRIGATORIA or caminho in ("/kanban", "/kanban/")
        if conta_padrao() is None and not livre:
            return JSONResponse({"detail": "conta não informada"}, status_code=401)
        return await call_next(request)

    try:
        account_id = int(conta_txt)
    except ValueError:
        return JSONResponse({"detail": "conta inválida"}, status_code=400)
    if not chave_valida(account_id, chave):
        return JSONResponse({"detail": "chave inválida"}, status_code=401)
    conta = await buscar_conta(account_id)
    if conta is None:
        return JSONResponse({"detail": "conta não cadastrada"}, status_code=404)

    request.state.conta_por_chave = True
    token = definir_conta(conta)
    try:
        return await call_next(request)
    finally:
        restaurar_conta(token)


app.include_router(contas.router)
app.include_router(conversations.router)
app.include_router(api.router)
app.include_router(kanban.router)
app.include_router(webhooks.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
