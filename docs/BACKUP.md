# Backup, exportação e recuperação

## O que existe

| Camada | Quem faz | Cobre | Onde fica |
|---|---|---|---|
| Backups automáticos do PostgreSQL | Hospedagem | Banco inteiro, recuperação a um ponto no tempo | No provedor |
| `scripts/backup_pg.sh` | Você (manual ou agendado) | Banco inteiro (pg_dump), criptografado com GPG AES-256 | Fora do provedor (seu computador/nuvem pessoal) |
| Cópia completa JSON | Você, pela tela ou `flask backup-json` | Todos os registros da aplicação, com soma de verificação SHA-256 | Onde você salvar |
| Exportação CSV/Excel | Você, pelos relatórios | Só o relatório filtrado | Análise; **não é backup** |

## O que a hospedagem oferece (Render)

Segundo a página de preços do Render (consultada em out/2026): bancos Postgres pagos incluem backups lógicos, e a recuperação a um ponto no tempo depende do plano do workspace (3 dias no Hobby, 7 dias nos planos Pro e superiores). O banco gratuito não deve ser usado para estes dados.

**Isso não foi verificado em um ambiente real**, porque a conta ainda não existe. Depois de criar o banco, confira no painel do Postgres → **Recovery** se os backups aparecem e qual a retenção.

## Rotina recomendada

- **Semanal:** Configurações → Backup → Baixar cópia (JSON). Guarde em pasta criptografada ou cofre de senhas. O arquivo tem todos os dados financeiros e o hash da senha.
- **Mensal (fechamento do mês):** `scripts/backup_pg.sh` a partir do seu computador, com a URL externa do banco.
- **A cada 3 meses:** teste de restauração (abaixo).

## Backup com pg_dump

```bash
export DATABASE_URL='postgresql://usuario:senha@host:5432/docelacos'   # "External Database URL" do provedor
export BACKUP_PASSPHRASE='frase longa guardada no cofre de senhas'
./scripts/backup_pg.sh ./backups
```

Requer `pg_dump`/`pg_restore` na mesma versão principal do servidor (ou mais nova) e `gpg`. O script confere se o dump é legível antes de criptografar e apaga o arquivo sem criptografia. No Render, o acesso externo ao banco vem bloqueado no `render.yaml` (`ipAllowList: []`): para rodar do seu computador, libere temporariamente o seu IP no painel e remova depois.

## Restaurar

### A partir do pg_dump

```bash
createdb docelacos_restaurado                      # ou um banco novo no provedor
export TARGET_DATABASE_URL='postgresql://…/docelacos_restaurado'
export BACKUP_PASSPHRASE='…'
./scripts/restore_pg.sh backups/docelacos_AAAAMMDD-HHMMSS.dump.gpg
DATABASE_URL=$TARGET_DATABASE_URL flask --app wsgi check
```

### A partir da cópia JSON

```bash
export DATABASE_URL='postgresql://…/banco_novo'
flask --app wsgi db upgrade                         # cria as tabelas vazias
flask --app wsgi restore-json copia.json            # recusa se o banco já tiver dados
flask --app wsgi check
```

`--force` substitui dados existentes; use só se tiver certeza. Arquivos alterados ou corrompidos são recusados pela soma de verificação. A restauração é uma única transação: ou entra tudo, ou nada.

## Em caso de falha

1. **Aplicação fora do ar, banco intacto:** veja os logs no painel; faça um novo deploy do último commit que funcionava.
2. **Dados apagados ou corrompidos por engano:** use a recuperação a um ponto no tempo do provedor, escolhendo um horário anterior ao problema (o provedor cria um banco novo; aponte `DATABASE_URL` para ele).
3. **Perda do provedor ou da conta:** crie banco novo em qualquer provedor PostgreSQL, restaure o último `backup_pg.sh` ou a última cópia JSON, publique a aplicação apontando para ele.
4. Depois de qualquer restauração: `flask check` e confira o saldo de cada conta com o extrato bancário.

## Verificação feita no desenvolvimento

Executado em 21/10/2026 contra PostgreSQL 16 local com dados de teste:
- `backup_pg.sh` → `restore_pg.sh` em banco vazio: mesma quantidade e soma de liquidações, `flask check` sem inconsistências.
- Teste automatizado `test_backup_e_restauracao`: cópia JSON restaurada em outro banco, saldos e contas em aberto iguais, login com a senha original, sequências ajustadas, arquivo adulterado recusado.
