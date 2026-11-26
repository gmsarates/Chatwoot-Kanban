import asyncio
import logging
import time as time_module
from datetime import date
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.chatwoot_client import chatwoot_client
from app.config import settings
from app.contas import (
    account_id_atual,
    conta_da_requisicao,
    conta_padrao,
    definir_conta,
    restaurar_conta,
)
from app.database import (
    batch_sync_tasks_from_chatwoot,
    close_task,
    cron_tick,
    edit_task,
    get_active_task,
    get_agent_stats,
    get_audit_history,
    get_or_create_agent,
    get_tasks_for_contacts,
    listar_contas,
    upsert_task,
    write_audit_log,
)

logger = logging.getLogger(__name__)

_board_cache: dict[str, tuple[float, dict]] = {}
_BOARD_CACHE_TTL = 8
_STAGE_MAX_PAGES = 400  # 6.000 contatos por etapa

# Etiquetas: o Chatwoot não manda as etiquetas na lista de contatos, então cada
# contato custa uma chamada. O cache segura isso entre os polls do quadro; o botão
# "Atualizar" pede ?fresh=1 e busca tudo de novo.
_contact_labels_cache: dict[str, tuple[float, list[str]]] = {}
_CONTACT_LABELS_TTL = 120
_label_colors_cache: dict[str, tuple[float, dict[str, str]]] = {}
_LABEL_COLORS_TTL = 300
_LABELS_CONCURRENCY = 8
_DEFAULT_LABEL_COLOR = "#8a94a6"

router = APIRouter(prefix="/kanban", tags=["kanban"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

PIPELINE_ATTR_KEY = "pipeline_01_etapas"

TASK_MSG_ATTR_KEY = "kanban_view_mensaje"
TASK_DATE_ATTR_KEY = "kanban_view_fecha_termino"


class MoveStageRequest(BaseModel):
    stage: str


class StageOrderRequest(BaseModel):
    stages: list[str]


class CreateTaskRequest(BaseModel):
    contact_id: int
    mensaje: str
    fecha_vencimiento: date


class EditTaskRequest(BaseModel):
    mensaje: str
    fecha_vencimiento: date


async def _get_actor(request: Request) -> tuple[int, str, str]:
    email = request.headers.get("cf-access-authenticated-user-email", "")
    if not email:
        email = settings.chatwoot_bot_email
        name = "Bot (dev)"
    else:
        name = email.split("@")[0].replace(".", " ").title()
    agent = await get_or_create_agent(email, name)
    return agent["id"], agent["nombre"], agent["email"]


def _chave_cache(stage: str | None) -> str:
    return f"{account_id_atual()}:{stage or '__all__'}"


def _get_cached_board(stage: str | None) -> dict | None:
    key = _chave_cache(stage)
    entry = _board_cache.get(key)
    if entry and (time_module.time() - entry[0]) < _BOARD_CACHE_TTL:
        return entry[1]
    return None


def _set_cached_board(stage: str | None, data: dict) -> None:
    _board_cache[_chave_cache(stage)] = (time_module.time(), data)


def _invalidate_board_cache() -> None:
    prefixo = f"{account_id_atual()}:"
    for chave in [c for c in _board_cache if c.startswith(prefixo)]:
        _board_cache.pop(chave, None)


def _clear_labels_cache() -> None:
    prefixo = f"{account_id_atual()}:"
    for chave in [c for c in _contact_labels_cache if c.startswith(prefixo)]:
        _contact_labels_cache.pop(chave, None)
    _label_colors_cache.pop(str(account_id_atual()), None)


async def _label_colors() -> dict[str, str]:
    chave = str(account_id_atual())
    entry = _label_colors_cache.get(chave)
    if entry and (time_module.time() - entry[0]) < _LABEL_COLORS_TTL:
        return entry[1]
    try:
        labels = await chatwoot_client.list_labels()
    except Exception as exc:  # sem cores o quadro ainda funciona
        logger.warning("Failed to list labels: %s", exc)
        return entry[1] if entry else {}
    cores = {
        str(lbl.get("title")): lbl.get("color") or _DEFAULT_LABEL_COLOR
        for lbl in labels or []
        if isinstance(lbl, dict) and lbl.get("title")
    }
    _label_colors_cache[chave] = (time_module.time(), cores)
    return cores


async def _attach_labels(cards: list[dict]) -> None:
    """Põe em cada card a lista de etiquetas do contato, com cor."""
    if not cards:
        return
    cores = await _label_colors()
    agora = time_module.time()
    conta = account_id_atual()
    sem = asyncio.Semaphore(_LABELS_CONCURRENCY)

    async def _labels_do(contact_id: int) -> list[str]:
        chave = f"{conta}:{contact_id}"
        entry = _contact_labels_cache.get(chave)
        if entry and (agora - entry[0]) < _CONTACT_LABELS_TTL:
            return entry[1]
        async with sem:
            try:
                labels = await chatwoot_client.get_contact_labels(contact_id)
            except Exception as exc:  # uma falha não pode derrubar o quadro
                logger.warning(
                    "Failed to get labels for contact %s: %s", contact_id, exc
                )
                return entry[1] if entry else []
        _contact_labels_cache[chave] = (time_module.time(), labels)
        return labels

    ids = [c["id"] for c in cards if c.get("id")]
    resultados = await asyncio.gather(*(_labels_do(i) for i in ids))
    por_id = dict(zip(ids, resultados, strict=True))
    for card in cards:
        card["labels"] = [
            {"title": t, "color": cores.get(t, _DEFAULT_LABEL_COLOR)}
            for t in por_id.get(card.get("id"), [])
        ]


async def _bg_sync_task_and_audit(
    *,
    contact_id: int,
    attrs: dict,
    actor_id: int,
    actor_name: str,
    action: str,
    previous_state: dict | None,
    new_state: dict | None,
) -> None:
    # Roda antes de responder: assim o quadro, ao recarregar, já lê do Chatwoot
    # a tarefa atualizada (o /board sincroniza as tarefas a partir dos atributos).
    chatwoot_ok = True
    try:
        await chatwoot_client.safe_update_contact_custom_attributes(
            contact_id,
            attrs,
            skip_read=True,
        )
    except Exception as exc:
        logger.error(
            "Background Chatwoot sync failed for contact %s: %s",
            contact_id,
            exc,
        )
        chatwoot_ok = False

    await write_audit_log(
        contact_id=contact_id,
        actor_agent_id=actor_id,
        actor_name=actor_name,
        action=action,
        previous_state=previous_state,
        new_state=new_state,
        chatwoot_call_ok=chatwoot_ok,
    )


# Com várias contas, a página é aberta com ?conta=&chave= (URL do Dashboard App da
# conta). Este script repassa os dois em todo fetch para a própria API e nos links
# entre o quadro e o dashboard, sem mexer no resto do front.
_SCRIPT_CONTA = """<script>
(function () {
  var p = new URLSearchParams(location.search);
  var conta = p.get('conta'), chave = p.get('chave');
  if (!conta) return;
  var original = window.fetch;
  window.fetch = function (recurso, opcoes) {
    var url = typeof recurso === 'string' ? recurso : recurso.url;
    if (url.charAt(0) === '/' || url.indexOf(location.origin) === 0) {
      opcoes = Object.assign({}, opcoes);
      var h = new Headers(opcoes.headers || {});
      h.set('X-Kanban-Conta', conta);
      h.set('X-Kanban-Chave', chave);
      opcoes.headers = h;
    }
    return original.call(this, recurso, opcoes);
  };
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href^="/kanban"]');
    if (!a) return;
    var u = new URL(a.getAttribute('href'), location.origin);
    u.searchParams.set('conta', conta);
    u.searchParams.set('chave', chave);
    a.setAttribute('href', u.pathname + u.search);
  }, true);
})();
</script>"""


def _pagina(nome: str) -> HTMLResponse:
    html = TEMPLATE_DIR / nome
    if not html.exists():
        raise HTTPException(status_code=404, detail="template not found")
    return HTMLResponse(html.read_text().replace("<head>", "<head>" + _SCRIPT_CONTA, 1))


@router.get("", response_class=HTMLResponse)
async def kanban_page():
    return _pagina("kanban.html")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return _pagina("dashboard.html")


@router.patch("/board/{contact_id}/stage")
async def move_stage(contact_id: int, body: MoveStageRequest, request: Request):
    actor_id, actor_name, actor_email = await _get_actor(request)

    definitions = await chatwoot_client.get_custom_attribute_definitions()
    pipeline_attr = _find_pipeline_attribute(definitions)
    valid_stages = pipeline_attr.get("attribute_values", []) if pipeline_attr else []
    if valid_stages and body.stage not in valid_stages:
        raise HTTPException(
            status_code=400,
            detail=f"Etapa inválida '{body.stage}'. Válidas: {valid_stages}",
        )

    try:
        await write_audit_log(
            contact_id=contact_id,
            actor_agent_id=actor_id,
            actor_name=actor_name,
            action="stage_change",
            previous_state={"stage_from": "unknown"},
            new_state={"stage_to": body.stage},
        )
        await chatwoot_client.safe_update_contact_custom_attributes(
            contact_id,
            {PIPELINE_ATTR_KEY: body.stage},
            skip_read=True,
        )
        await write_audit_log(
            contact_id=contact_id,
            actor_agent_id=actor_id,
            actor_name=actor_name,
            action="stage_change",
            previous_state=None,
            new_state={"stage_to": body.stage},
            chatwoot_call_ok=True,
        )
        _invalidate_board_cache()
        return {"ok": True, "stage": body.stage}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to move contact %s: %s", contact_id, exc)
        await write_audit_log(
            contact_id=contact_id,
            actor_agent_id=actor_id,
            actor_name=actor_name,
            action="stage_change",
            previous_state=None,
            new_state={"stage_to": body.stage},
            chatwoot_call_ok=False,
        )
        raise HTTPException(
            status_code=502, detail=f"Erro na API do Chatwoot: {exc}"
        ) from exc


@router.put("/stages/order")
async def reorder_stages(body: StageOrderRequest):
    """Muda a ordem das colunas regravando a lista de valores do atributo de etapas.

    A ordem do quadro é a ordem dessa lista no Chatwoot, então vale para todo mundo
    e para a tela de atributos do próprio Chatwoot. Só aceita a mesma lista reordenada:
    nada de criar, renomear ou apagar etapa por aqui.
    """
    try:
        chatwoot_client.clear_attribute_cache()  # compara com a lista atual, não a do cache
        definitions = await chatwoot_client.get_custom_attribute_definitions()
    except Exception as exc:
        logger.error("Failed to fetch attribute definitions: %s", exc)
        raise HTTPException(
            status_code=502, detail=f"Erro na API do Chatwoot: {exc}"
        ) from exc

    pipeline_attr = _find_pipeline_attribute(definitions)
    if not pipeline_attr or not pipeline_attr.get("id"):
        raise HTTPException(
            status_code=404,
            detail=f"Atributo '{PIPELINE_ATTR_KEY}' não encontrado no Chatwoot.",
        )

    current = list(pipeline_attr.get("attribute_values") or [])
    if len(body.stages) != len(current) or sorted(body.stages) != sorted(current):
        raise HTTPException(
            status_code=409,
            detail=(
                "As etapas mudaram no Chatwoot enquanto você reordenava. "
                "Atualize o quadro e tente de novo."
            ),
        )
    if body.stages == current:
        return {"ok": True, "stages": current}

    try:
        await chatwoot_client.update_custom_attribute_values(
            pipeline_attr["id"], body.stages
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise HTTPException(
                status_code=403,
                detail=(
                    "O Chatwoot recusou: mudar a ordem das etapas exige que o usuário "
                    "do token do kanban seja administrador da conta."
                ),
            ) from exc
        raise HTTPException(
            status_code=502, detail=f"Erro na API do Chatwoot: {exc}"
        ) from exc
    except Exception as exc:
        logger.error("Failed to reorder stages: %s", exc)
        raise HTTPException(
            status_code=502, detail=f"Erro na API do Chatwoot: {exc}"
        ) from exc

    _invalidate_board_cache()
    logger.info("Ordem das etapas alterada: %s", body.stages)
    return {"ok": True, "stages": body.stages}


@router.delete("/board/{contact_id}/stage")
async def remove_from_board(contact_id: int, request: Request):
    """Tira o contato do kanban apagando o atributo de etapa no Chatwoot.

    O contato e as conversas continuam no Chatwoot; só deixa de aparecer no quadro.
    """
    actor_id, actor_name, _ = await _get_actor(request)
    try:
        await chatwoot_client.remove_contact_custom_attributes(
            contact_id, [PIPELINE_ATTR_KEY]
        )
    except Exception as exc:
        logger.error("Failed to remove contact %s from board: %s", contact_id, exc)
        await _audit_board_remove(contact_id, actor_id, actor_name, ok=False)
        raise HTTPException(
            status_code=502, detail=f"Erro na API do Chatwoot: {exc}"
        ) from exc

    _invalidate_board_cache()
    await _audit_board_remove(contact_id, actor_id, actor_name, ok=True)
    return {"ok": True}


async def _audit_board_remove(
    contact_id: int, actor_id: int, actor_name: str, *, ok: bool
) -> None:
    try:
        await write_audit_log(
            contact_id=contact_id,
            actor_agent_id=actor_id,
            actor_name=actor_name,
            action="board_remove",
            previous_state=None,
            new_state={"stage_to": None},
            chatwoot_call_ok=ok,
        )
    except Exception as exc:  # o log de auditoria não pode quebrar a remoção
        logger.warning("Audit log failed for board_remove %s: %s", contact_id, exc)


@router.post("/tasks")
async def create_task(body: CreateTaskRequest, request: Request):
    actor_id, actor_name, actor_email = await _get_actor(request)

    existing = await get_active_task(body.contact_id)
    action_label = "updated" if existing else "created"
    previous = None
    if existing:
        previous = {
            "mensaje": existing["mensaje"],
            "fecha_vencimiento": str(existing["fecha_vencimiento"]),
            "creado_por": existing["creado_por_nombre"],
        }

    result = await upsert_task(
        contact_id=body.contact_id,
        conversation_id=None,
        mensaje=body.mensaje,
        fecha_vencimiento=body.fecha_vencimiento,
        actor_agent_id=actor_id,
    )

    audit_action = "task_overwritten" if existing else "create"
    fecha_iso = body.fecha_vencimiento.isoformat() + "T23:59:59.999Z"
    attrs = {
        TASK_MSG_ATTR_KEY: body.mensaje,
        TASK_DATE_ATTR_KEY: fecha_iso,
    }
    await _bg_sync_task_and_audit(
        contact_id=body.contact_id,
        attrs=attrs,
        actor_id=actor_id,
        actor_name=actor_name,
        action=audit_action,
        previous_state=previous,
        new_state={
            "mensaje": body.mensaje,
            "fecha_vencimiento": body.fecha_vencimiento.isoformat(),
        },
    )

    _invalidate_board_cache()
    return {
        "ok": True,
        "action": action_label,
        "task_id": result["id"],
    }


@router.patch("/tasks/{task_id}")
async def update_task_endpoint(task_id: int, body: EditTaskRequest, request: Request):
    actor_id, actor_name, _ = await _get_actor(request)

    result = await edit_task(
        task_id,
        mensaje=body.mensaje,
        fecha_vencimiento=body.fecha_vencimiento,
    )
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")

    previous = result.get("previous", {})
    cid = previous.get("contact_id")
    if cid:
        fecha_iso = body.fecha_vencimiento.isoformat() + "T23:59:59.999Z"
        attrs = {
            TASK_MSG_ATTR_KEY: body.mensaje,
            TASK_DATE_ATTR_KEY: fecha_iso,
        }
        await _bg_sync_task_and_audit(
            contact_id=cid,
            attrs=attrs,
            actor_id=actor_id,
            actor_name=actor_name,
            action="edit",
            previous_state={
                "mensaje": previous.get("mensaje"),
                "fecha_vencimiento": str(previous.get("fecha_vencimiento", "")),
            },
            new_state={
                "mensaje": body.mensaje,
                "fecha_vencimiento": body.fecha_vencimiento.isoformat(),
            },
        )

    _invalidate_board_cache()
    return {"ok": True, "task_id": task_id}


@router.patch("/tasks/{task_id}/close")
async def close_task_endpoint(task_id: int, request: Request):
    actor_id, actor_name, _ = await _get_actor(request)

    result = await close_task(task_id, cerrado_por=actor_id)
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    if result.get("error") == "already_closed":
        raise HTTPException(status_code=409, detail="A tarefa já foi concluída")

    previous = result.get("previous", {})
    cid = previous.get("contact_id")
    if cid:
        await _bg_sync_task_and_audit(
            contact_id=cid,
            attrs={TASK_MSG_ATTR_KEY: "", TASK_DATE_ATTR_KEY: ""},
            actor_id=actor_id,
            actor_name=actor_name,
            action="close",
            previous_state={"estado": previous.get("estado")},
            new_state={"estado": "tarea_cerrada"},
        )

    _invalidate_board_cache()
    return {"ok": True, "task_id": task_id}


@router.get("/tasks")
async def get_tasks(contact_id: int | None = None):
    if contact_id:
        task = await get_active_task(contact_id)
        return {"task": task}
    return {"tasks": []}


@router.post("/cron/tick")
async def kanban_cron_tick(request: Request):
    # Várias contas e nenhuma na requisição: roda para cada conta cadastrada (e a
    # padrão, se houver). Instalação de uma conta só: segue como antes
    if conta_da_requisicao() is None and settings.kanban_secret:
        contas = await listar_contas()
        padrao = conta_padrao()
        if padrao and all(c.account_id != padrao.account_id for c in contas):
            contas.append(padrao)
        resultados = {}
        for conta in contas:
            token = definir_conta(conta)
            try:
                resultados[conta.account_id] = await _cron_da_conta(request)
            except HTTPException as exc:
                resultados[conta.account_id] = {"ok": False, "erro": exc.detail}
            finally:
                restaurar_conta(token)
        return {"ok": True, "contas": resultados}
    return await _cron_da_conta(request)


async def _cron_da_conta(request: Request):
    actor_id, actor_name, _ = await _get_actor(request)
    try:
        result = await cron_tick()
        await write_audit_log(
            contact_id=None,
            actor_agent_id=actor_id,
            actor_name=actor_name,
            action="cron_tick",
            previous_state=None,
            new_state=result,
            source="cron",
        )
        return {"ok": True, "transitions": result}
    except Exception as exc:
        logger.error("Cron tick failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Cron tick failed: {exc}") from exc


@router.get("/stats")
async def kanban_stats():
    stats = await get_agent_stats()
    return {"agents": stats}


@router.get("/stats/history")
async def kanban_stats_history(limit: int = 50):
    history = await get_audit_history(limit)
    return {"history": history}


_SEARCH_LIMIT = 20


@router.get("/contacts/search")
async def search_contacts(q: str = ""):
    """Contatos do Chatwoot para o botão "Adicionar contato".

    Sem termo de busca devolve os contatos com atividade mais recente.
    """
    query = q.strip()
    try:
        if query:
            raw = await chatwoot_client.search_contacts(query)
        else:
            raw = await chatwoot_client.list_recent_contacts()
    except Exception as exc:
        logger.error("Failed to search contacts (q=%r): %s", query, exc)
        raise HTTPException(
            status_code=502, detail=f"Erro na API do Chatwoot: {exc}"
        ) from exc

    results = []
    for contact in raw[:_SEARCH_LIMIT]:
        attrs = contact.get("custom_attributes") or {}
        results.append(
            {
                "id": contact.get("id"),
                "name": contact.get("name") or "",
                "phone_number": contact.get("phone_number") or "",
                "email": contact.get("email") or "",
                "thumbnail": contact.get("thumbnail") or "",
                "stage": attrs.get(PIPELINE_ATTR_KEY) or None,
            }
        )
    return {"contacts": results}


# "Adicionar todos disponíveis": a lista sai paginada do Chatwoot e o navegador
# manda os contatos em lotes, para não estourar o tempo de uma requisição só.
_BULK_MAX_PAGES = 500
_BULK_BATCH_MAX = 50
_BULK_CONCURRENCY = 5


class BulkStageRequest(BaseModel):
    stage: str
    contact_ids: list[int]


@router.get("/contacts/available")
async def available_contacts(q: str = ""):
    """Ids de todos os contatos fora do kanban (com a busca, só os que batem nela)."""
    query = q.strip()
    ids: list[int] = []
    vistos: set[int] = set()
    page = 1
    truncated = False
    try:
        while True:
            if query:
                raw = await chatwoot_client.search_contacts(query, page=page)
            else:
                raw = await chatwoot_client.list_recent_contacts(page=page)
            if not raw:
                break
            novos = 0
            for contact in raw:
                cid = contact.get("id")
                if not cid or cid in vistos:
                    continue
                vistos.add(cid)
                novos += 1
                attrs = contact.get("custom_attributes") or {}
                if not attrs.get(PIPELINE_ATTR_KEY):
                    ids.append(cid)
            if not novos:  # página repetida: o Chatwoot já não tem mais nada
                break
            page += 1
            if page > _BULK_MAX_PAGES:
                truncated = True
                break
    except Exception as exc:
        logger.error("Failed to list available contacts (q=%r): %s", query, exc)
        raise HTTPException(
            status_code=502, detail=f"Erro na API do Chatwoot: {exc}"
        ) from exc
    return {"contact_ids": ids, "count": len(ids), "truncated": truncated}


@router.post("/board/bulk-stage")
async def bulk_stage(body: BulkStageRequest, request: Request):
    """Põe um lote de contatos numa etapa. Devolve quais entraram e quais falharam."""
    if len(body.contact_ids) > _BULK_BATCH_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"No máximo {_BULK_BATCH_MAX} contatos por lote.",
        )
    actor_id, actor_name, _ = await _get_actor(request)

    definitions = await chatwoot_client.get_custom_attribute_definitions()
    pipeline_attr = _find_pipeline_attribute(definitions)
    valid_stages = pipeline_attr.get("attribute_values", []) if pipeline_attr else []
    if valid_stages and body.stage not in valid_stages:
        raise HTTPException(
            status_code=400,
            detail=f"Etapa inválida '{body.stage}'. Válidas: {valid_stages}",
        )

    sem = asyncio.Semaphore(_BULK_CONCURRENCY)
    added: list[int] = []
    failed: list[dict] = []

    async def _um(contact_id: int) -> None:
        async with sem:
            try:
                await chatwoot_client.safe_update_contact_custom_attributes(
                    contact_id, {PIPELINE_ATTR_KEY: body.stage}, skip_read=True
                )
                ok = True
                added.append(contact_id)
            except Exception as exc:  # um contato com erro não para o lote
                logger.error("Bulk add failed for contact %s: %s", contact_id, exc)
                ok = False
                failed.append({"id": contact_id, "error": str(exc)})
        try:
            await write_audit_log(
                contact_id=contact_id,
                actor_agent_id=actor_id,
                actor_name=actor_name,
                action="stage_change",
                previous_state=None,
                new_state={"stage_to": body.stage, "bulk": True},
                chatwoot_call_ok=ok,
            )
        except Exception as exc:  # o log de auditoria não pode quebrar o lote
            logger.warning("Audit log failed for bulk add %s: %s", contact_id, exc)

    await asyncio.gather(*(_um(cid) for cid in dict.fromkeys(body.contact_ids)))
    if added:
        _invalidate_board_cache()
    return {"added": added, "failed": failed}


class BulkRemoveRequest(BaseModel):
    contact_ids: list[int]


@router.post("/board/bulk-remove")
async def bulk_remove(body: BulkRemoveRequest, request: Request):
    """Tira um lote de contatos do kanban (apaga o atributo de etapa).

    Contatos e conversas continuam no Chatwoot; só saem do quadro.
    """
    if len(body.contact_ids) > _BULK_BATCH_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"No máximo {_BULK_BATCH_MAX} contatos por lote.",
        )
    actor_id, actor_name, _ = await _get_actor(request)
    sem = asyncio.Semaphore(_BULK_CONCURRENCY)
    removed: list[int] = []
    failed: list[dict] = []

    async def _um(contact_id: int) -> None:
        async with sem:
            try:
                await chatwoot_client.remove_contact_custom_attributes(
                    contact_id, [PIPELINE_ATTR_KEY]
                )
                ok = True
                removed.append(contact_id)
            except Exception as exc:  # um contato com erro não para o lote
                logger.error("Bulk remove failed for contact %s: %s", contact_id, exc)
                ok = False
                failed.append({"id": contact_id, "error": str(exc)})
        await _audit_board_remove(contact_id, actor_id, actor_name, ok=ok)

    await asyncio.gather(*(_um(cid) for cid in dict.fromkeys(body.contact_ids)))
    if removed:
        _invalidate_board_cache()
    return {"removed": removed, "failed": failed}


@router.get("/config")
async def kanban_config(fresh: bool = False):
    if fresh:  # a tela de ordenar etapas precisa da lista atual, não da do cache
        chatwoot_client.clear_attribute_cache()
    try:
        definitions = await chatwoot_client.get_custom_attribute_definitions()
    except Exception as exc:
        logger.error("Failed to fetch attribute definitions: %s", exc)
        raise HTTPException(
            status_code=502, detail=f"Erro na API do Chatwoot: {exc}"
        ) from exc

    pipeline_attr = _find_pipeline_attribute(definitions)
    if not pipeline_attr:
        return {
            "columns": [],
            "chatwoot_url": chatwoot_client.base_url,
            "chatwoot_account_id": chatwoot_client.account_id,
            "chatwoot_frontend_url": settings.chatwoot_frontend_url,
        }

    return {
        "columns": pipeline_attr.get("attribute_values", []),
        "chatwoot_url": chatwoot_client.base_url,
        "chatwoot_account_id": chatwoot_client.account_id,
        "chatwoot_frontend_url": settings.chatwoot_frontend_url,
    }


@router.get("/board")
async def kanban_board(stage: str | None = None, fresh: bool = False):
    if fresh:  # botão "Atualizar": ignora os caches (etiquetas inclusive)
        _invalidate_board_cache()
        _clear_labels_cache()
    cached = _get_cached_board(stage)
    if cached is not None:
        return cached

    try:
        definitions = await chatwoot_client.get_custom_attribute_definitions()
    except Exception as exc:
        logger.error("Failed to fetch attribute definitions: %s", exc)
        raise HTTPException(
            status_code=502, detail=f"Erro na API do Chatwoot: {exc}"
        ) from exc

    pipeline_attr = _find_pipeline_attribute(definitions)
    if not pipeline_attr:
        result = {
            "columns": [],
            "chatwoot_url": chatwoot_client.base_url,
            "chatwoot_account_id": chatwoot_client.account_id,
            "chatwoot_frontend_url": settings.chatwoot_frontend_url,
        }
        _set_cached_board(stage, result)
        return result

    stages = pipeline_attr.get("attribute_values", [])
    target_stages = [stage] if stage else stages

    async def _load_stage(s: str) -> dict:
        payload = {
            "payload": [
                {
                    "attribute_key": PIPELINE_ATTR_KEY,
                    "filter_operator": "equal_to",
                    "values": [s],
                    "query_operator": None,
                }
            ]
        }
        all_cards = []
        vistos: set = set()
        page = 1
        try:
            # O Chatwoot ignora page_size e devolve 15 por página, então não dá para
            # calcular o total de páginas: busca até juntar o "count" ou a página vir
            # vazia/repetida.
            while True:
                resp = await chatwoot_client.filter_contacts(payload, page=page)
                cards = [c for c in _parse_contacts(resp) if c.get("id") not in vistos]
                if not cards:
                    break
                vistos.update(c.get("id") for c in cards)
                all_cards.extend(cards)
                count = _extract_meta(resp).get("count") or 0
                if count and len(all_cards) >= count:
                    break
                page += 1
                if page > _STAGE_MAX_PAGES:
                    logger.warning("Etapa '%s': parou em %s páginas", s, page - 1)
                    break

            contacts_data = [
                {
                    "contact_id": c["id"],
                    "custom_attributes": c.get("custom_attributes") or {},
                }
                for c in all_cards
                if c.get("id")
            ]
            await batch_sync_tasks_from_chatwoot(contacts_data)

            await _attach_labels(all_cards)

            contact_ids = [c["id"] for c in all_cards if c.get("id")]
            tasks_map = await get_tasks_for_contacts(contact_ids)
            for card in all_cards:
                task = tasks_map.get(card["id"])
                if task:
                    card["task"] = {
                        "id": task["id"],
                        "estado": task["estado"],
                        "mensaje": task["mensaje"],
                        "fecha_vencimiento": str(task["fecha_vencimiento"]),
                        "creado_por": task["creado_por_nombre"],
                    }
            return {"stage": s, "contacts": all_cards}
        except Exception as exc:
            logger.error("Failed to filter contacts for stage '%s': %s", s, exc)
            raise HTTPException(
                status_code=502,
                detail=f"Erro na API do Chatwoot ao filtrar a etapa '{s}': {exc}",
            ) from exc

    stage_tasks = [_load_stage(s) for s in target_stages]
    results = await asyncio.gather(*stage_tasks)

    result = {
        "columns": list(results),
        "chatwoot_url": chatwoot_client.base_url,
        "chatwoot_account_id": chatwoot_client.account_id,
        "chatwoot_frontend_url": settings.chatwoot_frontend_url,
        "generated_at": time_module.time(),
    }
    _set_cached_board(stage, result)
    return result


@router.get("/debug-status")
async def kanban_debug_status():
    status = {"chatwoot_url": chatwoot_client.base_url, "checks": {}}

    try:
        definitions = await chatwoot_client.get_custom_attribute_definitions()
        status["checks"]["chatwoot_connection"] = "ok"
        status["checks"]["attribute_definitions_count"] = len(definitions)

        contact_attrs = [
            d for d in definitions if d.get("attribute_model") == "contact_attribute"
        ]
        conversation_attrs = [
            d
            for d in definitions
            if d.get("attribute_model") == "conversation_attribute"
        ]
        status["checks"]["contact_attributes_count"] = len(contact_attrs)
        status["checks"]["conversation_attributes_count"] = len(conversation_attrs)

        pipeline_attr = _find_pipeline_attribute(definitions)
        if pipeline_attr:
            status["checks"]["pipeline_attribute"] = "found"
            status["checks"]["pipeline_attribute_key"] = pipeline_attr.get(
                "attribute_key"
            )
            status["checks"]["pipeline_attribute_model"] = pipeline_attr.get(
                "attribute_model"
            )
            status["checks"]["pipeline_stages"] = pipeline_attr.get(
                "attribute_values", []
            )
        else:
            status["checks"]["pipeline_attribute"] = "not_found"
            status["checks"]["available_keys"] = [
                d.get("attribute_key") for d in definitions
            ]
    except Exception as exc:
        status["checks"]["chatwoot_connection"] = "failed"
        status["checks"]["error"] = str(exc)

    return status


@router.get("/debug-raw")
async def kanban_debug_raw(stage: str = "Potencial"):
    payload = {
        "payload": [
            {
                "attribute_key": PIPELINE_ATTR_KEY,
                "filter_operator": "equal_to",
                "values": [stage],
                "query_operator": None,
            }
        ]
    }
    try:
        resp = await chatwoot_client.filter_contacts(payload)
        return {
            "stage": stage,
            "raw": resp,
            "top_level_keys": list(resp.keys())
            if isinstance(resp, dict)
            else type(resp).__name__,
            "parsed_contacts": _parse_contacts(resp),
        }
    except Exception as exc:
        logger.error("Debug raw failed for stage '%s': %s", stage, exc)
        return {"error": str(exc)}


def _find_pipeline_attribute(definitions: list) -> dict | None:
    for d in definitions:
        if (
            d.get("attribute_key") == PIPELINE_ATTR_KEY
            and d.get("attribute_model") == "contact_attribute"
        ):
            return d
    for d in definitions:
        if d.get("attribute_key") == PIPELINE_ATTR_KEY:
            return d
    return None


def _extract_meta(resp: dict) -> dict:
    meta = resp.get("meta") or {}
    if meta:
        return meta
    for key in ("payload", "data"):
        val = resp.get(key)
        if isinstance(val, dict):
            meta = val.get("meta") or {}
            if meta:
                return meta
    return {}


def _parse_contacts(resp: dict | list) -> list:
    raw = _extract_contact_list(resp)
    return [_normalize_contact(c) for c in raw]


def _extract_contact_list(resp: dict | list) -> list:
    if isinstance(resp, list):
        return resp
    if not isinstance(resp, dict):
        return []

    for key in ("contacts", "data", "payload"):
        val = resp.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            return _extract_contact_list(val)

    return []


def _normalize_contact(contact: dict) -> dict:
    return {
        "id": contact.get("id"),
        "contact_name": contact.get("name") or "",
        "email": contact.get("email") or "",
        "phone_number": contact.get("phone_number") or "",
        "thumbnail": contact.get("thumbnail") or "",
        "custom_attributes": contact.get("custom_attributes") or {},
        "last_activity_at": contact.get("last_activity_at") or "",
    }
