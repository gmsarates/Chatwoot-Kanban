# Chatwoot-Kanban

[![CI](https://github.com/CrisAlva1414/Chatwoot-Kanban/actions/workflows/test.yml/badge.svg)](https://github.com/CrisAlva1414/Chatwoot-Kanban/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.139-teal.svg)](https://fastapi.tiangolo.com)

Kanban board and task management integration for [Chatwoot](https://www.chatwoot.com/) CRM.  
Embeddable dashboard app with drag-and-drop pipeline, bidirectional custom attributes sync, and audit logging.

## Features

- **Kanban board** — Visual drag-and-drop pipeline using Chatwoot's own custom attribute values as stages. Each column maps to a `pipeline_01_etapas` value. Move contacts between stages and see changes reflected in Chatwoot instantly.
- **Bidirectional sync** — The board reads and writes Chatwoot custom attributes directly. When a contact's stage or task data changes in Chatwoot, webhooks keep the board in sync. When you create/edit/close tasks from the board, custom attributes are pushed back to Chatwoot.
- **Task management** — One active task per contact. States flow automatically: `tarea_activa` → `tarea_hoy` → `tarea_vencida` → `tarea_cerrada`. A cron endpoint triggers daily transitions.
- **Dashboard** — Agent performance stats: tasks created, closed, overwritten, and successful syncs. Full audit history with action attribution.
- **Webhook receiver** — Handles `contact_updated` and `conversation_updated` events with HMAC signature verification and idempotency. Synchronizes task data from Chatwoot custom attributes back to the local database.
- **Custom attributes integration** — Uses two contact-scoped custom attributes in Chatwoot: `kanban_view_mensaje` (task description) and `kanban_view_fecha_termino` (due date). The pipeline stage attribute (`pipeline_01_etapas`) is configurable.
- **Dark mode** — Light/dark theme following system preference.
- **Security** — Optional Cloudflare Access authentication (header-based). Read-only container with no-new-privileges.

## Demo

Tablero y dashboard listos para ver sin necesidad de una instancia real de
Chatwoot. Los datos (contactos, etapas, tareas y auditoría) son simulados y
se generan con fechas relativas a *hoy*, así que el tablero siempre muestra
tareas activas, que vencen hoy y vencidas.

<p align="center">
  <img src="docs/demo/kanban-light.png" alt="Tablero Kanban (claro)" width="820">
  <br>
  <em>Tablero Kanban — arrastra contactos entre etapas con sync instantáneo</em>
</p>

<p align="center">
  <img src="docs/demo/kanban-task-modal.png" alt="Modal de tarea" width="820">
  <br>
  <em>Modal de tarea — crear, editar o cerrar tareas con sync bidireccional a Chatwoot</em>
</p>

| Dashboard (claro) | Dashboard (oscuro) |
|:--:|:--:|
| <img src="docs/demo/dashboard-light.png" alt="Dashboard claro" width="400"> | <img src="docs/demo/dashboard-dark.png" alt="Dashboard oscuro" width="400"> |

> El mock de la API de Chatwoot y los datos de ejemplo (en `demo/` y
> `docker-compose.demo.yml`) son una **herramienta local de desarrollo** que
> no se publica en el repositorio. Para levantar el stack y regenerar estas
> capturas, consulta [docs/demo/README.md](docs/demo/README.md).

## How it works

```
┌─────────────────────────────┐      ┌──────────────────────┐
│  Chatwoot Dashboard App     │      │  chatwoot-kanban     │
│  (iframe)                   │      │  (FastAPI)           │
│                             │      │                      │
│  ┌───────────────┐          │ REST │  ┌────────────────┐  │
│  │ Kanban Board  │◄─────────┼──────┼──┤ /kanban/board  │  │
│  │ Drag & Drop   │          │      │  │ /kanban/config │  │
│  │ Task Modal    │          │      │  └───────┬────────┘  │
│  └───────────────┘          │      │          │           │
│                             │      │  ┌───────▼────────┐  │
│  Chatwoot Custom Attributes │◄─────┼──┤ Chatwoot API   │  │
│  pipeline_01_etapas         │ sync │  │ Client (httpx) │  │
│  kanban_view_mensaje        │──────┼──►                │  │
│  kanban_view_fecha_termino  │      │  └───────┬────────┘  │
│                             │      │          │           │
│  Webhooks ──────────────────┼──────┼──► ┌─────▼────────┐  │
│                             │      │    │ PostgreSQL 16 │  │
└─────────────────────────────┘      │    │ tasks, audit  │  │
                                     │    └──────────────┘  │
                                     └──────────────────────┘
```

The board serves as Chatwoot's **Dashboard App** — an iframe embedded in the agent's conversation view. All logic runs server-side; the frontend is a vanilla JavaScript SPA with optimistic updates and adaptive polling (15s–60s).

### Sync flow

1. **Board load** → Fetches contacts filtered by pipeline stage from Chatwoot API → batch syncs task custom attributes into local DB → renders cards with tasks.
2. **Stage move** → Writes new `pipeline_01_etapas` value to Chatwoot contact → invalidates board cache.
3. **Task create/edit/close** → Upserts in local DB → background task pushes `kanban_view_mensaje` and `kanban_view_fecha_termino` to Chatwoot via `safe_update_contact_custom_attributes` (merge-read-write pattern to avoid overwriting other attributes).
4. **Webhook received** → HMAC verification → deduplicate by event ID → sync task data from Chatwoot custom attributes into local DB.
5. **Cron tick** → Transitions tasks through lifecycle stages: `tarea_activa` → `tarea_hoy` (due today), `tarea_hoy` → `tarea_vencida` (past due), then syncs pending changes to Chatwoot.

## Quick Start

### Prerequisites

- Docker & Docker Compose
- A Chatwoot instance with API access
- A bot agent in Chatwoot with an API token

### Setup

1. **Clone the repo**

   ```bash
   git clone https://github.com/CrisAlva1414/Chatwoot-Kanban.git
   cd chatwoot-kanban
   ```

2. **Configure environment**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` with your Chatwoot credentials:

   ```env
   CHATWOOT_BASE_URL=https://your-instance.chatwoot.com
   CHATWOOT_ACCOUNT_ID=1
   CHATWOOT_BOT_TOKEN=your_bot_token
   CHATWOOT_BOT_EMAIL=bot@example.com
   CHATWOOT_FRONTEND_URL=https://your-instance.chatwoot.com
   POSTGRES_PASSWORD=your_secure_password
   ```

3. **Create an external Docker network**

   ```bash
   docker network create chatwoot_shared
   ```

4. **Start the services**

   ```bash
   docker compose up -d
   ```

5. **Install as Dashboard App in Chatwoot**

   In Chatwoot admin: *Settings → Applications → Dashboard Apps → Add*  
   URL: `https://your-domain/kanban`

6. **Configure webhooks** (optional, for real-time sync)

   In Chatwoot admin: *Settings → Webhooks → Add*  
   - `contact_updated` → `https://your-domain/webhooks/contact-updated`  
   - `conversation_updated` → `https://your-domain/webhooks/conversation-updated`

### Chatwoot Custom Attributes

Create these **contact** custom attributes in Chatwoot (*Settings → Custom Attributes → Add*):

| Attribute Key              | Type        | Description          |
|----------------------------|-------------|----------------------|
| `pipeline_01_etapas`       | Text / List | Pipeline stage       |
| `kanban_view_mensaje`      | Text        | Task description     |
| `kanban_view_fecha_termino`| Text/Date   | Task due date        |

> **Note:** The pipeline attribute key is configurable via `PIPELINE_ATTR_KEY` in `app/routers/kanban.py:37`.

**Ordem das colunas:** as colunas seguem a ordem dos valores do `pipeline_01_etapas` (atributo do tipo *Lista*). O botão **Ordenar etapas** do quadro regrava essa lista no Chatwoot, então a nova ordem vale para todos. Para isso o usuário dono do `CHATWOOT_BOT_TOKEN` precisa ser **administrador** da conta (o Chatwoot só deixa admin alterar atributos personalizados); sem isso o quadro avisa e não muda nada.

### Cron

To enable daily stage transitions, call the cron endpoint daily (e.g., via external cron job):

```bash
curl -X POST https://your-domain/kanban/cron/tick
```

## Várias contas (Turvia)

Com `KANBAN_SECRET` configurado, o mesmo Kanban atende várias contas do Chatwoot (uma
por agência do Turvia), cada uma com os próprios dados:

- O Turvia cadastra a conta em `PUT /contas/{account_id}` (cabeçalho
  `X-Kanban-Segredo: <KANBAN_SECRET>`, corpo `{"token": "...", "nome": "..."}`) e recebe a
  `chave` da conta (HMAC do id com o segredo).
- O Dashboard App da conta aponta para `/kanban?conta=<id>&chave=<chave>`; a página
  repassa os dois em toda chamada (`X-Kanban-Conta`/`X-Kanban-Chave`). Trocar o número da
  conta na URL não abre a de outra agência.
- Os webhooks da conta usam a mesma `?conta=&chave=` na URL.
- `POST /kanban/cron/tick` sem conta roda para todas as contas cadastradas.
- Tarefas, histórico e eventos ganharam a coluna `account_id` (o que já existia fica com
  `CHATWOOT_ACCOUNT_ID`). Sem `conta` na requisição vale a conta padrão das variáveis.

### Deploy no Railway (Turvia)

O roteiro completo do CRM (Chatwoot, Evolution, relay e Turvia) está no repositório do Turvia, em
`infra/README.md`, seção 8. Para este serviço:

1. Novo serviço a partir deste repositório (o Railway usa o `Dockerfile`) e um **Postgres próprio**. As tabelas
   (inclusive `contas` e as colunas `account_id`) são criadas ao subir.
2. Variáveis:

   | Variável | Valor |
   |---|---|
   | `CHATWOOT_BASE_URL` | Chatwoot visto pelo Kanban (a rede privada serve: `http://<web>.railway.internal:<porta>`) |
   | `CHATWOOT_FRONTEND_URL` | endereço público do Chatwoot (links para os contatos) |
   | `DATABASE_URL` | Postgres do Kanban |
   | `KANBAN_SECRET` | `openssl rand -hex 32`, o mesmo valor no Turvia |
   | `PORT` | `8000` |
   | `CHATWOOT_ACCOUNT_ID` / `CHATWOOT_BOT_TOKEN` | **só** se quiser o Kanban numa conta sua fora do Turvia (veja o alerta abaixo) |

3. Gere o domínio público. No Turvia, `KANBAN_URL` é **a raiz** desse domínio (sem `/kanban`).
4. O cron das tarefas (`POST /kanban/cron/tick`) é chamado pelo `sistur-worker` de hora em hora (tarefa
   `kanban-tick`, com `KANBAN_URL` nas variáveis do worker). Sem ele, as tarefas não passam para "hoje"/"vencida".
5. Depois, rode o `chatwoot/apply.rb` no Chatwoot (item Kanban na barra lateral): `chatwoot/README.md`.

Se o endereço, o `KANBAN_SECRET` ou o banco deste serviço mudarem, as contas já instaladas precisam ser
reinstaladas pelo Turvia (`infra/README.md` do Turvia, seção 8.7).

> **Segurança: conta padrão.** Requisições sem `conta`/`chave` usam a conta padrão (`CHATWOOT_ACCOUNT_ID` +
> `CHATWOOT_BOT_TOKEN`) **sem nenhuma autenticação** (o projeto original contava com Cloudflare Access na frente).
> Com a conta padrão configurada e o domínio público, qualquer pessoa que souber o endereço vê o quadro, os contatos
> e as rotas `/debug` dessa conta. Sem conta padrão, essas requisições respondem 401. As contas do Turvia não são
> afetadas: elas sempre exigem a `chave`.

## Architecture

| Layer    | Technology                          |
|----------|-------------------------------------|
| Runtime  | Python 3.12                         |
| Framework| FastAPI                             |
| Client   | httpx (Chatwoot REST API)           |
| Database | PostgreSQL 16 via asyncpg           |
| Lint     | Ruff (line-length 88, strict types) |
| CI/CD    | GitHub Actions (lint + pytest + build) |
| Deploy   | Docker → GHCR                        |

## API Endpoints

| Method | Path                                  | Description                     |
|--------|---------------------------------------|---------------------------------|
| GET    | `/health`                             | Health check                    |
| GET    | `/kanban`                             | Kanban board (HTML)             |
| GET    | `/kanban/dashboard`                   | Dashboard page (HTML)           |
| GET    | `/kanban/config`                      | Pipeline config & Chatwoot URLs |
| GET    | `/kanban/board`                       | Board data with task sync       |
| PATCH  | `/kanban/board/{contact_id}/stage`    | Move contact to stage           |
| PUT    | `/kanban/stages/order`                | Reorder stages (columns)        |
| GET    | `/kanban/contacts/available`          | Ids of contacts not on the board|
| POST   | `/kanban/board/bulk-stage`            | Put up to 50 contacts in a stage|
| POST   | `/kanban/board/bulk-remove`           | Take up to 50 contacts off board|
| POST   | `/kanban/tasks`                       | Create task                     |
| PATCH  | `/kanban/tasks/{task_id}`             | Edit task                       |
| PATCH  | `/kanban/tasks/{task_id}/close`       | Close task                      |
| GET    | `/kanban/tasks`                       | Get tasks (by contact_id)       |
| POST   | `/kanban/cron/tick`                   | Trigger daily transitions       |
| GET    | `/kanban/stats`                       | Agent statistics                |
| GET    | `/kanban/stats/history`               | Audit history                   |
| POST   | `/webhooks/contact-updated`           | Chatwoot webhook                |
| POST   | `/webhooks/conversation-updated`      | Chatwoot webhook                |

Full API documentation available at `/docs` when running locally.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
ruff check app/ tests/
ruff format --check app/ tests/
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed setup and conventions.

## License

[MIT](LICENSE) — same license as Chatwoot itself.

This project was originally developed as an internal tool and later released as open source. See [ADR-017](docs/adr/017-desvinculacion-y-apertura-open-source.md) for the transition history.
