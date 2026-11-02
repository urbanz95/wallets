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


## Resultado final após as correções

A [execução41021973206](https://github.com/urbanz95/wallets/actions/runs/41021973206), revisão `2026-10-30.profit.3`, percorreu todos os estágios. Foram consultados o ranking global por PnL realizado e cinco tokens recentes, com pacing partilhado e proteção temporal dos proxies.

| Estágio | Contagem | Birdeye tentado | Helius tentado |
|---|---:|---:|---:|
| Candidatas únicas antes das exclusões | 150 | — | — |
| Candidatas após exclusões | 113 | 230 CU na descoberta | 0 |
| Pré-filtros7d com resposta / favoráveis | 13 / 9 | 390CU | 0 |
| Consultas30d com resposta | 3 | 90CU | 0 |
| Análises profundas | 2 | 36 CU | 200 créditos |
| Top-3 por nível A / B / C | 0 / 2 / 1 | — | — |
| Total desta execução | — | **746CU** | **200 créditos** |

Foram reservados782 CU e devolvidos36 CU nunca tentados na análise profunda. Contabilizaram-se26 pedidos Birdeye e2 Helius. O consumo agregado das iterações e da sondagem descritas nesta entrega foi1506 CU Birdeye e200 créditos Helius; não se mascara o custo das tentativas de correção como consumo da execução final.

Histograma final, por ocorrência:50 exclusões de etiquetas;3 pré-filtros com PnL do fornecedor não positivo;1 por intensidade acima do limite;2 históricos profundos parciais. Na amostra consultada,13 casos sem prova suficiente de amostra/custos/resultados SOL, PF, concentração ou horizonte;10 sem30d/datas completas porque apenas as três melhores receberam a consulta30d. São contagens de motivos, não carteiras únicas nem afirmações de que todas perderam dinheiro.

**Os três lugares são candidatas com evidência B/B/C, não três carteiras cuja rentabilidade e acompanhamento estejam confirmados.** Nos dois históricos profundos observaram-se 9 e 53 tokens, mas nenhum atingiu custo SOL conhecido suficiente nesta avaliação. O realizado próprio, PF próprio e cenários sem vencedores ficam por apurar, nunca zero. Ambos os históricos mantêm continuação Helius e classificação insuficiente; os proxies7d/30d não substituem esses dados.

A poda foi agora exercitada com dados reais:244 transações normalizadas em receipts,244 payloads Helius removidos após persistência e manifestos,0 payloads Helius brutos restantes. O SQLite recuperado tinha 1 204 224 bytes. Isso comprova persistência e poda nesta amostra, não cobertura integral ou precisão de custos que permaneceram desconhecidos. Reexecuções incrementais podem continuar a paginação; não se excede o orçamento para forçar uma aprovação.

O Excel privado contém o top-3, pontuação, proxies7d/30d, motivos, NÃO VERIFICADO e folhas com as análises profundas e tokens, incluindo análises que fiquem fora do top-3. A recuperação por `scripts/profit_export.py` usa uma base temporária e não altera a fotografia. Nenhum endereço foi acrescentado aos resumos públicos. Não houve notificações.

A suite final tem **586 testes** e cobre os formatos das respostas reais através de fixtures anónimas, datas futuras do fornecedor, pacing entre famílias, Retry-After e recuperação do Excel. A contabilidade SOL, os cenários de custo conhecido e a classificação A foram validados com fixtures; **não foi confirmada rentabilidade FIFO SOL em nenhuma carteira real nesta entrega**. O ranking já não fica vazio por impossibilidade artificial de atingir a seleção estrita, mas os limites de evidência continuam explícitos.

## Revisão de 02-11-2026: recolha completa, interpretação ainda parcial

As alterações `b1efc4e` e `66fe4cd` foram publicadas apenas em
`feat/profit-evidence-funnel`, sem merge. Acrescentam rotação persistente de
candidatas, pedidos manuais cifrados, evidência de origem comprimida durante
35 dias e interpretações versionadas, paginação profunda e recuperação limitada
de inventário anterior. Parsed Events da Helius é usado como prova adicional;
não substitui a reconciliação dos saldos. Os critérios financeiros mantêm-se.

A [execução geral 41534727472](https://github.com/urbanz95/wallets/actions/runs/41534727472)
terminou com sucesso. O período termina em 02-11-2026 às 22:09, Europe/Lisbon;
a janela de seleção tem sete dias.

| Estágio | Contagem | Birdeye tentado | Helius tentado |
|---|---:|---:|---:|
| Candidatas descobertas / após exclusões | 146 / 122 | 230 CU | 0 |
| Respostas PnL 7d / pré-filtros favoráveis | 13 / 6 | 390 CU | 0 |
| Consultas PnL 30d | 3 | 90 CU | 0 |
| Análises profundas | 2 | 48 CU | 870 créditos |
| Top-3 A / B / C | 0 / 2 / 1 | — | — |
| Total | — | **758 CU** | **870 créditos** |

Foram devolvidos 24 CU Birdeye e 850 créditos Helius reservados mas não
utilizados. Os valores são contagens conservadoras locais de pedidos tentados,
não um extrato de faturação. Nenhuma notificação foi enviada.

Os dois históricos recentes ficaram paginados por completo: 1 615 e 38
transações. Isso confirma a conclusão da paginação nesta janela e âmbito de
consulta, não a cobertura de todo o inventário da carteira. A recuperação
anterior da primeira análise atingiu 2 000 transações e permaneceu truncada.
As duas análises observaram 50 e 8 tokens, respetivamente, mas **nenhuma produziu
um token com resultado FIFO SOL suficientemente conhecido**. O top-3 B/B/C não
constitui uma seleção de três carteiras confirmadas como lucrativas.

A revisão local, sem novas consultas, identificou uma limitação de implementação:
rotas com programas adicionais, instruções Pump ainda não suportadas e taxas
pagas por terceiros continuam sem atribuição suficiente. As sete consultas
Parsed Events forneceram 631 provas com `parserStatus=OK`, incluindo 248 resumos
de swap. Um resumo de swap não basta para provar a participação da carteira,
o custo líquido e todos os fluxos da transação. O verificador conservador
mantém estes casos desconhecidos. A variante `get_fees` agora suportada não é
a variante dominante desta amostra; a correção foi testada com fixtures, mas
não resolveu a interpretação destas carteiras reais.

O bloqueio atual não é ausência total de transações nas APIs gratuitas:
**há dados reais recolhidos que o interpretador ainda não consegue reconciliar**.
DexScreener não fornece a interpretação das transações ou o PnL das carteiras
e não resolve esta lacuna. Não foi integrada. A evidência preservada permite
validar futuras correções localmente, sem voltar a consumir quota de recolha.

Histograma de verificações (ocorrências, não carteiras distintas):

| Verificação | Reprovada | Indeterminada |
|---|---:|---:|
| Pré-filtro fornecedor 7d | 7 | 0 |
| Persistência fornecedor 30d | 1 | 10 |
| Cobertura profunda | 0 | 11 |
| Proporção com custo conhecido | 2 | 11 |
| Amostra SOL | 2 | 11 |
| Custos SOL | 0 | 11 |
| Resultado conservador, PF, concentração e remoção de vencedores | 0 cada | 13 cada |
| Atividade recente, intensidade e horizonte | 0 cada | 13 cada |

A [execução manual 41535174902](https://github.com/urbanz95/wallets/actions/runs/41535174902)
consultou uma carteira introduzida por pedido cifrado: uma resposta PnL 7d,
zero pré-filtros favoráveis, zero consultas 30d e zero análises profundas.
O motivo foi `activity_over_daily_limit`; consumo de **30 CU Birdeye**,
zero créditos Helius e zero notificações. Os contadores de descoberta são
zero porque a descoberta foi desativada neste pedido, não porque a carteira
ficou por consultar. O critério mede a média de operações por dia e não
confirma automatização nem prejuízo. Os dados individuais ficam apenas no
estado cifrado e no Excel privado.

Os Excel privados separam agora o PnL total, realizado e não realizado
**fornecido pela Birdeye**, para 7d e 30d. A consulta parcial preserva os
valores agregados do fornecedor, mas não inventa PF, concentração ou taxa de
sucesso quando faltam páginas por token. Não se apresentam esses valores como
lucro líquido recalculado. A exportação foi repetida a partir dos snapshots
imutáveis, sem rede e sem alterar a avaliação original.

A suite passou **596 testes**. Os cenários de FIFO SOL reconciliado e de nível
A continuam validados com fixtures; nesta revisão real não foi confirmada
nenhuma carteira de nível A. Os secrets existentes foram suficientes e não
é necessário configurar um terceiro fornecedor para reproduzir estes resultados.
