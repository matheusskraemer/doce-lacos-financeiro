# Regras financeiras

Todas as regras abaixo estão implementadas em `app/services/finance.py` (escrita) e `app/services/metrics.py` (leitura) e cobertas por testes em `tests/`.

## Conceitos

| Conceito | O que é | Mexe no saldo? |
|---|---|---|
| Título | Compromisso a receber ou a pagar (parcela de venda, despesa, receita, parcela de compra) | Não |
| Liquidação | Recebimento ou pagamento efetivo de um título, total ou parcial, em uma conta | Sim |
| Estorno | Liquidação inversa ligada à original; devolve o valor ao "em aberto" | Sim (desfaz) |
| Transferência | Dinheiro entre contas próprias | Só os saldos das contas, não o consolidado nem o resultado |
| Venda / Compra | Documento de origem: guarda itens, bruto, desconto e gera os títulos | Não |

## Fórmulas

- **Saldo de uma conta** = saldo inicial + Σ(liquidações de recebimento − estornos) − Σ(liquidações de pagamento − estornos) + transferências recebidas − transferências enviadas, até a data de referência.
- **Saldo atual (consolidado)** = soma dos saldos das contas marcadas "somar no saldo consolidado".
- **Recebido / Pago no período** = liquidações líquidas de estornos com data efetiva no período, apenas contas no consolidado.
- **Resultado de caixa** = recebido − pago. Não é lucro.
- **Vendas do período** = Σ (bruto − desconto) das vendas não canceladas, pela data da venda. Canceladas aparecem à parte.
- **Em aberto de um título** = valor − valor líquido liquidado; título cancelado = 0.
- **Saldo projetado** = saldo atual + a receber em aberto − a pagar em aberto com vencimento até o horizonte (padrão 30 dias, ajustável). Inclui títulos vencidos e não liquidados, pois ainda são esperados.
- **Compras de mercadorias (indicador)** = total das compras registradas no período, pela data da compra. A saída de caixa aparece só quando a compra é paga, em "Pago".

## Competência × caixa

Cada título tem data de competência (quando o fato aconteceu) e vencimento; cada liquidação tem a data efetiva.

- Relatórios de caixa (recebimentos, pagamentos, fluxo, resultado de caixa) usam a data efetiva.
- Relatórios por competência (despesas por categoria base competência, resultado gerencial) usam a competência.
- O "resultado gerencial simplificado" considera compras de mercadorias do período inteiras, não o custo das mercadorias vendidas, e ignora tributos sobre o lucro. Por isso nunca é apresentado como lucro.

## Integridade

- Valores `NUMERIC(14,2)`; cálculos em `Decimal` com arredondamento comercial.
- Parcelas somam exatamente o total (centavos de diferença na primeira).
- Liquidar bloqueia o título (`SELECT … FOR UPDATE`) e recusa valor acima do saldo em aberto.
- `request_key` único por formulário: reenvio ou duplo clique não duplica venda, despesa, compra, liquidação ou transferência.
- `reverses_id` único: cada lançamento só pode ser estornado uma vez; estorno de estorno é recusado.
- Cancelar um título com valores liquidados exige estornar (opção no próprio formulário).
- Exclusão definitiva só para lançamentos sem nenhuma movimentação de caixa; contas e categorias com histórico são desativadas, não apagadas.
- Valores de vendas e compras não são editáveis: corrige-se cancelando e registrando de novo, mantendo o rastro.
- Restrições no banco (CHECK) reforçam as regras: valores positivos, líquido = bruto − desconto, transferência entre contas diferentes, estorno sempre ligado ao original.
- Registro de alterações (`audit_log`) para criação, edição, liquidação, estorno, cancelamento, exclusão, backup e troca de senha.
- `flask check` / Configurações → Backup → Conferência: confere tudo isso e reconcilia o saldo de cada conta lançamento a lançamento.
