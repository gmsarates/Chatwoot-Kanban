"""Cliente sobre la API de Chatwoot con pooling y retry."""

import asyncio
import logging
import time

import httpx

from app.config import settings
from app.contas import conta_atual

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = [0.5, 1.0, 2.0]
_ATTR_CACHE_TTL = 300  # 5 minutos


class ChatwootClient:
    def __init__(self) -> None:
        self.base_url = settings.chatwoot_base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        # Por conta: as definições de atributos mudam de uma agência para outra
        self._attr_cache: dict[int, tuple[float, list]] = {}

    @property
    def account_id(self) -> int:
        """Conta da requisição atual (ver app.contas)."""
        return conta_atual().account_id

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "api_access_token": conta_atual().token,
            "Content-Type": "application/json",
        }

    async def init(self) -> None:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def _account_path(self) -> str:
        return f"{self.base_url}/api/v1/accounts/{self.account_id}"

    async def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        if self._client is None or self._client.is_closed:
            await self.init()
        assert self._client is not None

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = await self._client.request(
                    method, url, headers=self._headers, **kwargs
                )
                if response.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                    logger.warning(
                        "Chatwoot %s %s returned %s, retrying in %.1fs (%d/%d)",
                        method,
                        url,
                        response.status_code,
                        _RETRY_DELAYS[attempt],
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
                    continue
                if response.status_code >= 400:
                    logger.error(
                        "Chatwoot %s %s error %s: %s",
                        method,
                        url,
                        response.status_code,
                        response.text[:500],
                    )
                response.raise_for_status()
                return response
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    logger.warning(
                        "Chatwoot %s %s transport error: %s, retrying in %.1fs (%d/%d)",
                        method,
                        url,
                        exc,
                        _RETRY_DELAYS[attempt],
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
                    continue
                raise
        raise last_exc  # type: ignore[misc]

    async def filter_conversations(self, payload: dict, page: int = 1) -> dict:
        url = f"{self._account_path}/conversations/filter"
        body = {**payload, "page": page}
        resp = await self._request("POST", url, json=body)
        return resp.json()

    async def filter_contacts(
        self, payload: dict, page: int = 1, page_size: int = 50
    ) -> dict:
        url = f"{self._account_path}/contacts/filter"
        body = {**payload, "page": page, "page_size": page_size}
        resp = await self._request("POST", url, json=body)
        return resp.json()

    async def search_contacts(self, query: str, page: int = 1) -> list:
        """Busca contatos por nome, telefone ou email (API de busca do Chatwoot)."""
        url = f"{self._account_path}/contacts/search"
        resp = await self._request(
            "GET", url, params={"q": query, "page": page, "include_contacts": "true"}
        )
        data = resp.json()
        return data.get("payload", []) if isinstance(data, dict) else data

    async def list_recent_contacts(self, page: int = 1) -> list:
        """Contatos com atividade mais recente primeiro."""
        url = f"{self._account_path}/contacts"
        resp = await self._request(
            "GET", url, params={"page": page, "sort": "-last_activity_at"}
        )
        data = resp.json()
        return data.get("payload", []) if isinstance(data, dict) else data

    async def get_contact_labels(self, contact_id: int) -> list[str]:
        """Etiquetas do contato (as do contato, não as das conversas)."""
        url = f"{self._account_path}/contacts/{contact_id}/labels"
        resp = await self._request("GET", url)
        data = resp.json()
        labels = data.get("payload", []) if isinstance(data, dict) else data
        return [str(lbl) for lbl in labels or []]

    async def list_labels(self) -> list[dict]:
        """Etiquetas da conta, com a cor de cada uma."""
        url = f"{self._account_path}/labels"
        resp = await self._request("GET", url)
        data = resp.json()
        return data.get("payload", []) if isinstance(data, dict) else data

    async def get_contact(self, contact_id: int) -> dict:
        url = f"{self._account_path}/contacts/{contact_id}"
        resp = await self._request("GET", url)
        return resp.json()

    async def get_contact_conversations(self, contact_id: int) -> list:
        url = f"{self._account_path}/contacts/{contact_id}/conversations"
        resp = await self._request("GET", url)
        data = resp.json()
        return data.get("payload", data) if isinstance(data, dict) else data

    async def update_contact_custom_attributes(
        self, contact_id: int, attributes: dict
    ) -> dict:
        url = f"{self._account_path}/contacts/{contact_id}"
        resp = await self._request("PATCH", url, json={"custom_attributes": attributes})
        return resp.json()

    async def safe_update_contact_custom_attributes(
        self, contact_id: int, attributes: dict, *, skip_read: bool = False
    ) -> dict:
        if skip_read:
            return await self.update_contact_custom_attributes(contact_id, attributes)

        try:
            contact = await self.get_contact(contact_id)
            contact_data = contact.get("payload", contact)
            existing = contact_data.get("custom_attributes") or {}
        except Exception:
            logger.warning(
                "Could not read existing attributes for contact %s, "
                "sending partial update",
                contact_id,
            )
            return await self.update_contact_custom_attributes(contact_id, attributes)

        merged = {**existing, **attributes}
        return await self.update_contact_custom_attributes(contact_id, merged)

    async def remove_contact_custom_attributes(
        self, contact_id: int, keys: list
    ) -> dict:
        """Apaga atributos personalizados do contato (não só deixa vazio)."""
        url = f"{self._account_path}/contacts/{contact_id}/destroy_custom_attributes"
        resp = await self._request("POST", url, json={"custom_attributes": keys})
        return resp.json() if resp.content else {}

    async def get_custom_attribute_definitions(self) -> list:
        now = time.time()
        account_id = self.account_id
        cached = self._attr_cache.get(account_id)
        if cached and (now - cached[0]) < _ATTR_CACHE_TTL:
            return cached[1]
        url = f"{self._account_path}/custom_attribute_definitions"
        resp = await self._request("GET", url)
        definitions = resp.json()
        self._attr_cache[account_id] = (now, definitions)
        return definitions

    async def update_custom_attribute_values(
        self, definition_id: int, values: list[str]
    ) -> dict:
        """Regrava a lista de valores de um atributo do tipo lista (ex.: a ordem das etapas).

        O Chatwoot só aceita isso de um usuário administrador da conta.
        """
        url = f"{self._account_path}/custom_attribute_definitions/{definition_id}"
        resp = await self._request(
            "PATCH",
            url,
            json={"custom_attribute_definition": {"attribute_values": values}},
        )
        self.clear_attribute_cache()
        return resp.json() if resp.content else {}

    def clear_attribute_cache(self) -> None:
        self._attr_cache.pop(self.account_id, None)

    async def update_custom_attributes(
        self, conversation_id: int, attributes: dict
    ) -> dict:
        url = f"{self._account_path}/conversations/{conversation_id}/custom_attributes"
        resp = await self._request("POST", url, json={"custom_attributes": attributes})
        return resp.json()

    async def get_conversation(self, conversation_id: int) -> dict:
        url = f"{self._account_path}/conversations/{conversation_id}"
        resp = await self._request("GET", url)
        return resp.json()

    async def safe_update_custom_attributes(
        self, conversation_id: int, attributes: dict, *, skip_read: bool = False
    ) -> dict:
        if skip_read:
            return await self.update_custom_attributes(conversation_id, attributes)

        try:
            conv = await self.get_conversation(conversation_id)
            existing = conv.get("custom_attributes") or {}
        except Exception:
            logger.warning(
                "Could not read existing attributes for conv %s, "
                "sending partial update",
                conversation_id,
            )
            return await self.update_custom_attributes(conversation_id, attributes)

        merged = {**existing, **attributes}
        return await self.update_custom_attributes(conversation_id, merged)


chatwoot_client = ChatwootClient()
