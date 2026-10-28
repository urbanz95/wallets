# Critérios, métricas e limites heurísticos

Versão inicial: `2026-10-28.1`; versão atual: `2026-10-28.2` (integração Helius autorizada, sem reduzir os limites de aprovação). Os valores efetivos estão em `config.toml`. São **heurísticas conservadoras de triagem**, sem validação estatística de rentabilidade e sem otimização para carteiras observadas. Alterações devem aumentar a versão; cada fotografia também guarda a configuração completa e o seu hash. Alterar um limite, mesmo mantendo a versão, invalida a elegibilidade de uma fotografia antiga para envio.

## Verificações obrigatórias

| Verificação | Valores iniciais / condição | Justificação e limite |
|---|---|---|
| Qualidade e atualidade | Dados e avaliação com até 24 horas; universo completo, sem falhas, sem truncagem, sem informação futura | Adequa-se à rotina diária. Uma página recolhida com sucesso não demonstra a cobertura do histórico; as lacunas do contrato permanecem indeterminadas. |
| Amostra | Pelo menos 20 tokens, 14 dias de atividade e realizações observadas em 4 semanas diferentes | Reduz dependência de uma única oportunidade ou semana. Não estabelece significância estatística. Recompras do mesmo mint não são novas observações. |
| Contabilidade | Inventário inicial e taxas conhecidos; 0% de posições com custo desconhecido | Evita transformar transferências ou custos ausentes em lucro. A percentagem usa contagem de tokens/posições, nunca soma unidades de tokens diferentes. |
| Consistência | Resultado agregado positivo; sucesso ≥50%; profit factor ≥1,3 quando há perdas; retorno mediano ≥0%; ≥60% de semanas lucrativas | Exige distribuição dos resultados e alguma margem sobre perdas. Sem perdas identificadas, PF não é infinito: fica desconhecido e uma condição explícita verifica ganhos sem perdas. Semanas inativas entram na distribuição; quatro semanas com realizações continuam necessárias. |
| Dependência de vencedores | Melhor ≤35% e três melhores ≤65% da soma dos resultados positivos; resultados positivos sem origens, sem melhor e sem três melhores | Veta dependência concentrada independentemente de outras métricas. Concentração não demonstra sorte, fraude ou causalidade. |
| Risco e posições abertas | Perdas abertas absolutas ≤20% dos custos de entrada identificados, com inventário, marcas e liquidez suficientes | É uma aproximação de exposição relativa ao capital identificado nas entradas, não drawdown nem perda relativa ao património. Preço spot não prova saída executável. |
| Automatização inadequada | Pelo menos 2 famílias de sinais para risco excessivo; cobertura suficiente de execução, etiquetas e coordenação | Um único indício não exclui. Sinais de velocidade, mesmo bloco e mesma transação pertencem à mesma família, para não contar três vezes o mesmo comportamento. Sem cobertura, INDETERMINADA mesmo sem indícios. |
| Possibilidade de acompanhar | Mediana de manutenção ≥300 segundos; ≤20% de fechos em até60 segundos; cobertura de execução e liquidez suficiente e risco de automatização aprovado | O tempo, isoladamente, não prova que um seguidor conseguiria reproduzir a operação. Custos, profundidade, atraso e execução desconhecidos continuam a bloquear. |

Não existe pontuação agregada com poder para ultrapassar uma destas oito verificações. Métricas informativas — por exemplo uma etiqueta Birdeye, resultado WAC do fornecedor ou número de operações — não substituem os requisitos.

## Definições quantitativas

A unidade é o endereço do token. SOL/wSOL e USDC/USDT usados na liquidação ficam numa rubrica separada, para não inflacionar a amostra de tokens especulativos; o universo e os resultados de liquidação aparecem no JSON. Não se afirma que a origem pump.fun prova que cada ativo é uma memecoin. O filtro de plataforma é uma aproximação explícita, configurável.

O resultado agregado por token usado nas exclusões e na concentração é o realizado mais o não realizado marcado das posições abertas, se ambos forem conhecidos. O relatório separa as duas parcelas. Marcas não são garantia de liquidação. Os melhores tokens são ordenados por esse resultado agregado. O denominador da concentração é **a soma das parcelas positivas por token**, não o resultado líquido depois de perdas.

A taxa de sucesso conta tokens com resultado realizado positivo divididos por todos os tokens com resultado realizado fiável, incluindo resultados zero e posições abertas. O retorno mediano usa resultado realizado dividido pelo custo FIFO consumido nas vendas; o montante de entradas ainda abertas não aumenta esse denominador. Ganho/perda médios também são por token. Os períodos são semanas ISO UTC não sobrepostas, incluindo semanas sem realizações; as datas de apresentação são Europe/Lisbon. Não se calcula drawdown sem uma série patrimonial adequada e ajustada a fluxos.

Os exemplos fictícios obrigatórios dão:

| Caso | Total | Melhor token | Sem melhor | Concentração do melhor |
|---|---:|---:|---:|---:|
| A | 10 000 | 12 000 | −2 000 | 100%, se os restantes são perdas |
| B | 4 000 | 800 | 3 200 | 20%, no exemplo de cinco ganhos de800 |

Estes exemplos distinguem dependência de um vencedor; não representam uma amostra suficiente para aprovar carteiras reais.

## Sinais e operação

Intensidade superior a100 transações por dia, intervalos quase regulares em pelo menos20 intervalos e atividade em20 horas por dia durante pelo menos3 dias são famílias de sinais heurísticos. São combinadas com rapidez e fechos no mesmo bloco/transação; duas pernas do mesmo swap contam como uma execução. Não se declara arbitragem, MEV, coordenação, sniping ou identidade humana provados apenas por estas medidas.

A rotina usa por defeito até5 tokens de descoberta, até10 carteiras, até5 páginas por endpoint e até40 pedidos/800 CU por execução. Esses limites controlam o custo, não autorizam ignorar o resto do histórico: atingir um limite torna a cobertura insuficiente. O teto local mensal é28 000 CU, abaixo dos30 000 do Standard. No Actions reserva-se antecipadamente o orçamento inteiro de800 CU antes da rede, mesmo se o consumo real for menor; 31 reservas totalizam24 800 CU. O consumo noutras aplicações não é conhecido pelo contador local.

No máximo são tentadas5 mensagens por execução. Uma carteira já enviada só pode ser novamente considerada depois de30 dias e de um período de análise completamente posterior ao anterior. Tentativas incertas não são repetidas automaticamente. Nenhum limite permite enviar erros, candidatas, demonstrações ou resultados vazios.


## Complemento Helius Free

A nova fonte tem orçamento próprio: até100 pedidos e5 000 créditos máximos por execução, teto local900 000 por mês e5 pedidos/segundo. São margens abaixo do Free documentado (1 milhão/mês,10 pedidos/segundo). O cliente reserva pelo máximo da página solicitada antes de cada tentativa; no Actions pré-reserva-se o orçamento inteiro. Não há compra de créditos nem upgrades. O preço de uma API paga não é confundido com consumo dos créditos incluídos no Free.

A descoberta inicial consulta até3 tokens por execução, até5 páginas de100 transações por endereço, até3 endereços (mint/curva/pools conhecidos) e até20 carteiras observadas por token. Estes limites podem deixar transações por analisar: os cursores ficam guardados e a cobertura global não é declarada completa. Novas compras e variantes Pump não suportadas permanecem desconhecidas.

O histórico e os eventos brutos são conservados separadamente. Transações coincidentes entre fontes entram uma única vez na contabilidade. Um valor histórico USD Birdeye só é associado à perna Helius se assinatura, instante, token, sentido e quantidade coincidirem inequivocamente. Taxas em lamports não são convertidas retroativamente com SOL atual. A ausência de uma compra no histórico do mint não prova que não ocorreu noutro mercado.
