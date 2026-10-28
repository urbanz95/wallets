# Observações posteriores sem informação futura

Cada avaliação é uma fotografia imutável dos critérios, dos dados e da decisão nesse instante. O identificador encontra-se no campo `id` do relatório JSON. A seleção original nunca é recalculada com eventos que só aconteceram depois.

Para guardar separadamente observações posteriores já recolhidas em execuções seguintes:

```sh
python -m wallets.cli followup --selection IDENTIFICADOR_DA_FOTOGRAFIA --db state/wallets.sqlite --output reports
```

O comando usa unicamente os eventos Birdeye já guardados da mesma carteira, com instante **estritamente posterior à data da avaliação original** e até à data atual. Não consulta outro fornecedor. É possível indicar `--as-of 2026-11-28T12:00:00Z`; o comando rejeita um corte no futuro. As observações são guardadas noutra tabela imutável e exportadas em `followup.json`.

Esta versão conta atividade efetivamente observada, mas não chama rentabilidade a essa contagem: o PnL posterior permanece INDETERMINADO sem inventário inicial, taxas e cobertura reconciliados. Dados fictícios continuam identificados. Ausência de operações guardadas não prova ausência de atividade real.

Não há simulação de execução ou copy trading. Nunca é atribuído a um seguidor o preço original de outra carteira; não se inventam atraso, dimensão, saída, custos ou impacto sem dados adequados. Relatórios retroativos não são apresentados como acompanhamento real validado.
