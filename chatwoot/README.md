# Personalizações do Chatwoot

O script [`apply.rb`](apply.rb) aplica no Chatwoot, **sem fork**, tudo o que usamos com o Turvia:

- item **Kanban** na barra lateral, que abre este kanban em tela cheia
- **título** da aba, **nome da marca** e **logos**
- **ícone da aba** (favicon), inclusive a versão de "mensagens não lidas"

Tudo fica salvo no banco do Chatwoot e **continua valendo depois das atualizações**.
Você pode rodar o script quantas vezes quiser: ele substitui o que aplicou antes, sem duplicar.

## Como aplicar (instalação nova ou mudança)

### 1. Variáveis no serviço **web** do Chatwoot (Railway → Variables)

Defina só as que quiser mudar. As que você não definir continuam como estão.

| Variável | Exemplo | Para quê |
|---|---|---|
| `CW_KANBAN_URL` | `https://meu-kanban.up.railway.app/kanban` | item Kanban na barra lateral |
| `CW_KANBAN_LABEL` | `Kanban` | texto do item (padrão: Kanban) |
| `CW_KANBAN_APP_TITLE` | `Funil (Kanban)` | título do Dashboard App que o Turvia cria em cada conta (padrão: Funil (Kanban)) |
| `CW_INSTALLATION_NAME` | `Turvia CRM` | título da aba / nome do sistema |
| `CW_BRAND_NAME` | `Turvia` | nome da marca |
| `CW_BRAND_URL` | `https://turvia.com.br` | site da marca |
| `CW_LOGO` | `https://.../logo.png` | logo no tema claro (tela de login) |
| `CW_LOGO_DARK` | `https://.../logo-escuro.png` | logo no tema escuro |
| `CW_LOGO_THUMBNAIL` | `https://.../icone.png` | ícone quadrado do canto do menu |
| `CW_FAVICON` | `https://.../favicon.png` | ícone da aba (PNG quadrado) |
| `CW_FAVICON_BADGE` | `https://.../favicon-badge.png` | ícone da aba com notificação (padrão: igual ao `CW_FAVICON`) |

As imagens precisam estar em um endereço público.

### 2. Rodar no console do serviço **web** do Chatwoot

```bash
cd /app && bundle exec rails runner 'require "open-uri"; eval(URI.open("https://raw.githubusercontent.com/gmsarates/Chatwoot-Kanban/main/chatwoot/apply.rb").read)'
```

Ele mostra o que mudou. Depois, recarregue o Chatwoot com **Ctrl+Shift+R**.

> O comando baixa o script direto deste repositório (precisa estar público). Se o repositório
> for privado, copie o `apply.rb` para o container (por exemplo com `cat > /tmp/apply.rb <<'EOF' ... EOF`)
> e rode `cd /app && bundle exec rails runner /tmp/apply.rb`.

## Remover

Para tirar o item do Kanban e o favicon (título e logos continuam como estão):

```bash
cd /app && CW_UNINSTALL=1 bundle exec rails runner 'require "open-uri"; eval(URI.open("https://raw.githubusercontent.com/gmsarates/Chatwoot-Kanban/main/chatwoot/apply.rb").read)'
```

## Várias contas (agências do Turvia)

O item da barra lateral descobre a conta aberta (`/app/accounts/<id>`) e abre o endereço do Dashboard App
"Funil (Kanban)" dessa conta, que o Turvia cria com `?conta=&chave=` da agência. Na conta que não tem esse app
(ex.: a sua, de prospecção), abre o `CW_KANBAN_URL`, que usa a conta padrão do kanban (`CHATWOOT_ACCOUNT_ID`).

## Observações

- O console precisa ser o do serviço **web** do Chatwoot (o que tem a pasta `/app`), não o do Postgres, Redis ou Sidekiq.
- Use a imagem **Community Edition** do Chatwoot (tag terminada em `-ce`).
- Alguns textos internos do Chatwoot (certas mensagens do sistema e e-mails) continuam dizendo "Chatwoot"; tirar todos exige fork ou a edição paga.
- Se você já tinha colado à mão o script do kanban ou do favicon no `DASHBOARD_SCRIPTS`, o `apply.rb` substitui essas versões; outros scripts que estejam lá são mantidos.
