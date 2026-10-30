# Capacidades reais da Birdeye gratuita

Este documento descreve o componente Birdeye. Em28-10-2026 o utilizador autorizou acrescentar Helius Free como complemento on-chain. A política atual permite apenas estes dois fornecedores gratuitos; consultar [capacidades Helius](helius-capabilities.md). Não há outros RPCs ou serviços de dados.

Verificação documental: **28 de outubro de 2026**. Rede deste projeto: **Solana**. Fonte deste componente: **API pública Birdeye**, autenticada com `X-API-KEY` e `x-chain: solana`. Ler documentação pública não significa recolher dados por scraping. Não se usam páginas de trading como API, RPCs, endpoints privados, x402 ou planos pagos.

## Resultado da pesquisa

O plano **Standard** é gratuito, com **30 000 CU por mês**, **1 pedido por segundo por conta** e sem CU adicionais. Não confundir registo gratuito com acesso gratuito a todos os endpoints. A documentação atual foi migrada para [data.birdeye.so/docs](https://data.birdeye.so/docs), pelo que resultados antigos de pesquisa em `docs.birdeye.so` podem estar desatualizados. [Preços oficiais](https://data.birdeye.so/docs/guides/payment/pricing); [limites por conta e endpoint](https://data.birdeye.so/docs/guides/api-access/rate-limiting).

A [matriz de acesso](https://data.birdeye.so/docs/guides/data-accessibility-by-packages), com efeito indicado de 26-11-2025, inclui tokens, traders, operações, transferências, património e PnL em Standard. Por isso a descoberta de candidatas **é possível documentalmente**. Isso **não basta para aprovar**: lacunas de inventário, taxas, falhas, cobertura de transações, coordenação ou execução continuam a bloquear as verificações respetivas.

**Validação autenticada:** em29-10-2026 a [sondagem corrigida](https://github.com/urbanz95/wallets/actions/runs/40869508780) passou os quatro contratos de carteira e uma segunda página de transferências: cinco respostas,92 CU reservados, sem retentativas ou Telegram. Os erros400/422 anteriores foram identificados por mensagens privadas da API e corrigidos: limite temporal exclusivo, âmbito WAC e endereço na continuação. A confirmação dos pedidos não prova cobertura integral, contabilidade fiável ou elegibilidade. O [registo da entrega](delivery.md) distingue os testes simulados, os resultados reais e as limitações restantes. Um401/403/429 nunca autoriza upgrade nem outro fornecedor.

## Tabela de capacidades

«Gratuito» abaixo significa confirmado na matriz e na página individual consultada, salvo indicação explícita. Todas as datas de recolha e evidências devem continuar a ser registadas em cada execução.

| Verificação pretendida | Dados necessários | Endpoint documentado em Standard | Limitações e efeito na avaliação |
|---|---|---|---|
| Descobrir tokens por idade, liquidez, volume e valorização | Listagem, momento de listagem e medidas atuais | `GET /defi/v3/token/list` | Idade de listagem não é idade de criação. Nem todos os tokens são memecoins. Filtro por plataforma é uma aproximação documentada; token sem idade disponível não satisfaz esse critério. |
| Descobrir traders relevantes por token | Endereço e atividade por token | `GET /defi/v2/tokens/top_traders` | Uma classificação de vencedores serve apenas para descobrir; não substitui a análise completa da carteira. |
| Encontrar primeiros compradores | Coorte de primeiras entradas | **Indisponível**: `/token/v1/first-buyers` é Lite+ na página individual | Não chamar. Top traders não pode ser descrito como lista de compradores iniciais. |
| Histórico de negociações de uma carteira | Operações de todos os tokens e tempos | `GET /trader/txs/seek_by_time` | A API exige apenas um limite temporal; o projeto usa o corte superior e filtra o início localmente. Limite de paginação; retenção histórica completa não garantida. Não enumera todas as transações falhadas da carteira. |
| Transferências e distribuições | Movimentos de entrada/saída | `POST /wallet/v2/transfer` | Desde 12-11-2021; cursor; movimento não demonstra compra nem custo de aquisição. |
| Posições abertas, incluindo ilíquidas | Inventário atual sem filtros de valor | `GET /wallet/v2/current-net-worth` | Exige `flags=include_low_liquidity`. Valor ou preço ausente continua desconhecido. Valor indicado não é receita executável para vender tudo. |
| Resultado por token fornecido pela Birdeye | PnL e cashflow por endereço do token | `POST /wallet/v2/pnl/details` | WAC exige `position_scope=duration_only`, conforme validado na API. Não são métricas recalculadas; método e âmbito devem ficar explícitos. Não provam que todas as taxas ou transferências tenham custo conhecido. |
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
- `address`, um único limite temporal, `tx_type=all`, `ui_amount_mode=raw`, `offset`, `limit` até100; `offset + limit <=10000`. O schema publica `after_time` e `before_time` como opcionais, mas a API real recusa ambos em simultâneo (HTTP422: `one of either before_time or after_time`). O projeto envia apenas `before_time` fixado no corte e aplica o início da janela localmente. Não existe parâmetro ou garantia de ordenação neste esquema: nunca terminar páginas só por encontrar uma operação antiga.
- Resposta `data.items`; aceitar `hasNext` e `has_next` documentados.
- Identidade: `tx_hash`, `ins_index`, `inner_ins_index`; contexto: `block_unix_time`, `block_number`, `owner`, `source`, `signers`.
- Pernas `base`/`quote` ou `from`/`to`, com `address`, `amount` bruto, `decimals`, alterações assinadas e `type_swap`. `price`, `base_price`, `quote_price` referem-se ao instante histórico; `nearest_price` é apenas referência próxima.
- Classificações incluem `swap`, `add`, `remove`; pernas de swap também podem conter a palavra `transfer`. Não confundir classificação de uma perna com transferência externa independente.
- A página não fixa a data inicial de cobertura, garantia de completude, taxas por operação ou enumeração de todas as falhas.

### Transferências

[Contrato oficial](https://data.birdeye.so/docs/data-api/balance-transfer/post-wallet-v2-transfer).

- `POST /wallet/v2/transfer`: **10 CU/pedido**, Solana desde epoch 257 (12-11-2021).
- Corpo inicial: `wallet`, `time_from`, `time_to`, `limit`; sem filtros de valor ou token para a análise integral. Podem existir filtros de sentido/montante/contraparte.
- A nota documental indica apenas `cursor` nas páginas seguintes, mas a API real respondeu HTTP400 `wallet is required`. O corpo de continuação envia **`wallet` e `cursor`**, sem repetir filtros de tempo ou montante. A sondagem verifica também uma continuação, quando existe cursor.
- Resposta `data` é um array. A nota de utilização documenta `next_cursor`, mas o esquema não fixa a sua localização: ausência ambígua, resposta malformada ou página cheia sem indicação fiável de fim não prova cobertura integral.
- Campos por evento: `unix_time`, `block_number`, `token_address`, `tx_hash`, `from_address`, `to_address`, contas de token, `amount`, `ui_amount`, `token_info`, `flow`, `action`.
- Ações: `mint`, `burn`, `transfer`, `set_authority`. Um valor USD de transferência não identifica o custo que o proprietário pagou para adquirir os tokens.

### Inventário atual

[Contrato oficial](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/get-wallet-v2-current-net-worth).

- `GET /wallet/v2/current-net-worth`: **30 CU/pedido**.
- `wallet`, `sort_by=value`, `sort_type`, `limit` até 100, `offset` até 10 000, **`flags=include_low_liquidity`**. O comportamento por defeito exclui tokens com liquidez inferior a 100 USD; seria incorreto usá-lo para avaliar perdas abertas.
- Não aplicar `filter_value` na análise da carteira.
- `data.items`, **`pagination.total` no topo da resposta**, `data.current_timestamp`; campos de posição incluem endereço, decimais, saldo bruto, montante, preço e valor. Aceitar somente formatos e variantes documentados.
- `items` ausente, nulo ou inválido significa inventário indisponível. Comparar total e linhas observadas; uma última página não prova que as anteriores tenham sido recolhidas.
- Não pressupor que um token sem preço vale zero nem que o preço spot permita liquidar a posição completa.

### PnL detalhado fornecido

[Contrato oficial](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/post-wallet-v2-pnl-details).

- `POST /wallet/v2/pnl/details`: **30 CU/pedido**.
- Corpo: `wallet`, `duration` (`all`, `90d`, `30d`, `7d`, `24h`), **`position_scope=duration_only`**, `pnl_method=wac`, `sort_by=last_trade`, `sort_type`, `offset` até 10 000, `limit` até 100. Não limitar `token_addresses` aos tokens vencedores/da descoberta.
- `data.tokens`, `data.summary.unique_tokens`, `data.meta.time`.
- Por token: `counts`, `quantity`, `cashflow_usd`, `pnl`, `pricing`. Separar realizado, não realizado e custo da quantidade vendida conforme fornecidos.
- `wac` usa custo médio ponderado do inventário existente; recompras posteriores não devem alterar vendas realizadas anteriores. `net_cash` usa médias cumulativas e pode mudar resultados históricos após novas compras; não o usar para validação temporal deste projeto.
- A API real confirmou que WAC só suporta `position_scope=duration_only`: a combinação anterior com `cumulative` devolvia HTTP400. O âmbito e a duração são guardados nas métricas do fornecedor. `duration=all` continua informativo e separado da janela FIFO local.
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

Há recolha real parcial, mas não há rentabilidade validada nem acompanhamento real do desempenho de carteiras aprovadas. Fixtures verificam os algoritmos e bloqueios, não certificam disponibilidade da API nem carteiras reais.

## Avaliação limitada dos dados observados — 29-10-2026

A versão de critérios `2026-10-29.3` separa a paginação concluída do feed (`coverage.complete`) da prova de cobertura global (`coverage.all_tokens`, inventário, custos e execução). O modo `observed` calcula uma seleção adicional para relatórios, com dados históricos USD e ciclos fechados interpretáveis, amostra, consistência, concentração, sinais observados de automatização e horizonte. Não altera os endpoints autorizados nem os respetivos limites gratuitos.

Tokens abertos ou desconhecidos ficam enumerados e fora do resultado bruto dos ciclos fechados. A seleção exige pelo menos80% dos tokens observados incluídos; essa fração mede número de tokens, não valor financeiro nem cobertura do património. Inventário global, taxas totais USD, posições abertas globais, liquidez, execução, coordenação/MEV e etiquetas continuam expressamente NÃO VERIFICADOS.

Esta seleção tem pré-visualização local e não autoriza Telegram. O envio continua a exigir as oito verificações estritas originais; `selection.notifications="strict"` é a única configuração aceite. Alterar essa política depende de autorização expressa e implementação posterior. A [entrega](delivery.md) regista a reavaliação offline e distingue-a de novas consultas autenticadas ou de aprovação real.


## Funil experimental por lucro — contratos revistos em 30-10-2026

Esta secção descreve o branch experimental iniciado em `077096d`. Acrescenta descoberta e pré-filtro por métricas do fornecedor; não altera a política estrita do Telegram. A confirmação abaixo é **documental**, cruzando os esquemas individuais com a [matriz Standard](https://data.birdeye.so/docs/guides/data-accessibility-by-packages), a [tabela de CU](https://data.birdeye.so/docs/guides/what-is-compute-unit-cost) e os [limites oficiais](https://data.birdeye.so/docs/guides/api-access/rate-limiting). Os testes destes novos módulos usam fixtures e não confirmam acesso autenticado com a chave da conta. Uma execução real deve ficar identificada separadamente no relatório de entrega.

| Etapa | Endpoint Standard e custo | Pedido implementado | Cobertura e limitação |
|---|---|---|---|
| Descoberta principal | `GET /trader/gainers-losers`, **30 CU** | `type=1W`, `sort_by=PnL`, `max_trade=700`, `offset=0`, `limit=100` | Classificação agregada de carteiras em Solana; não demonstra idade dos tokens, ciclos fechados, execução acompanhável ou ausência de bots. |
| Complemento por tokens recentes | `GET /defi/v3/token/list`, **50 CU** | Uma página de 100; listagem entre o corte e os sete dias anteriores, liquidez e volume 24h mínimos configuráveis; valorização 24h opcional | Seleciona até cinco tokens para obter traders. O momento de listagem não prova a criação do token e a lista não é exaustiva. |
| Traders por resultado realizado do token | `GET /defi/v2/tokens/top_traders`, **30 CU** por token | `time_frame=7d`, `sort_by=realized_pnl`, `sort_type=desc`, `offset=0`, `limit=10` | A origem é registada por token; encontrar lucro num token não demonstra lucro da carteira inteira. |
| Pré-filtro 7d e persistência 30d | `POST /wallet/v2/pnl/details`, **30 CU** por página | `duration=7d` ou `30d`, `pnl_method=wac`, `position_scope=duration_only`, `sort_by=last_trade`, `sort_type=desc`, `offset`, `limit=100` | Apenas ordenação e pré-filtro. A avaliação independente usa contabilidade própria; USD do fornecedor não passa a FIFO em SOL. |

### Classificação global de carteiras por PnL

[Contrato oficial de Gainers/Losers](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/get-trader-gainers-losers).

- `type` aceita `yesterday`, `today`, **`1W`**, `30d` e `90d`. `sort_by` aceita **`PnL`**, `realized_pnl`, `unrealized_pnl` e `trader_score`. A capitalização de `PnL` é obrigatória; este esquema não documenta `sort_type`.
- Paginação: `limit` de 1 a 100 e `offset + limit <= 10000`. O perfil experimental usa só a primeira página de 100, sem afirmar que encontrou todas as carteiras lucrativas.
- Filtros documentados incluem `min_trade`, `max_trade`, mínimos/máximos de volume USD, PnL realizado, não realizado e total. O contrato associa a ordenação `PnL` a um mínimo implícito de 1000 USD; o pedido omite `min_pnl`, em vez de inventar `min_pnl=0`. Isto introduz um viés adicional para carteiras de maior dimensão.
- Os campos documentados por linha são `network`, `address`, `pnl`, `realized_pnl`, `unrealized_pnl`, `volume` e `trade_count`. `pnl` agrega realizado e não realizado em USD, não representa lucro líquido recalculado. Campos ausentes ficam desconhecidos.
- Não há etiquetas, lista completa de tokens nem tempos por operação garantidos neste resultado. `trade_count / 7 <= 100` é apenas um filtro de intensidade média: não prova atividade uniforme, inexistência de rajadas nem estratégia humana.
- Uma carteira descoberta nesta classificação pode ter negociado tokens antigos. O requisito de atividade é a janela recente; a origem em tokens listados há até sete dias só fica demonstrada para candidatas com evidência adicional de `top_traders` desses tokens.

### Campos de top traders usados pelo novo funil

[Contrato oficial de Top Traders](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/get-defi-v2-tokens-top-traders).

- `sort_by` aceita exatamente `volume`, `trade`, `total_pnl`, `unrealized_pnl`, **`realized_pnl`**, `volume_usd` e `hold_volume`. A experiência escolhe resultado realizado, em vez de volume. `time_frame` aceita `30m`, `1h`, `2h`, `4h`, `6h`, `8h`, `12h`, `24h`, `2d`, `3d`, `7d`, `14d`, `30d`, `60d`, `90d` e `all_time`.
- Além de `owner` e `tags`, guardam-se os campos normalizados disponíveis: `volume`, `trade`, `tradeBuy`, `tradeSell`, `volumeBuy`, `volumeSell`, `volumeBuyUSD`, `volumeSellUSD`, `volumeUsd`, `totalPnl`, `realizedPnl`, `unrealizedPnl`, `holdVolume`, `holdVolumeUsd`, `holdAvgPrice`, `avgBuyPrice`, `avgSellPrice`, `firstTradeUnixTime` e `lastTradeUnixTime`. Os nomes de resposta são camelCase; os nomes de ordenação usam underscores.
- `wallet_tags` é um filtro de **inclusão**. Não existe aqui uma sintaxe documentada para excluir etiquetas pelo pedido. A experiência exclui localmente etiquetas observadas `sniper`, `bundler` e `insider`, segundo a regra explícita deste perfil. Etiquetas ausentes ou um array vazio não provam que a carteira seja humana ou acompanhável.
- A ordenação dá prioridade a carteiras observadas em dois ou mais tokens distintos. Não soma PnL de origens duplicadas nem transforma várias operações no mesmo token em amostras independentes. Os compradores iniciais Helius não entram neste funil.

### Paginação e significado do pré-filtro WAC

[Contrato oficial de PnL Details](https://data.birdeye.so/docs/data-api/wallet-networth-pnl/post-wallet-v2-pnl-details).

- O esquema documenta `sort_by=last_trade`; um exemplo textual que usa `value` não justifica enviar esse valor. `duration` aceita `all`, `90d`, `30d`, `7d` e `24h`; a experiência usa 7d e 30d. O limite é de 100 tokens por página; `offset` pode ir até 10000.
- `data.summary.unique_tokens` e os totais de `data.summary` referem-se a **todos os tokens do âmbito pedido**, independentemente da página devolvida. O cabeçalho pode por isso sustentar um pré-filtro provisório mesmo quando a primeira página não cobre todos os tokens. Esse passe não é aprovação, nem prova de contabilidade independente.
- Em `data.tokens`, os grupos normalizados são `counts` (`total_buy`, `total_sell`, `total_trade`, `total_win`, `total_loss`, `win_rate`), `quantity` (`total_bought_amount`, `total_sold_amount`, `holding`), `cashflow_usd` (`cost_of_quantity_sold`, `total_invested`, `total_sold`, `current_value`), `pnl` (`realized_profit_usd`, `realized_profit_percent`, `unrealized_usd`, `unrealized_percent`, `total_usd`, `total_percent`, `avg_profit_per_trade_usd`) e `pricing` (`current_price`, `avg_buy_cost`, `avg_sell_cost`). Só os campos necessários são conservados pelo pré-filtro; não se guardam payloads completos.
- A taxa de sucesso, o profit factor e a concentração por token só são calculados quando a paginação está completa e todos os resultados por token são conhecidos. Com uma página parcial ficam desconhecidos. Alteração dos totais entre páginas, linhas duplicadas ou respostas incoerentes impedem declarar completude.
- Os indicadores deste pré-filtro usam `pnl.total_usd` por token, incluindo posições marcadas pelo fornecedor. Não são os indicadores finais em SOL com abertos no pior caso. A taxa de sucesso é recalculada por token; o campo `counts.win_rate` do fornecedor permanece separado. A concentração divide os maiores resultados pela soma dos resultados **positivos**, nunca pelo resultado líquido total. Sem perdas observadas, o profit factor não é apresentado como infinito.
- `data.meta.time` identifica a fotografia do fornecedor; não oferece uma consulta histórica arbitrária. Os agregados móveis 7d e 30d sobrepõem-se, não são períodos independentes, não provam estabilidade fora da amostra e não devem ser apresentados como acompanhamento posterior real.

### Limites operacionais e estado de validação

O plano Standard permanece em 30000 CU/mês, um pedido por segundo por conta e sem compras adicionais. A página de rate limits refere também limites de grupos wallet (5 pedidos/s e 75/min); prevalece o limite da conta mais restritivo, sem ultrapassar 60 pedidos/min. Uma frase genérica antiga da página de limites restringe Standard a três endpoints, mas as páginas individuais acima e a matriz específica assinalam estes contratos como Standard: esta divergência fica registada e exige uma consulta real controlada antes de afirmar acesso da conta. Não foram encontrados avisos de depreciação nestes três contratos à data da revisão; ausência de aviso não garante disponibilidade futura ou retenção integral.

O teto documental do estágio de descoberta é **230 CU**: 30 da classificação global, 50 da lista de tokens e 5 × 30 dos traders. O perfil pode reservar 210 CU para sete pré-filtros 7d, 90 CU para três consultas 30d e 270 CU para a análise profunda, totalizando 800 CU. Estes são limites de planeamento, não consumos reais: o relatório de execução tem de distinguir reservas, pedidos efetivos, cache e consumo por estágio. Uma falha, restrição ou quota esgotada deixa as limitações explícitas e nunca autoriza consultar um endpoint pago ou outro fornecedor.

Os testes novos verificam contratos, ordenação, etiquetas, amostras por token, paginação, respostas parciais, orçamentos interrompidos e pré-filtro sem rede. **Não são uma validação autenticada destas novas consultas**, nem evidência de que qualquer carteira real seja rentável ou acompanhável. Identificadores de carteira e evidências individualizadas ficam apenas no estado cifrado e no Excel privado; este documento contém só contratos e limitações.
