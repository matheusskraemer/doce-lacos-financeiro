# Encomendas

## Fluxo

| Etapa | Pode ir para |
|---|---|
| Novo pedido | Aguardando confirmação, Confirmado, Cancelado |
| Aguardando confirmação | Novo pedido, Confirmado, Cancelado |
| Confirmado | Aguardando confirmação, Em produção, Pronto para entrega, Cancelado |
| Em produção | Confirmado, Pronto para entrega, Cancelado |
| Pronto para entrega | Em produção, Saiu para entrega (só entrega/transportadora), Entregue, Cancelado |
| Saiu para entrega | Pronto para entrega, Entregue, Cancelado |
| Entregue / Cancelado | Etapas finais |

As transições são validadas no servidor (o Kanban só oferece o que é permitido, mas quem decide é o backend). Nada muda de etapa sozinho: chegar a data não marca como entregue. Cancelar exige motivo e confirmação.

**Pendência** é um indicador separado da etapa (ex.: "aguardando foto da cor"): aparece no cartão e nos alertas sem alterar o status.

**Adaptar as etapas:** edite `ORDER_STATUSES` e `ORDER_TRANSITIONS` em `app/models.py`. As chaves gravadas no banco são as da primeira coluna; renomear só o rótulo não exige migração.

## Atraso

Atrasada = encomenda em aberto cujo prazo já passou: data prometida + horário combinado; sem horário, vale até 23:59 do dia (fuso America/Sao_Paulo). "Prazo próximo" usa a antecedência configurada em Configurações → Empresa (padrão 2 dias). Atraso é sempre indicado com texto e ícone, não só com cor.

## Integração com o financeiro

- A encomenda gera **uma** venda vinculada (`orders.sale_id`, único) ao ser confirmada ou ao receber o primeiro pagamento, o que vier antes. A venda é a mesma usada pelo módulo de Vendas: aparece em Vendas, Contas a receber, fluxo de caixa e relatórios, sem lançamento paralelo.
- Total = Σ(quantidade × valor unitário − desconto do item) − desconto do pedido.
- Recebido = liquidações − estornos dos títulos dessa venda. Saldo pendente = em aberto dos títulos.
- Pagamentos (sinal, parcial, quitação) são registrados explicitamente na tela da encomenda; mudar etapa não registra caixa; entregue não significa pago.
- Itens e valores só mudam enquanto não houver nenhum movimento de caixa. Sem movimentos, a venda antiga é cancelada (com registro) e outra é gerada com os novos valores.
- Cancelar com valores recebidos exige registrar o reembolso: o sistema cria estornos com a data informada. Recebimentos originais e estornos permanecem no histórico.
- A venda vinculada não pode ser cancelada pela tela de Vendas, só pela encomenda.
- Nenhuma compra ou despesa é criada a partir da encomenda.

## Notificações (fase futura)

Hoje os alertas aparecem dentro da aplicação (menu, painel de encomendas e visão geral). Envio automático por WhatsApp, SMS ou e-mail não foi implementado. Antes, avaliar:

- **WhatsApp Business Platform (Meta)**: cobrança por conversa, modelos de mensagem aprovados, número dedicado e consentimento do cliente.
- **E-mail transacional** (ex.: provedores SMTP com plano gratuito limitado): exige domínio próprio com SPF/DKIM.
- **SMS**: custo por mensagem.
- Em todos os casos: consentimento e base legal na LGPD, opção de descadastro, e uma tarefa agendada (o Render oferece cron jobs pagos à parte).
