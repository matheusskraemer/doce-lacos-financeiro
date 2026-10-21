# Hospedagem: alternativas e custos

Valores em dólar, sem impostos, conforme as páginas de preços consultadas em outubro de 2026. Confira antes de contratar; preços mudam e a cobrança em real depende do câmbio e do IOF do cartão.

| Opção | Aplicação | Banco | Total aproximado/mês | Prós | Contras |
|---|---|---|---|---|---|
| **Render completo (recomendada)** | Web service ~US$ 7 (0,5 CPU, 512 MB) | Postgres ~US$ 6 (256 MB, 1 GB de disco incluso; disco extra US$ 0,30/GB) | **~US$ 13** | Um só painel; `render.yaml` pronto; HTTPS e domínio próprio inclusos; backups lógicos no banco pago; recuperação a um ponto no tempo (3 dias no workspace Hobby) | Sem região no Brasil (latência maior, sem impacto relevante para uso individual) |
| Render + Neon | Web service ~US$ 7 | Neon Launch: cobrança por uso (US$ 0,106 por hora de computação, US$ 0,35/GB-mês), sem mínimo, desliga sozinho sem uso | ~US$ 7 + poucos dólares | Histórico de restauração de até 7 dias | Dois provedores; o banco "acorda" após 5 min parado (pequena espera) |
| Railway | Plano Hobby: mínimo de US$ 5/mês com cobrança por uso | Incluso no uso | ~US$ 5–10 | Simples | Custo variável; política de backup do Postgres no Hobby não confirmada |
| Gratuita (Render free + Neon free) | US$ 0 | US$ 0 (6 h de histórico) | US$ 0 | Sem custo | Aplicação "dorme" sem uso e demora para abrir; janela de recuperação curta. **Não recomendada para dados financeiros** |

Domínio: a Doce Laços já planeja registrar um domínio `.com.br` para a loja (Registro.br, valor anual). Um subdomínio como `financeiro.seudominio.com.br` não tem custo adicional.

## Fontes

- Render — https://render.com/pricing
- Neon — https://neon.com/pricing
- Railway — https://costbench.com/software/database-as-service/railway-postgres/ (agregador; confirme em https://railway.com/pricing)
