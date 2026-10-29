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
