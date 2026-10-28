# Método de contabilidade e significado dos valores

O programa usa FIFO: numa venda, considera consumidas primeiro as unidades
compradas há mais tempo. Agrupa todas as operações pelo endereço completo do
token; compras repetidas do mesmo token não aumentam a amostra de tokens.
Quantidades e valores monetários usam `Decimal`, não números binários `float`.
O coletor converte unidades inteiras usando os decimais publicados pela Birdeye.

Uma compra cria um lote com o valor total USD histórico e as taxas de aquisição
conhecidas. Uma venda parcial consome apenas a fração correspondente do lote.
Uma recompra cria um novo lote. As taxas de venda conhecidas são deduzidas do
resultado dessa venda. A informação sobre taxas tem de ser completa para o
resultado poder ser apresentado como líquido.

Os campos internos têm significados diferentes:

| Campo | Significado |
| --- | --- |
| `costs` | Soma dos valores USD identificados de todas as compras, incluindo posições ainda abertas; exclui taxas. |
| `fees` | Soma das taxas USD identificadas; não garante que todas as taxas sejam conhecidas. |
| `realized_cost` | Custo FIFO identificado das unidades vendidas, incluindo taxas de aquisição identificadas. Pode ser parcial quando `reliable=false`. |
| `identified_realized` | Resultado das frações vendidas com custo identificável, deduzindo apenas taxas identificadas. Pode omitir custos desconhecidos; não é lucro líquido completo. |
| `realized` | Resultado realizado recalculado; fica desconhecido (`null`) se a contabilidade ou a cobertura não forem suficientemente fiáveis. |
| `open_quantity` | Inventário final dedutível das operações interpretadas no período. |
| `open_cost` | Custo do inventário final; fica desconhecido se existem lotes ou operações não interpretadas que impeçam apurá-lo. |
| `unrealized` | Quantidade aberta × cotação indicativa − custo conhecido; fica desconhecido se faltar custo ou cotação. |
| `unknown_cost_quantity` | Quantidade aberta cujo custo completo não é conhecido. Custos desconhecidos de unidades já vendidas continuam a bloquear `reliable`, mesmo que esta quantidade seja zero. |

As métricas calculadas são separadas das métricas agregadas devolvidas pela
Birdeye. O método WAC usado por alguns endpoints da Birdeye não é tratado como
se fosse o FIFO recalculado nem como prova de taxas ou de inventário completos.

## O que permanece desconhecido

Uma transferência recebida ou distribuição gratuita cria um lote de custo
desconhecido, mesmo que exista um preço de mercado. Esse preço não é prova de
que a carteira comprou o token. Uma transferência enviada não é uma venda e não
realiza um ganho. Vender unidades sem compras anteriores observadas não cria um
custo de aquisição de zero. Falhas, operações de liquidez e swaps com várias
etapas sem interpretação completa nunca são transformados em compras ou vendas
simples.

Uma transação falhada não altera o inventário; uma taxa identificada pode ser
registada como despesa. Converter SOL para wrapped SOL não cria um ganho por si
só. O coletor deve normalizar ambos para a mesma exposição e não contar pernas
intermédias de um swap como operações independentes. A taxa de uma transação
partilhada por várias pernas deve ser alocada uma única vez pelo normalizador;
na ausência de prova dessa alocação, permanece desconhecida.

Se faltarem inventário inicial, cobertura de todos os tokens, taxas ou páginas
de histórico, `reliable` permanece falso. Múltiplas operações do mesmo token no mesmo
segundo também não são declaradas fiáveis quando não há ordenação intrassegundo
suficiente: até duas compras a preços diferentes podem alterar o FIFO de uma
venda parcial posterior. Um evento duplicado idêntico é eliminado por carteira e identidade;
um duplicado contraditório interrompe a contabilidade em vez de escolher uma
versão arbitrária.

## Posições abertas e tempo

As cotações usadas nas posições abertas são indicativas, não uma promessa de
que toda a posição possa ser vendida a esse preço. Não incluem impacto no
preço sem dados que o permitam estimar. Uma perda aberta conhecida é preservada;
a falta de cotação não significa que a posição tenha valor zero nem que esteja
sem perdas. Nenhum preço atual de SOL é aplicado às operações históricas.

Os eventos e cotações posteriores ao fim da avaliação são rejeitados. As semanas
contabilísticas são intervalos não sobrepostos que começam à segunda-feira em
UTC; os relatórios apresentam horas locais de Europe/Lisbon. O período de
manutenção é medido entre a aquisição de cada lote e o respetivo consumo FIFO.
Não se calcula drawdown a partir da soma de resultados de trades: é necessária
uma série adequada do valor da carteira e tratamento dos fluxos externos.


## Reconciliação Birdeye e Helius

A partir da versão0.2, os dados brutos Helius, os eventos Helius e os eventos Birdeye ficam separados no SQLite. A vista usada para análise escolhe a representação Helius nas assinaturas observadas em ambas as fontes, evitando duplicar posições ou resultados. Valores históricos USD só são associados se token, direção, quantidade, instante e assinatura coincidirem de forma única. Divergências tornam a interpretação indeterminada; dados desconhecidos não são preenchidos por estimativas.

Os programas Pump.fun e PumpSwap são reconhecidos por endereços, discriminadores e posições de contas publicados nos IDLs oficiais, fixados num commit documentado no normalizador. O beneficiário da conta de token é identificado separadamente do pagador da taxa. Renda de contas, conversão SOL/wSOL, variantes desconhecidas e rotas múltiplas não são tratados como swaps simples sem reconciliação suficiente. Saldos de USDC/USDT não implicam automaticamente um preço histórico exato de1USD.
