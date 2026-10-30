# Critérios, métricas e limites heurísticos

Versão atual: `2026-10-29.3`. Os valores efetivos estão em `config.toml`. São **heurísticas de seleção**, sem garantia de rentabilidade e sem otimização para fazer as carteiras observadas passar. Cada fotografia guarda a versão, a configuração completa e o respetivo hash. Alterar a política ou um limite exige nova avaliação; uma fotografia antiga não passa a poder ser enviada automaticamente.

## Dois âmbitos de avaliação

O modo predefinido `observed` acrescenta uma avaliação limitada às operações observadas. A seleção para consulta nos relatórios pode passar com uma amostra suficiente e interpretável, enquanto aspetos sem prova continuam **NÃO VERIFICADOS**. Não é aprovação integral da carteira nem autorização para Telegram. A regra original de envio mantém-se estrita; ativar outro tipo de notificação depende de autorização expressa e ainda não está implementado.

```toml
[selection]
mode = "observed"
min_closed_token_fraction = 0.8
notifications = "strict"
```

As oito verificações originais são calculadas nos dois modos, incluindo cobertura integral, inventário, taxas e condições de execução. Para produzir apenas essa avaliação, muda `selection.mode` para `"strict"` e aumenta a versão dos critérios. O modo observado não transforma as flags estritas desconhecidas em verdadeiras. Uma reprovação estrita impede também a seleção observada; uma indeterminação estrita é mantida como tal. `selection.notifications = "strict"` é a única configuração de envio aceite.

## Verificações obrigatórias no modo observado

As oito condições abaixo têm de ficar APROVADAS para a seleção observada no relatório. Uma condição REPROVADA ou INDETERMINADA bloqueia essa seleção; nenhuma pontuação permite compensá-la. Passar estas condições não basta para Telegram.

| Verificação | Condição inicial | Justificação e limite |
|---|---|---|
| Dados observados | Dados e avaliação até 24 horas; pelo menos uma fonte com histórico paginado concluído no período; sem conflitos de quantidades, identidades incoerentes ou informação futura | Uma fonte completa no âmbito consultado permite avaliar esse âmbito. Não demonstra que todos os mercados, posições ou operações da carteira tenham sido identificados. Quota esgotada sem outra fonte suficiente não permite passar. |
| Amostra | Pelo menos 20 **tokens distintos com operações fechadas**,14 dias de atividade e resultados observados em 4 semanas diferentes | Recompras do mesmo mint não aumentam a amostra. A condição reduz dependência de uma oportunidade; não estabelece significância estatística. |
| Contabilidade observada | Em cada token incluído: compras e vendas interpretáveis, quantidades reconciliadas, saldo observado final zero e valores históricos USD conhecidos | FIFO calcula o saldo bruto das operações observadas antes de taxas. Uma transferência, custo desconhecido, inventário em falta ou rota não interpretada impede incluir esse token como resultado conhecido. |
| Consistência observada | Saldo bruto agregado positivo; sucesso ≥50%; profit factor ≥1,3 quando há perdas; retorno mediano ≥0%; ≥60% de semanas lucrativas | São resultados do conjunto incluído, antes de taxas. Semanas inativas entram na distribuição; continuam necessárias 4 semanas com resultados. Sem perdas identificadas, PF fica desconhecido e uma condição explícita verifica ganhos sem perdas. |
| Dependência de vencedores | Melhor token ≤35% e três melhores ≤65% da soma dos resultados positivos; saldo positivo sem origens, sem melhor e sem três melhores | Calculado por token sobre o conjunto fechado incluído. Não demonstra ausência de perdas nos tokens excluídos nem sorte ou fraude. |
| Âmbito suficiente | Tokens fechados incluídos ≥80% dos tokens observados elegíveis para esta contagem | `min_closed_token_fraction=0.8` é uma heurística explícita para limitar a seleção de um subconjunto favorável. Os restantes tokens abertos ou desconhecidos ficam enumerados com motivo. Os 80% referem-se a tokens observados, não ao valor total nem à cobertura global da carteira. |
| Automatização observada | Risco abaixo do limite configurado, combinando pelo menos 2 famílias de sinais para excluir | São avaliados os eventos observados. Uma etiqueta ou um único indício não exclui. Identidade humana, coordenação global e ausência de MEV não ficam provadas. |
| Horizonte observado | Mediana de manutenção ≥300 segundos e ≤20% de fechos em até 60 segundos | Mede o tempo das operações incluídas. Liquidez, impacto no preço, custos de um seguidor e execução reproduzível continuam NÃO VERIFICADOS quando faltarem provas. |

O limiar de 80% não foi escolhido para aprovar uma carteira já observada. É uma regra configurável que obriga a mostrar a proporção incluída e as exclusões. Uma carteira sem 20 tokens fechados conhecidos ou sem as restantes condições não pode ser selecionada. Um resultado favorável nos tokens conhecidos não é apresentado como o resultado de toda a carteira.

## Aspetos que continuam não verificados

Inventário inicial global, taxas históricas completas em USD, cobertura global e posições abertas fora do conjunto incluído, liquidez de saída, execução, coordenação/MEV e etiquetas permanecem INDETERMINADOS quando não houver prova. No modo observado não são usados para fabricar um resultado nem para declarar aprovação integral; aparecem no relatório e na pré-visualização local como **NÃO VERIFICADO**.

Uma posição aberta não é encerrada ficticiamente nem tem o valor convertido em zero. Se o token não reunir as condições do conjunto fechado, é excluído das métricas desse conjunto e fica visível com o respetivo motivo. As taxas conhecidas são informativas; taxas em falta não são assumidas como zero. O saldo comunicado é explicitamente **antes de taxas**, não lucro líquido.

## Modo estrito

As verificações estritas originais continuam a avaliar qualidade/cobertura integral, amostra, contabilidade completa, consistência, concentração, risco de posições abertas, automatização e possibilidade de acompanhamento. Exigem as provas correspondentes de inventário inicial, custos e taxas, posições e marcas, liquidez, execução, coordenação e etiquetas. Uma lacuna mantém a verificação INDETERMINADA e bloqueia o envio nos dois modos de avaliação.

O limite estrito de perdas abertas é 20% dos custos de entrada identificados e a tolerância estrita a posições com custo desconhecido é 0%. Isso não é drawdown nem perda relativa ao património. O preço spot não prova saída executável. Estas conclusões estritas não são substituídas pelas métricas observadas.

## Definições quantitativas

A unidade da amostra é o endereço do token. SOL/wSOL e USDC/USDT usados na liquidação ficam separados para não inflacionar a amostra de tokens especulativos. A plataforma pump.fun é uma aproximação de descoberta e não prova a categoria de cada ativo.

No modo observado, apenas tokens fechados e interpretáveis entram no resultado, na taxa de sucesso, no profit factor, no retorno mediano e na concentração. Os melhores tokens são definidos pelo saldo bruto agregado por token. O denominador da concentração é **a soma dos resultados positivos**, não o saldo líquido depois das perdas. O número de tokens abertos, desconhecidos e excluídos e a fração fechada acompanham essas métricas.

A taxa de sucesso é a fração de tokens incluídos com resultado bruto positivo; resultados zero continuam no denominador. O retorno por token usa o resultado bruto dividido pelo custo FIFO consumido nas vendas. Ganho/perda médios também são por token. Os períodos são semanas ISO UTC não sobrepostas, incluindo semanas sem resultados; a apresentação usa Europe/Lisbon. Não se calcula drawdown sem uma série patrimonial adequada e ajustada a fluxos.

No modo estrito, o resultado agregado por token incorpora realizado e não realizado das posições abertas quando ambos forem conhecidos. A taxa de sucesso usa resultados realizados fiáveis e inclui resultados zero e posições abertas conforme essa definição. O relatório identifica o âmbito e a base de cálculo usados, sem misturar métricas brutas observadas com resultados estritos ou WAC fornecido pela Birdeye.

Os exemplos fictícios obrigatórios dão:

| Caso | Total | Melhor token | Sem melhor | Concentração do melhor |
|---|---:|---:|---:|---:|
| A |10 000 |12 000 |−2 000 |100%, se os restantes são perdas |
| B |4 000 |800 |3 200 |20%, no exemplo de cinco ganhos de 800 |

Estes exemplos distinguem dependência de um vencedor; não representam uma amostra suficiente para selecionar carteiras reais.

## Sinais, envio e limites gratuitos

Intensidade superior a 100 transações por dia, intervalos quase regulares em pelo menos 20 intervalos e atividade em 20 horas por dia durante pelo menos 3 dias são famílias de sinais heurísticos. São combinadas com rapidez e fechos no mesmo bloco/transação; duas pernas do mesmo swap contam como uma execução. Sinais de velocidade correlacionados não são contados como provas independentes. Usar uma interface de trading ou um bot Telegram para decisões próprias não é motivo isolado de exclusão.

O Telegram continua reservado a carteiras com as oito verificações estritas APROVADAS e uma nova validação de atualidade, proveniência, critérios e estado imediatamente antes do envio. A pré-visualização observada começa por **PRÉ-VISUALIZAÇÃO LOCAL — SEM ENVIO**, inclui **CARTEIRA SELECIONADA — DADOS OBSERVADOS** e as limitações essenciais, mas não é usada pelo notificador. Não se enviam listas sem critérios, dados fictícios, erros, logs ou resultados vazios. Não há compras, vendas ou assinatura de transações.

Os limites por execução e mensais continuam em `config.toml`, separados por Birdeye e Helius. Usam-se apenas Birdeye Standard e Helius Free, sem fornecedores adicionais, pagamentos ou upgrades. Atingir um limite não promove uma condição desconhecida. O contador local mantém reservas duráveis antes da rede e não conhece consumo de outras aplicações da conta.

No máximo são tentadas 5 mensagens por execução. Uma carteira já enviada só pode ser reconsiderada depois de 30 dias e de um período completamente posterior ao anterior. Tentativas incertas não são repetidas automaticamente. Fotografias e acompanhamentos posteriores permanecem separados e imutáveis; observações futuras não alteram a seleção original.


## Perfil experimental de lucro e evidência — `2026-10-30.profit.1`

Esta secção aplica-se exclusivamente a `config.profit.toml` e ao novo ranking experimental. As definições anteriores mantêm-se nos respetivos perfis. A experiência procura candidatas recentes com evidência de resultados e um horizonte observável de acompanhamento; devolve até três, mesmo quando só existe evidência parcial. **A, B e C são níveis de evidência, não aprovação integral, probabilidade de lucro ou autorização para Telegram. O Telegram permanece desligado neste perfil.** Os endereços ficam apenas no estado cifrado e no Excel privado; o resumo público contém contagens por estágio e um histograma de motivos.

### População, janelas e pré-filtro

A descoberta privilegia PnL realizado apresentado pela Birdeye, em vez de volume. Os tokens consultados têm idade de listagem até sete dias, liquidez e volume mínimos configurados; a idade de listagem não demonstra a data de criação do token. O aumento de preço a 24 horas é opcional. A presença de uma carteira entre os traders de dois ou mais tokens melhora a prioridade, sem contar esses tokens como ensaios independentes nem comprovar consistência. As etiquetas `sniper`, `bundler` e `insider` excluem esta população experimental por opção expressa de seleção; não constituem uma conclusão de fraude ou de identidade humana. Compradores iniciais Helius não têm prioridade neste perfil.

O período profundo é fixado antes das consultas em `[T−7 dias, T)`, com datas UTC. A recolha não admite informação posterior a T. Para o nível A é também necessária uma compra ou venda observada nas últimas 24 horas e dados recolhidos/avaliados dentro do limite de atualidade de 24 horas. Operações antigas não satisfazem essa condição. O pré-filtro consulta as durações 7d e, para as melhores candidatas, 30d, com `position_scope=duration_only` e contabilidade WAC do fornecedor. Esses resultados em USD são guardados separadamente da contabilidade própria em SOL e servem para ordenar/triagem, não como lucro líquido recalculado.

A triagem exige PnL positivo nas duas durações e limita a intensidade a 100 operações por dia. Um resumo global documentado de PnL e contagem de operações pode ser utilizado mesmo que só se tenha recolhido uma página da lista de tokens: essa lista fica **NÃO VERIFICADA** e não passa a ser uma amostra integral. PF, concentração e a cobertura final são novamente avaliados na análise própria. Um pré-filtro sem resumo suficiente não é promovido por falta de dados. O mínimo de um token no pré-filtro é uma condição de triagem económica; não substitui a amostra mínima para A.

**PnL positivo em 7d e 30d é um indício sobreposto, não persistência independente.** Por exemplo, +100 nos últimos sete dias e −50 nos 23 anteriores ainda produzem +50 em 30 dias. Além disso, os resumos WAC de janelas diferentes podem não ser aditivos. Os blocos diários não sobrepostos da contabilidade própria são apresentados separadamente; a janela de sete dias não demonstra consistência em vários regimes. Só fotografias e observações posteriores, sem informação futura, permitirão avaliar persistência fora da seleção.

### Contabilidade em SOL e posições abertas

Usa-se FIFO por endereço do token, com quantidades decimais, vendas parciais e recompras. A contrapartida em SOL só é reconhecida quando as pernas da mesma transação permitem interpretá-la com segurança. O delta nativo ajustado da taxa de rede e o delta wSOL não bastam, por si só, para provar o preço: transferências independentes, renda de contas, rotas ambíguas, operações de liquidez ou várias operações misturadas mantêm o custo desconhecido. SOL/wSOL usados na liquidação não aumentam a amostra de tokens especulativos. Uma transferência recebida não cria uma compra a custo zero.

Para cada token de custo conhecido calcula-se o cenário conservador:

`resultado conservador SOL = realizado FIFO SOL − custo SOL restante da posição aberta`.

Assim, uma posição aberta entra como perda total do custo que ainda não foi realizado; uma posição parcialmente vendida pode continuar com saldo positivo se os ganhos já realizados excederem esse custo. É um **cenário de perda**, não uma marcação a preço zero nem uma afirmação de que o ativo perdeu todo o valor. O valor marcado, quando disponível, é apresentado à parte e não prova capacidade de vender a posição inteira. Taxas SOL identificadas são deduzidas sem duplicação; taxas desconhecidas impedem A. Outros custos não identificados permanecem **NÃO VERIFICADOS** e o resultado não é anunciado como lucro líquido global.

Tokens `UNKNOWN` permanecem sem resultado, nunca zero. A fração desconhecida é `número de tokens UNKNOWN / número de tokens observados`, incluindo posições abertas no denominador. O teto é 20%: exige-se custo conhecido em pelo menos 80% dos tokens observados. Este limite substitui, **só neste perfil**, a exigência de 80% de tokens fechados; uma posição aberta de custo conhecido integra a amostra e o cenário conservador. Não é uma cobertura de 80% do património ou do valor investido. Se existir qualquer custo desconhecido, o subtotal conhecido pode ser calculado, mas o resultado de toda a carteira continua desconhecido.

PF, taxa de sucesso, retorno mediano e concentração são calculados por token sobre o cenário conservador conhecido. PF é a soma dos resultados positivos dividida pelo valor absoluto das perdas; taxas SOL não atribuídas a um token são consideradas sem duplicação. Sem perdas observadas não se fabrica um PF infinito: é necessária a condição explícita de ganhos positivos e ausência de perdas observadas. A taxa de sucesso é a fração de tokens conhecidos com resultado conservador estritamente positivo; resultados zero contam no denominador. Os melhores tokens são definidos pelo resultado agregado por mint. A concentração usa a soma dos resultados positivos, nunca o saldo depois das perdas. Os saldos sem os tokens de descoberta, sem o melhor e sem os três melhores são apresentados separadamente.

As razões são adimensionais, mas **a moeda de conta pode mudar o sinal do resultado**: comprar por 1 SOL quando vale 100 USD e vender por 1,1 SOL quando vale 80 USD dá +0,1 SOL e −12 USD. Não se converte todo o histórico pelo preço atual de SOL. A conversão histórica USD da contabilidade própria fica NÃO VERIFICADA; blocos diários de PnL continuam expressos em SOL.

### Condições e ranking

Todas as condições seguintes têm de estar demonstradas para o nível A; uma pontuação alta não compensa uma falha ou uma condição indeterminada:

| Condição do perfil | Valor e âmbito | Justificação/limitação |
|---|---|---|
| Histórico profundo | Sete dias completos, datas coerentes, sem entradas inválidas e atualidade até 24 h | Completude da consulta observada, não prova de inventário inicial ou cobertura de todos os mercados |
| Atividade recente | Compra/venda observada nas últimas 24 h | Evita classificar como ativa uma carteira com atividade apenas antiga |
| Custos SOL | Taxas de rede conhecidas no histórico analisado | Custos adicionais e conversão USD permanecem identificados como limitações |
| Amostra | Pelo menos 40 tokens distintos de custo SOL conhecido, abertos incluídos no cenário conservador | Resolução planeada da proporção; não prova estatística de rentabilidade |
| Custos desconhecidos | No máximo 20% dos tokens observados | Tokens desconhecidos continuam visíveis; o subtotal conhecido não é lucro global |
| Resultado | Subtotal conservador SOL estritamente positivo e PnL do fornecedor positivo em 7d e 30d | Bases SOL/FIFO e USD/WAC separadas; janelas sobrepostas |
| Payoff | PF ≥1,3 ou condição explícita de ganhos sem perdas observadas | Mantém o peso económico de ganhos e perdas, sem usar taxa de sucesso isolada |
| Remoção de vencedores | Subtotal conservador positivo sem o melhor e sem os três melhores tokens | Testa dependência de poucos vencedores; o resultado sem origens também fica visível |
| Concentração | Melhor token ≤35% e três melhores ≤65% da soma dos ganhos positivos | São tetos heurísticos, não testes de sorte ou fraude |
| Horizonte observado | Mediana de manutenção ≥300 s e fração de fechos rápidos ≤20% | Não prova preço, liquidez ou atraso reproduzível por um seguidor |
| Intensidade | No máximo 100 operações por dia | Exclui deste universo estratégias demasiado intensas para acompanhamento manual |

A referência de win rate é **40%, secundária**, com até quatro pontos de ranking e uma nota quando fica abaixo. Não é uma condição que possa anular PF, concentração, perdas ou cobertura. O retorno mediano é informativo. Este é o desvio deliberado ao ponto E proposto: obrigar a mediana a ser não negativa exclui estratégias com 40% de ganhos e perdas pequenas nas restantes operações, mesmo com payoff agregado favorável. PF e o cenário conservador continuam obrigatórios; a distribuição e a mediana ficam expostas.

Os níveis são: **A**, todas as condições observáveis acima cumpridas; **B**, pré-filtro favorável e análise profunda disponível mas parcial ou com condições por cumprir; **C**, apenas pré-filtro disponível/suficiente para ordenar, ou evidência ainda incompleta para B. Os motivos distinguem falta de prova de uma reprovação conhecida. As candidatas sem reprovações financeiras/estratégicas conhecidas vêm primeiro; depois ordenam-se pelo nível e pela pontuação. Há até três resultados, não uma promessa de obter três carteiras nem uma promoção automática para A.

A pontuação de 0–100 organiza evidência e tem pesos fixos: pré-filtros 7d/30d 6+6; cobertura profunda 10; amostra conhecida 10; subtotal conservador positivo 10; PF 12; saldos sem melhor/três melhores 6+6; concentração 8; cobertura de custos 8; horizonte/rapidez 10; win rate secundário 4; repetição em tokens de origem 4. Amostra, PF, cobertura de custos, win rate e repetição podem receber frações do respetivo peso. Esta pontuação não é uma probabilidade de lucro e não altera as condições de A. Os limites são definidos antes das novas observações, não ajustados às carteiras já vistas.

Inventário inicial global, cobertura de mercados fora da consulta, custos adicionais, liquidez executável, preços/atraso/impacto de um seguidor, coordenação, identidade humana e etiquetas globais continuam **NÃO VERIFICADOS**, mesmo em A. A ausência de um indício não prova a sua ausência na carteira. Nenhum nível permite compras, assinaturas de transações ou envio no Telegram.

### Amostra: cálculo e limites estatísticos

Vinte operações no mesmo token não são vinte observações independentes. Mesmo com 20 tokens independentes de probabilidade de ganho 50%, uma regra `win rate ≥50%` passa com probabilidade `P[Binomial(20, 0,5) ≥10] = 58,81%`. A referência secundária de 40% passaria em 86,84% desses casos. Nenhuma demonstra uma vantagem económica; a dimensão dos ganhos e das perdas é essencial.

Para tornar explícito o custo de um teste de proporções, considere-se apenas o modelo ideal de tokens independentes, `H0: p=0,5`, teste binomial exato unilateral com erro de tipo I ≤5%. Para cada n escolhe-se o menor k com `P[Binomial(n,0,5) ≥k] ≤0,05`. Calculando o poder na alternativa p, obtêm-se estes primeiros tamanhos de amostra:

| Probabilidade verdadeira de ganho p | n mínimo para poder ≥80% | Número crítico de ganhos nesse n | Poder nesse n | n mínimo para poder ≥90% |
|---|---:|---:|---:|---:|
| 60% | 158 | 90 | 80,57% | 213 |
| 65% | 69 | 42 | 80,21% | 93 |
| 70% | 37 | 24 | 80,71% | 53 |

Os números resultam da soma exata `Σ comb(n,i) × p^i × (1−p)^(n−i)`, de i=k até n. A discretização do teste altera o poder; não se deve substituir esta conta por uma aproximação nem interpretar 40 tokens como prova de uma taxa real de 60% ou 65%.

O mínimo operacional de **40 tokens conhecidos** usa outro objetivo: a meia largura máxima de um intervalo de Wilson nominal de 95% para uma proporção, no pior caso p=0,5, é `h = z / (2 × sqrt(n + z²))`, com `z=1,95996398454`. Exigir `h≤0,15` dá `n≥38,8414`, isto é, 39 observações; arredonda-se o desenho para 40, dando `h≈0,1480`. É uma escolha de resolução de amostra declarada antes dos resultados. Não foi calculada a partir das carteiras observadas nem significa significância de lucro. Amostras menores continuam no ranking B/C, com a insuficiência visível.

Não existe um n universal que prove rentabilidade sem assumir distribuição dos retornos, tamanho do efeito, variância e dependência. Tokens podem responder ao mesmo regime; a descoberta em top PnL seleciona vencedores e testar repetidamente carteiras introduz seleção adicional. Por isso os pressupostos de independência e o nível nominal dos cálculos acima não estão demonstrados neste scanner. A avaliação posterior deve usar fotografias imutáveis e períodos futuros separados; os resultados retroativos não são apresentados como acompanhamento real.
