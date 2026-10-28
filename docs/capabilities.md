# Capacidades reais da Birdeye gratuita

Este documento descreve o componente Birdeye. Em28-10-2026 o utilizador autorizou acrescentar Helius Free como complemento on-chain. A política atual permite apenas estes dois fornecedores gratuitos; consultar [capacidades Helius](helius-capabilities.md). Não há outros RPCs ou serviços de dados.

Verificação documental: **28 de outubro de 2026**. Rede deste projeto: **Solana**. Fonte deste componente: **API pública Birdeye**, autenticada com `X-API-KEY` e `x-chain: solana`. Ler documentação pública não significa recolher dados por scraping. Não se usam páginas de trading como API, RPCs, endpoints privados, x402 ou planos pagos.

## Resultado da pesquisa

O plano **Standard** é gratuito, com **30 000 CU por mês**, **1 pedido por segundo por conta** e sem CU adicionais. Não confundir registo gratuito com acesso gratuito a todos os endpoints. A documentação atual foi migrada para [data.birdeye.so/docs](https://data.birdeye.so/docs), pelo que resultados antigos de pesquisa em `docs.birdeye.so` podem estar desatualizados. [Preços oficiais](https://data.birdeye.so/docs/guides/payment/pricing); [limites por conta e endpoint](https://data.birdeye.so/docs/guides/api-access/rate-limiting).

A [matriz de acesso](https://data.birdeye.so/docs/guides/data-accessibility-by-packages), com efeito indicado de 26-11-2025, inclui tokens, traders, operações, transferências, património e PnL em Standard. Por isso a descoberta de candidatas **é possível documentalmente**. Isso **não basta para aprovar**: lacunas de inventário, taxas, falhas, cobertura de transações, coordenação ou execução continuam a bloquear as verificações respetivas.

**Validação autenticada:** nenhuma foi realizada durante esta pesquisa; não havia uma chave Birdeye configurada. A confirmação efetiva de acesso exige a chave Standard do utilizador e poucas consultas controladas. Um 403/401/429 não pode converter-se em aprovação nem desencadear consulta a outro fornecedor.

## Tabela de capacidades

«Gratuito» abaixo significa confirmado na matriz e na página individual consultada, salvo indicação explícita. Todas as datas de recolha e evidências devem continuar a ser registadas em cada execução.

| Verificação pretendida | Dados necessários | Endpoint documentado em Standard | Limitações e efeito na avaliação |
|---|---|---|---|
| Descobrir tokens por idade, liquidez, volume e valorização | Listagem, momento de listagem e medidas atuais | `GET /defi/v3/token/list` | Idade de listagem não é idade de criação. Nem todos os tokens são memecoins. Filtro por plataforma é uma aproximação documentada; token sem idade disponível não satisfaz esse critério. |
| Descobrir traders relevantes por token | Endereço e atividade por token | `GET /defi/v2/tokens/top_traders` | Uma classificação de vencedores serve apenas para descobrir; não substitui a análise completa da carteira. |
| Encontrar primeiros compradores | Coorte de primeiras entradas | **Indisponível**: `/token/v1/first-buyers` é Lite+ na página individual | Não chamar. Top traders não pode ser descrito como lista de compradores iniciais. |
| Histórico de negociações de uma carteira | Operações de todos os tokens e tempos | `GET /trader/txs/seek_by_time` | Limite de paginação; retenção histórica completa não garantida. Não enumera todas as transações falhadas da carteira. |
| Transferências e distribuições | Movimentos de entrada/saída | `POST /wallet/v2/transfer` | Desde 12-11-2021; cursor; movimento não demonstra compra nem custo de aquisição. |
| Posições abertas, incluindo ilíquidas | Inventário atual sem filtros de valor | `GET /wallet/v2/current-net-worth` | Exige `flags=include_low_liquidity`. Valor ou preço ausente continua desconhecido. Valor indicado não é receita executável para vender tudo. |
| Resultado por token fornecido pela Birdeye | PnL e cashflow por endereço do token | `POST /wallet/v2/pnl/details` | Não são métricas recalculadas; método e âmbito devem ficar explícitos. Não provam que todas as taxas ou transferências tenham custo conhecido. |
| Preço atual | Preço e instante de atualização | `GET /defi/price` | Valor indicativo; pode estar ausente ou desatualizado. Não é preço executável da posição completa. |
| Taxas/falhas de transações já conhecidas | Assinatura, taxas e estado | `GET /blockchain/v1/transaction/detail` | Enriquecimento opcional implementado, taxas em lamports sem USD/reconciliação; só consulta assinaturas conhecidas. Não descobre falhas omitidas do feed de operações. Campos opcionais ou não interpretáveis bloqueiam cobertura. |
| Curva patrimonial | Série histórica e fluxos externos reconciliados | `GET /wallet/v2/net-worth` | Diário ou horário; horário limitado a sete dias. Património bruto sem ajustar depósitos/levantamentos não é uma curva de rentabilidade adequada para drawdown. |
| Sinais de automatização | Intervalos, slots, instruções, etiquetas e pares de entradas/saídas | Operações + etiquetas de top traders | Etiqueta isolada não prova bot, humano ou fraude. Coordenação/MEV/latência e atividade não observada podem continuar indeterminados. |
| Possibilidade de acompanhar | Atraso, duração, profundidade, custos e impacto para o tamanho considerado | **Não demonstrada** por um preço spot ou lista de trades | Não simular entrada ao preço original do trader. Falta de evidência de liquidez executável e rapidez reproduzível bloqueia aprovação. |

## Contratos dos endpoints usados na recolha

Todos usam `https://public-api.birdeye.so`. Os métodos e nomes de parâmetros abaixo seguem os esquemas oficiais consultados. Os exemplos da documentação não são prova de acesso com a chave do utilizador. Não há sinalização de depreciação nas páginas dos endpoints aqui integrados, à data da consulta; isso não garante disponibilidade futura.

### Lista de tokens V3

[Contrato oficial](https://data.birdeye.so/docs/data-api/tokenmarket-list/get-defi-v3-token-list).

- `GET /defi/v3/token/list`: **50 CU/pedido**.
- Paginação `offset` até 10 000, `limit` de 1 a 100; resposta `data.items`, `data.hasNext`.
- `sort_by`, `sort_type`; filtros confirmados incluem `min_liquidity`, `min_volume_24h_usd`, `min_recent_listing_time`, `max_recent_listing_time`, `min_price_change_24h_percent`, `platform_name`.
- Campos relevantes: `address`, `decimals`, `liquidity`, `volume_24h_usd`, `price_change_24h_percent`, `recent_listing_time`, `last_trade_unix_time`.
- Medidas móveis atuais não são um arquivo histórico do universo de tokens. Não usar a lista de hoje para afirmar descoberta em datas passadas.
- Não usar a variante `scroll`: o acesso gratuito não está confirmado.

### Top traders

[Contrato oficial](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/get-defi-v2-tokens-top-traders).

- `GET /defi/v2/tokens/top_traders`: **30 CU/pedido**.
- `address`, `time_frame`, `sort_by`, `sort_type`, `offset`, `limit` (1–10); `offset + limit <= 10000`.
- Janelas de 30 minutos a 90 dias e `all_time` em Solana. Ordenação por volume, operações, PnL ou posição conforme o contrato.
- Resposta `data.items`; endereço em `owner`. PnL/cashflow, montantes de compra/venda, tempos da primeira/última operação e `tags` podem estar presentes.
- `sniper`, `bundler`, `insider` ou `smart_trader` são classificações Birdeye e sinais contextuais; não são conclusões deste programa. A ausência de etiquetas não certifica comportamento humano.

### Operações por carteira

[Contrato oficial](https://data.birdeye.so/docs/data-api/transactions/get-trader-txs-seek-by-time).

- `GET /trader/txs/seek_by_time`: **12 CU/pedido**.
- `address`, `after_time`, `before_time` (Unix em segundos), `tx_type=all`, `ui_amount_mode=raw`, `offset`, `limit` até 100; `offset + limit <= 10000`. Não existe parâmetro de ordenação neste esquema.
- Resposta `data.items`; aceitar `hasNext` e `has_next` documentados.
- Identidade: `tx_hash`, `ins_index`, `inner_ins_index`; contexto: `block_unix_time`, `block_number`, `owner`, `source`, `signers`.
- Pernas `base`/`quote` ou `from`/`to`, com `address`, `amount` bruto, `decimals`, alterações assinadas e `type_swap`. `price`, `base_price`, `quote_price` referem-se ao instante histórico; `nearest_price` é apenas referência próxima.
- Classificações incluem `swap`, `add`, `remove`; pernas de swap também podem conter a palavra `transfer`. Não confundir classificação de uma perna com transferência externa independente.
- A página não fixa a data inicial de cobertura, garantia de completude, taxas por operação ou enumeração de todas as falhas.

### Transferências

[Contrato oficial](https://data.birdeye.so/docs/data-api/balance-transfer/post-wallet-v2-transfer).

- `POST /wallet/v2/transfer`: **10 CU/pedido**, Solana desde epoch 257 (12-11-2021).
- Corpo inicial: `wallet`, `time_from`, `time_to`, `limit`; sem filtros de valor ou token para a análise integral. Podem existir filtros de sentido/montante/contraparte.
- Páginas seguintes: enviar **apenas** `cursor` com o `next_cursor` anterior. Não juntar filtros antigos ao cursor.
- Resposta `data` é um array. A nota de utilização documenta `next_cursor`, mas o esquema não fixa a sua localização: ausência ambígua, resposta malformada ou página cheia sem indicação fiável de fim não prova cobertura integral.
- Campos por evento: `unix_time`, `block_number`, `token_address`, `tx_hash`, `from_address`, `to_address`, contas de token, `amount`, `ui_amount`, `token_info`, `flow`, `action`.
- Ações: `mint`, `burn`, `transfer`, `set_authority`. Um valor USD de transferência não identifica o custo que o proprietário pagou para adquirir os tokens.

### Inventário atual

[Contrato oficial](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/get-wallet-v2-current-net-worth).

- `GET /wallet/v2/current-net-worth`: **30 CU/pedido**.
- `wallet`, `sort_by=value`, `sort_type`, `limit` até 100, `offset` até 10 000, **`flags=include_low_liquidity`**. O comportamento por defeito exclui tokens com liquidez inferior a 100 USD; seria incorreto usá-lo para avaliar perdas abertas.
- Não aplicar `filter_value` na análise da carteira.
- `data.items`, `data.total`, `data.current_timestamp`; campos de posição incluem endereço, decimais, saldo bruto, montante, preço e valor. Aceitar somente formatos e variantes documentados.
- `items` ausente, nulo ou inválido significa inventário indisponível. Comparar total e linhas observadas; uma última página não prova que as anteriores tenham sido recolhidas.
- Não pressupor que um token sem preço vale zero nem que o preço spot permita liquidar a posição completa.

### PnL detalhado fornecido

[Contrato oficial](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/post-wallet-v2-pnl-details).

- `POST /wallet/v2/pnl/details`: **30 CU/pedido**.
- Corpo: `wallet`, `duration` (`all`, `90d`, `30d`, `7d`, `24h`), `position_scope`, `pnl_method=wac`, `sort_by=last_trade`, `sort_type`, `offset` até 10 000, `limit` até 100. Não limitar `token_addresses` aos tokens vencedores/da descoberta.
- `data.tokens`, `data.summary.unique_tokens`, `data.meta.time`.
- Por token: `counts`, `quantity`, `cashflow_usd`, `pnl`, `pricing`. Separar realizado, não realizado e custo da quantidade vendida conforme fornecidos.
- `wac` usa custo médio ponderado do inventário existente; recompras posteriores não devem alterar vendas realizadas anteriores. `net_cash` usa médias cumulativas e pode mudar resultados históricos após novas compras; não o usar para validação temporal deste projeto.
- Uma janela móvel agregada não fornece semanas independentes nem prova custo de transferências. Reconciliação independente continua necessária para aprovação.

## Enriquecimento opcional por assinatura

O módulo `transaction_details.py` implementa consultas controladas a `GET /blockchain/v1/transaction/detail` (10 CU), com cache e limite por carteira (`api.max_transaction_details_per_wallet=2` no exemplo; `0` desativa). Consulta apenas assinaturas conhecidas, rejeita eventos e detalhes posteriores ao corte temporal e guarda taxas observadas em **lamports**. `priority_fee` é uma parcela de `fee`: nunca se somam as duas para produzir um custo maior. Dados ausentes ou inválidos permanecem desconhecidos.

Os subtotais não são convertidos automaticamente para USD nem atribuídos à contabilidade da carteira. O pagador económico e as falhas omitidas do histórico não ficam demonstrados. O contrato não define a estrutura interna do objeto `status`; conserva-se a evidência sem inferir sucesso pela ausência de erros. Este enriquecimento **não promove nenhuma flag de cobertura** nem desbloqueia aprovações. Testado com mocks; sem consulta autenticada real.

## Preços e outras evidências suplementares

O detalhe por assinatura tem implementação opcional descrita acima. Os endpoints de preço isolado, curva patrimonial e variações de saldo abaixo foram investigados documentalmente; a sua presença nesta tabela não significa integração automática na análise.

| Endpoint e fonte | Método / CU | Contrato e cobertura | Utilidade e limite |
|---|---|---|---|
| [Preço](https://data.birdeye.so/docs/data-api/price-ohlcv/get-defi-price) | GET `/defi/price`, 3 CU | `address`, `include_liquidity`; sem paginação; `value`, `updateUnixTime`, liquidez quando disponível | Atualidade e marca indicativa; desconhecido mantém-se desconhecido. Nunca converter todo o histórico com SOL atual. |
| [Detalhe de transação](https://data.birdeye.so/docs/blockchain-api/transaction/get-blockchain-v1-transaction-detail) | GET `/blockchain/v1/transaction/detail`, 10 CU | `signature`; sem paginação; `fee` em lamports, `priority_fee`, `slot`, `block_time`, `status`, saldos, instruções e logs quando disponíveis | `fee` é total: não somar outra vez a prioridade. Não identifica todas as assinaturas falhadas de uma carteira. Retenção não fixada na página. |
| [Curva de património](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/get-wallet-v2-net-worth) | GET `/wallet/v2/net-worth`, 50 CU | `wallet`, `time` UTC, `direction=back/forward`, `count` 1–90, `type=1d/1h`; horário apenas últimos sete dias | Não usar património bruto como curva ajustada a fluxos externos. |
| [Variações de saldo](https://data.birdeye.so/docs/data-api/balance-transfer/get-wallet-v2-balance-change) | GET `/wallet/v2/balance-change`, 10 CU | Esquema usa `address` (o texto introdutório diz `wallet`); `time_from/time_to`, `type`, `change_type`, `offset` até 10 000, `limit` até 100 | Contradições textuais devem ser resolvidas pelo esquema e por consulta controlada; não inventar campos. |

Os custos acima foram cruzados com a [tabela oficial de CU](https://data.birdeye.so/docs/guides/what-is-compute-unit-cost). A pesquisa documental não consome CU da conta; os pedidos de dados e as retentativas consomem orçamento local conservador. O limite mensal é partilhado com outras aplicações da mesma conta. O contador local não conhece automaticamente consumo externo.

## Capacidades bloqueadas e contradições

- [`/v1/wallet/tx_list`](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/get-v1-wallet-tx-list) e o portfolio legado não são Standard: não são uma alternativa.
- [First Buyers](https://data.birdeye.so/docs/data-api/holder/get-token-v1-first-buyers) e os trackers de etiquetas não têm acesso gratuito confirmado; não se chamam.
- A matriz geral marca Meme List como Standard, mas a [página individual](https://data.birdeye.so/docs/data-api/meme/get-defi-v3-token-meme-list) indica Lite ou superior. **Bloqueado por prudência**. Usa-se apenas a lista V3 geral confirmada e declara-se o âmbito da seleção.
- A documentação de limites menciona valores diferentes para grupos wallet. O cliente aplica o limite Standard mais restritivo (um pedido de cada vez, intervalo mínimo entre pedidos) e retentativas limitadas; não tenta ultrapassar 429.
- Dados por assinatura, tags ou operações observadas não garantem cobertura de falhas, coordenação entre todas as carteiras, MEV ou profundidade vendável para um seguidor. Não há classificação «humano confirmado» ou «livre de bots».

## Consequência para o Telegram

Na integração Birdeye, o recolhedor nunca declara cobertura integral, inventário inicial, taxas totais ou execução/coordenação/etiquetas suficientes: estes factos não foram demonstrados pelo conjunto de endpoints integrado. Essas lacunas bloqueiam a aprovação. O complemento Helius acrescenta histórico, evidência de compras e taxas nativas, mas não prova por si só custos totais USD, inventário inicial, liquidez de saída ou automatização adequada. A configuração de secrets e respostas HTTP bem-sucedidas não substituem essas verificações. A descoberta e os relatórios parciais continuam funcionais. Flags de cobertura não são opções de configuração para ultrapassar estas lacunas; exigiriam nova evidência auditável e implementação verificada.

Cada verificação obrigatória permanece obrigatória. Sempre que o conjunto gratuito efetivamente recolhido não sustenta taxas, custo, inventário, risco de automatização ou possibilidade de acompanhar com cobertura suficiente, o estado é **INDETERMINADA**. Zero carteiras aprovadas é um resultado válido. Não se enviam candidatas, erros ou explicações de limitações pelo Telegram.

Não há resultados reais de rentabilidade nem acompanhamento real validado nesta entrega sem chave e recolha posterior. Fixtures verificam os algoritmos e bloqueios, não certificam disponibilidade da API nem carteiras reais.
