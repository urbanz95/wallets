# Validação real da experiência de ranking

30-10-2026. Branch `feat/profit-evidence-funnel`, derivado de `077096d`, sem merge e sem alterações ao workflow da main. Telegram desligado. Este relatório público contém apenas contagens, consumo e motivos; endereços e ranking individual ficam no estado cifrado e no Excel privado.

## Implementado e testado

Descoberta Birdeye por PnL e cinco tokens recentes, deduplicação e exclusão da população etiquetada; pré-filtro WAC 7d/30d; reservas proporcionais por estágio com devolução de créditos nunca tentados; FIFO próprio em SOL, incluindo vendas parciais, recompras, taxas e rendas reconciliadas; abertos no pior caso; ranking A/B/C com pontuação, motivos e limitações; receipts compactos e snapshots imutáveis; exportação privada de Excel e execução manual isolada.

A suite cobre descoberta, pré-filtro, FIFO SOL, transferências de custo desconhecido, posições abertas, remoção dos vencedores, amostra insuficiente, quotas, atualidade, informação futura, ordenação, privacidade, persistência, reservas/reembolsos e envio estrito. Os cenários contabilísticos e a análise profunda integrada foram testados com **fixtures e mocks**, sem rede. Isso não equivale a uma validação contabilística de carteiras reais.

## Execuções autenticadas

| Execução | Candidatas únicas antes das exclusões | Após exclusões | Carteiras cujo pré-filtro foi invocado | Respostas PnL guardadas | Consultas 30d / profundas | Top-3 A/B/C | Birdeye por estágio: descoberta / pré-filtro / 30d / profunda | Helius |
|---|---:|---:|---:|---:|---:|---|---|---:|
| [41016344918](https://github.com/urbanz95/wallets/actions/runs/41016344918) | 110 | 101 | 7 | 4 | 0 / 0 | 0 / 0 / 3 | 220 / 210 / 0 / 0 CU | 0 |
| [41017213254](https://github.com/urbanz95/wallets/actions/runs/41017213254) | 110 | 101 | 7 | 0 novas | 0 / 0 | 0 / 0 / 3 | 80 / 0 / 0 / 0 CU | 0 |
| [41018023452](https://github.com/urbanz95/wallets/actions/runs/41018023452) | 100 | 100 | 7 | 0 novas | 0 / 0 | 0 / 0 / 3 | 80 / 0 / 0 / 0 CU | 0 |

Total das três execuções: **590 CU** locais contabilizadas como tentadas; Helius 0. Créditos tentados são contagem conservadora local, não confirmação de faturação pelo fornecedor. Na primeira foram devolvidos 10 CU não tentados; nas seguintes 360 CU em cada (150 da descoberta e 210 do pré-filtro). Não se reservaram créditos 30d/Helius/análise profunda sem candidatas que avançassem. Nenhuma mensagem Telegram foi enviada.

A contagem `pnl_details` descreve invocações do pré-filtro por carteira, não respostas bem-sucedidas: depois de o circuito interromper a API, essas invocações devolvem INDETERMINADO sem fazer rede. Os contadores de pedidos/consumo por estágio distinguem esse caso.

## O que ficou verificado com dados reais

Gainers `1W/PnL` devolveu 100 traders com PnL, contagens e campos do contrato. Top traders `realized_pnl` também devolveu dados. A lista de tokens teve respostas com os campos de idade de listagem, liquidez e volume; não prova a criação do mint. As respostas PnL reais incluíram SOL/stablecoins em `tokens[]` além de `summary.unique_tokens`; corrigiu-se a separação dos ativos de liquidação. As quatro respostas guardadas foram reprocessadas localmente com sucesso pelo leitor corrigido, **sem novos pedidos**. São prova de leitura do contrato, não aprovação dessas carteiras.

A terceira execução identificou o bloqueio concreto: **HTTP 429 em `/defi/v3/token/list`**. O cliente interrompeu depois a rede e as sete invocações de PnL não fizeram pedidos. Não se demonstrou rejeição financeira dessas sete carteiras. Também não se demonstrou lucro FIFO SOL, resultados 30d ou possibilidade de acompanhar as suas operações.

Histograma da terceira execução, contado por ocorrência de motivo (uma carteira pode ter vários):

| Motivo | Ocorrências |
|---|---:|
| Restrição HTTP 429 na fonte de tokens | 1 |
| Fonte de descoberta indisponível | 1 |
| Pré-filtro PnL indisponível | 7 |
| Proxies 7d e 30d não demonstrados | 7 cada |
| Cobertura profunda, amostra e custos SOL não demonstrados | 7 cada |
| Resultado conservador, PF, remoção de vencedores e concentração não demonstrados | 7 cada |
| Atividade recente, intensidade e horizonte não demonstrados | 7 cada |

São indeterminações, não evidência de que as carteiras perderam dinheiro. Os três lugares C existem para mostrar candidatas e lacunas; **não são três carteiras lucrativas identificadas**. O Excel mostra os endereços e as razões, sem promover esses lugares a recomendação.

O estado experimental recuperado após a terceira execução ocupava 348 160 bytes, sem payloads RPC Helius ou receipts reais, porque a análise profunda não chegou a ser chamada. A poda foi testada com fixtures; não se afirma que foi exercitada em rede nesta validação.

## Limites e configuração

Mantêm-se NÃO VERIFICADOS inventário global, cobertura global, conversão histórica USD, custos não identificados, liquidez executável, execução de um seguidor, coordenação/MEV, identidade humana, etiquetas completas e persistência independente. Não foram alterados os limiares em resposta às carteiras observadas. Não foram adicionados fornecedores ou subscrições. Os secrets existentes chegaram para autenticar as consultas bem-sucedidas; HTTP 429 exige distinguir limite de frequência/quota no fornecedor, sem pedir um upgrade.

A main continua independente. Este branch é manual; não se agendou uma segunda execução diária que somasse consumo à main. Ver [utilização](profit-experiment.md), [critérios e cálculo da amostra](criteria.md) e [contratos](capabilities.md).
