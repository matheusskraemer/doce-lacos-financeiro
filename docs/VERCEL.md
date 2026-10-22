# Implantação no Vercel + Neon

O sistema roda no Vercel como uma única função Python (Flask), com o banco PostgreSQL no Neon, conectado pela integração oficial do Marketplace do Vercel.

## Como o projeto está preparado

| Item | Onde | O que faz |
|---|---|---|
| Ponto de entrada | `wsgi.py` + `pyproject.toml` (`[tool.vercel] entrypoint = "wsgi:app"`) | O Vercel carrega o objeto `app` |
| Python | `.python-version` (3.13) e `requires-python` | Versão fixa |
| Dependências | `pyproject.toml` (`[project].dependencies`) e `requirements.txt` | Mantenha as duas listas iguais |
| Arquivos estáticos | `public/static/` | Servidos pela CDN do Vercel em `/static/...`; localmente o Flask serve a mesma pasta |
| Região | `vercel.json` → `"regions": ["gru1"]` (São Paulo) | Crie o banco do Neon também em São Paulo (`aws-sa-east-1`) |
| Banco no build | `scripts/vercel_build.py` (`[tool.vercel.scripts] build`) | **Só em deploy de produção**: migra o banco pela conexão direta (`DATABASE_URL_UNPOOLED`), cria categorias padrão e o primeiro usuário |
| Conexões | `app/config.py` | Pool de 1 conexão por instância; sem *prepared statements* no endereço `-pooler` do Neon |
| Verificação | `GET /healthz` | Responde o commit publicado (`VERCEL_GIT_COMMIT_SHA`) e a versão do banco |

Deploys de pré-visualização (branches e pull requests) **nunca** alteram o banco de produção: o script de build sai sem fazer nada quando `VERCEL_ENV` não é `production`.

## Passo a passo (primeira vez)

### 1. Antes de sair do Render: baixe a cópia dos dados
No sistema atual: **Configurações → Backup e conferência → Baixar cópia**. Guarde o arquivo `.json`.

### 2. Crie o projeto no Vercel
1. Entre em https://vercel.com com a conta do GitHub.
2. **Add New → Project** → importe `matheusskraemer/doce-lacos-financeiro`.
3. O Vercel deve detectar **Flask**. Não altere Build Command nem Output Directory.
4. **Ainda não clique em Deploy** se a tela já pedir variáveis: siga para o passo 3 primeiro (ou deixe o primeiro deploy falhar, sem problema).

### 3. Conecte o banco (Neon)
1. No projeto: **Storage → Create Database → Neon** (ou **Marketplace → Neon**).
2. Região: **São Paulo (aws-sa-east-1)**. Plano: o gratuito serve para começar.
3. Ambientes: marque **Production**. Para Preview, deixe desmarcado ou habilite o "branching" do Neon (um banco de teste por pré-visualização). Nunca aponte Preview para o banco de produção.
4. A integração cria sozinha `DATABASE_URL` (com pool) e `DATABASE_URL_UNPOOLED` (direta).

### 4. Variáveis de ambiente (Settings → Environment Variables, ambiente Production)

| Nome | Valor |
|---|---|
| `SECRET_KEY` | Texto aleatório de 64 caracteres (gerador de senhas do seu cofre de senhas, só letras e números) |
| `INITIAL_ADMIN_USERNAME` | Seu login, em minúsculas (ex.: `matheus`) |
| `INITIAL_ADMIN_PASSWORD` | Senha provisória forte (10+ caracteres, maiúsculas, minúsculas e números) |

Não é preciso definir `APP_ENV` (o padrão já é produção).

### 5. Publique
**Deployments → Redeploy** (ou um novo push na `main`). No log do build devem aparecer:
```
Aplicando migrações do banco…
Usuário “…” criado.
Banco pronto (versão d8d0affc0cdc).
```
Confira `https://<seu-projeto>.vercel.app/healthz`: deve mostrar `"schema": "d8d0affc0cdc"` e o commit publicado.

### 6. Traga os dados do Render
1. Entre com o usuário provisório.
2. **Configurações → Backup e conferência → Restaurar cópia**: envie o `.json` do passo 1 e confirme sua senha.
3. Você é desconectado; entre com o usuário e a senha que valiam no Render.
4. Rode **Rodar conferência** e compare o saldo de cada conta com o que aparecia no Render.

A restauração pela tela só existe enquanto o banco novo não tem nenhum lançamento, e exige senha. Aceita cópias da versão anterior do sistema.

### 7. Limpeza
- **Vercel → Environment Variables**: apague `INITIAL_ADMIN_PASSWORD`.
- **Render**: com tudo conferido no Vercel, apague o web service e o banco no Render para parar a cobrança. Antes, baixe mais uma cópia por garantia.
- Domínio próprio (opcional): **Vercel → Settings → Domains**.

## Atualizações
Cada push na `main` gera um deploy de produção; as migrações rodam no build. As migrações devem continuar aditivas (sem apagar colunas usadas pela versão em produção), porque o banco é migrado antes de a nova versão entrar no ar.

## Limites do Vercel que afetam o sistema
- Requisições e respostas de até **4,5 MB**: o download e a restauração da cópia completa ficam limitados a esse tamanho (hoje, poucas centenas de KB). Se um dia passar disso, use `scripts/backup_pg.sh` com a conexão direta do Neon.
- Tempo máximo por requisição configurado em 60 s (`vercel.json`); nenhuma tela chega perto disso.
- **Plano Hobby (gratuito): pelas regras do Vercel, só para uso pessoal e não comercial.** Para o uso comercial da Doce Laços, o plano adequado é o Pro.

## Backup no Neon
O Neon guarda histórico para restauração a um ponto no tempo (6 horas no plano gratuito; até 7 dias no Launch). Continue baixando a cópia semanal pela tela (ver `docs/BACKUP.md`). Para o `scripts/backup_pg.sh`, use o valor de `DATABASE_URL_UNPOOLED`.
