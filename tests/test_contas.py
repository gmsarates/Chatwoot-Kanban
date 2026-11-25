from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.contas import Conta, chave_da_conta, chave_valida


@pytest.fixture()
def segredo():
    anterior = settings.kanban_secret
    settings.kanban_secret = "segredo-teste"
    yield "segredo-teste"
    settings.kanban_secret = anterior


def test_chave_da_conta_e_unica_por_conta(segredo):
    assert chave_valida(7, chave_da_conta(7))
    assert not chave_valida(8, chave_da_conta(7))
    assert not chave_valida(7, "")


def test_sem_segredo_nenhuma_chave_vale():
    assert not chave_valida(7, "qualquer")


def test_cadastro_de_conta_exige_segredo(client, segredo):
    with patch("app.routers.contas.salvar_conta", new_callable=AsyncMock) as salvar:
        negado = client.put("/contas/7", json={"token": "t"})
        assert negado.status_code == 401
        ok = client.put(
            "/contas/7",
            json={"token": "t", "nome": "Agência"},
            headers={"x-kanban-segredo": segredo},
        )
        assert ok.status_code == 200
        assert ok.json()["chave"] == chave_da_conta(7)
        salvar.assert_awaited_once_with(7, "t", "Agência")


def test_chave_errada_e_recusada(client, segredo):
    response = client.get("/kanban/config?conta=7&chave=errada")
    assert response.status_code == 401


def test_conta_da_url_vira_a_conta_da_requisicao(client, segredo, mock_chatwoot_ok):
    conta = Conta(7, "token-da-conta-7")
    with patch("app.main.buscar_conta", new_callable=AsyncMock, return_value=conta):
        response = client.get(
            "/kanban/config",
            headers={"x-kanban-conta": "7", "x-kanban-chave": chave_da_conta(7)},
        )
    assert response.status_code == 200


def test_pagina_repassa_conta_nas_chamadas(client, segredo):
    conta = Conta(7, "token-da-conta-7")
    with patch("app.main.buscar_conta", new_callable=AsyncMock, return_value=conta):
        response = client.get(f"/kanban?conta=7&chave={chave_da_conta(7)}")
    assert response.status_code == 200
    assert "X-Kanban-Conta" in response.text
