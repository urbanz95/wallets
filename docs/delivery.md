# Registo da entrega

## Avaliação observada separada — 29-10-2026

Implementada a versão `2026-10-29.3`: o recolhedor deriva a conclusão da paginação do histórico observado em vez de a forçar permanentemente a falso. As garantias globais não são inventadas. Uma política adicional calcula resultados brutos FIFO apenas de tokens fechados interpretáveis, com valores USD históricos conhecidos; exige amostra, consistência, exclusão das origens/melhor/três melhores, concentração, âmbito mínimo e sinais observáveis de automatização/horizonte. Os limiares anteriores de amostra e desempenho foram preservados. A fração mínima de80% de tokens fechados é uma heurística nova e explícita; mede quantidade de tokens, não valor financeiro.

As oito verificações estritas continuam guardadas. Uma reprovação estrita comprovada veta a seleção observada. As oito limitações globais são obrigatórias nos registos; remover um aviso invalida a seleção. CSV/JSON/HTML mostram separadamente a seleção observada, os bloqueios integrais, tokens excluídos e motivos, antes de taxas e sem apresentar posições abertas desconhecidas como perdas zero.

**Telegram:** `Evaluation.eligible`, a verificação de envio e o notificador mantêm a política estrita original. A pré-visualização observada é apenas local. A revisão automática rejeitou a ativação de envios com verificações globais indeterminadas por conflito com a regra inicial; essa alteração depende de autorização expressa do utilizador. Não se implementou um atalho por configuração. Não faltam novos secrets e não é necessário reinicializar o cofre.

**Testes com fixtures/mocks:** a suíte local passou377 testes, incluindo dois percursos completos de recolha simulada, FIFO, avaliação, reabertura SQLite e relatórios. Um cenário sintético com20 tokens fechados em28 dias passa a seleção observada com garantias globais desconhecidas, mas permanece bloqueado para Telegram. Histórico truncado bloqueia mesmo quando o subtotal observado é favorável. Os testes não certificam carteiras reais.

**Dados reais guardados:** o snapshot cifrado `state-20261029T142545393683Z-6df3abbf3c4442d4a8b6351bcc46f493.enc` foi autenticado e restaurado para uma cópia privada. Às17:10 UTC reavaliaram-se40 carteiras com os tempos originais de recolha preservados:741 tokens observados,218 tokens fechados com valores conhecidos,40 novas fotografias e todas as40 anteriores conservadas. Resultado: zero seleções observadas, zero aprovações integrais e zero notificações no estado.19 carteiras passaram o critério de dados observados, mas não tinham ciclos fechados elegíveis suficientes;20 tinham cobertura insuficiente e uma apresentava conflito entre fontes. Os218 tokens fechados pertenciam a estes históricos insuficientes ou contraditórios, pelo que não fundamentam seleção. Não houve novas consultas Birdeye/Helius nem envio Telegram nesta validação.

Os relatórios em claro permanecem locais e ignorados pelo Git. A rotina diária continua preparada para produzir as duas avaliações e guardar fotografias no estado cifrado. A [execução agendada anterior](https://github.com/urbanz95/wallets/actions/runs/40890991714) terminou com sucesso, pelo que o cron já disparou; a afirmação anterior de que todas as execuções eram manuais deixou de ser atual.

## Correção dos pedidos Birdeye — 29-10-2026

A captura privada das mensagens da API identificou as causas dos dois erros: o histórico aceita apenas um de `before_time` ou `after_time`; o método WAC só admite `position_scope=duration_only`. A revisão corrige os pedidos e a sondagem, sem mudar os critérios de aprovação. Os307 testes locais passaram, incluindo paginação sem ordem presumida, exclusão de dados futuros, backfill, indexação atrasada e confidencialidade do diagnóstico. O [CI passou307 testes](https://github.com/urbanz95/wallets/actions/runs/40868190368). A [sondagem autenticada corrigida](https://github.com/urbanz95/wallets/actions/runs/40868317846) passou os quatro contratos, todos os checkpoints e a cópia cifrada, com82 CU reservados e zero Telegram. Usou uma candidata real descoberta automaticamente, com inventário anteriormente observado; não substituiu descoberta por uma lista fixa. Isso confirma os pedidos concretos, sem demonstrar cobertura integral nem aprovar carteiras.

A análise completa seguinte confirmou989 eventos Birdeye recolhidos, mas identificou ainda uma incompatibilidade na continuação das transferências: o servidor exige `wallet` juntamente com `cursor`, apesar de a nota documental indicar apenas o cursor. A continuação foi corrigida sem repetir filtros de tempo ou montante. A sondagem passou a verificar também uma segunda página, até cinco pedidos e92 CU reservados. A [sondagem de continuação corrigida](https://github.com/urbanz95/wallets/actions/runs/40869508780) passou cinco respostas, incluindo duas páginas de transferências, e confirmou os checkpoints cifrados. A validação de cursores malformados foi reforçada sem os confundir com fim do histórico. O [CI final do código](https://github.com/urbanz95/wallets/actions/runs/40869654046) passou311 testes.

A [execução normal final](https://github.com/urbanz95/wallets/actions/runs/40869867971), com o código final validado pelo CI acima, terminou com sucesso. O último estado cifrado foi descarregado, decifrado e autenticado: zero novos erros HTTP400/422,2965 eventos Birdeye acumulados,10 carteiras avaliadas nessa execução e30 carteiras com avaliação no estado. Zero aprovadas nessa execução e zero notificações no estado. Mantêm-se lacunas de cobertura, limites de pedidos, histórico parcial e uma divergência de quantidades entre fontes; esses casos permanecem indeterminados. O sucesso técnico não constitui aprovação de carteiras.

As execuções antigas conservam o seu resultado histórico. Para executar a correção, usar **Run workflow → main**; **Re-run jobs** de uma execução antiga volta a executar o respetivo código antigo. Os secrets e o cofre existente não precisam de ser reinicializados.

O histórico usa apenas o corte superior e filtra a janela localmente. Isso pode exigir mais páginas do que uma consulta com dois limites. O cursor guarda a cobertura confirmada, mas não permite saltar páginas ou operações antigas: pedidos repetidos são deduplicados e podem acrescentar operações indexadas com atraso. Ao atingir o orçamento ou o máximo de páginas, o histórico continua truncado e as verificações dependentes permanecem indeterminadas.

## Validação real e persistência — 29-10-2026

O utilizador configurou os cinco secrets em Actions. A [sondagem controlada](https://github.com/urbanz95/wallets/actions/runs/40771232173) confirmou um pedido Birdeye de posições (30 CU) e um pedido Helius de histórico (máximo10 créditos). O cofre foi inicializado e autenticado sem publicar os valores dos secrets.

A primeira análise falhou no checkpoint final. A revisão seguinte acrescentou compressão sem perda, eliminou a segunda cópia em cache das páginas Helius, limpou apenas cache expirada, compactou os snapshots e conservou recuperação cifrada de uploads pendentes. Não houve reset de estado nem reembolso de reservas. A [análise seguinte terminou com sucesso](https://github.com/urbanz95/wallets/actions/runs/40829026880), incluindo os checkpoints e o artefacto cifrado; o [CI dessa revisão](https://github.com/urbanz95/wallets/actions/runs/40828946770) passou282 testes.

Uma cópia do último snapshot foi descarregada, decifrada, autenticada e validada em SQLite. Contagens nessa fotografia:2472 transações Helius de origem,1467 eventos Helius normalizados,51 pares de descoberta carteira/token e10 avaliações. **Zero carteiras aprovadas; zero notificações.** Todas as oito verificações ficaram indeterminadas nas10 carteiras. Os resultados não constituem lucro líquido validado nem acompanhamento posterior.

A execução real também revelou erros Birdeye HTTP400/422, respostas sem a estrutura esperada e limites de orçamento. A leitura do total do inventário foi corrigida para `pagination.total`, conforme o contrato oficial. O cliente passou a identificar o método/caminho sem parâmetros privados e a aceitar diagnósticos de validação limitados, sem mensagens livres ou corpos autenticados. A recuperação foi também confirmada com GitHub CLI2.46, mantendo paginação integral dos snapshots. O limite por execução pode deixar candidatas por analisar; a rotação e a atualização incremental prosseguem nas execuções seguintes. Os contadores mensais preservam também as reservas da execução que falhou.

A [sondagem de quatro contratos](https://github.com/urbanz95/wallets/actions/runs/40840954635) reservou82 CU e usou uma candidata real já descoberta, sem retentativas, cache ou Telegram:

| Contrato | Resultado real nesta sondagem | Consequência |
|---|---|---|
| `GET /trader/txs/seek_by_time` | HTTP422 com os parâmetros documentados | Histórico Birdeye indisponível nesta consulta; cobertura indeterminada. Não há evidência suficiente para atribuir o erro ao plano ou inventar outros parâmetros. |
| `POST /wallet/v2/transfer` | Resposta200 com um registo e cursor | Acesso e primeira página confirmados; não prova cobertura integral. |
| `POST /wallet/v2/pnl/details` | HTTP400 com os parâmetros documentados | PnL fornecido indisponível nesta consulta; não substituído por lucro fabricado. |
| `GET /wallet/v2/current-net-worth` | Resposta200, `pagination` presente e `data.items=null` | Não interpretado como lista vazia; inventário e perdas abertas continuam indeterminados. |

A sondagem termina com código2 se algum contrato falhar, pelo que essa execução aparece a vermelho. O checkpoint final e a cópia cifrada passaram: este resultado distingue uma limitação de dados de uma falha de persistência. O [CI da revisão](https://github.com/urbanz95/wallets/actions/runs/40840837627) passou296 testes sem rede, incluindo o novo diagnóstico, a paginação e os bloqueios de envio. Não se fez nova análise integral apenas para gastar mais quota a repetir estes erros.

A execução diária continua programada para07:17 UTC. A validação manual acima manteve `notify=false`. O token/chat Telegram não foram usados para um envio real: só poderá acontecer após uma carteira elegível. O utilizador deve conservar a chave original do cofre e nunca reinicializar um estado existente. Não faltam novos secrets para prosseguir com a recolha.

## Revisão 0.2 — complemento Helius autorizado

Em 28-10-2026 o utilizador autorizou explicitamente Helius Free como complemento a Birdeye Standard, mantendo todas as outras restrições. Foram acrescentados cliente HTTP read-only, quotas separadas, histórico paginado e retomável, interpretação Pump.fun/PumpSwap por IDL oficial, compras iniciais **observadas** e reconciliação sem duplicação. A qualidade global dos primeiros compradores continua não demonstrada por um mint ou conjunto parcial de pools.

Os cinco secrets são BIRDEYE_API_KEY, HELIUS_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID e STATE_ENCRYPTION_KEY; os cliques estão em [configuration.md](configuration.md). O modo probe_only reutiliza o workflow e permissões anteriores, faz até uma consulta por fonte e nunca envia Telegram.

A recolha e os bloqueios passaram 268 testes locais com fixtures/mocks e SQLite real, incluindo a integração conjunta e todas as regressões Telegram anteriores. Não houve consultas autenticadas Helius/Birdeye nem envios Telegram nesta revisão; o acesso real fica pendente da configuração dos secrets. Taxas em lamports não são custos totais USD e nenhum requisito foi enfraquecido para aprovar carteiras.

## Entrega inicial0.1 — apenas Birdeye

Data: 28-10-2026. Repositório existente usado: urbanz95/wallets, público e inicialmente vazio. Não se alterou a visibilidade nem se substituiu histórico.

| Categoria | Evidência e limite |
|---|---|
| Implementado | Python/SQLite; cliente HTTP só para endpoints Birdeye Standard confirmados; descoberta pump.fun configurável; análise manual; paginação e atualização incremental; FIFO Decimal; resultados e perdas abertas; exclusões e concentração; oito gates; Telegram condicionado; relatórios HTML/CSV/JSON; fotografias e observações posteriores imutáveis; cofre cifrado e execução diária GitHub. |
| Testado localmente com fixtures/mocks | 170 testes automatizados aprovados com `PYTHONPATH=src python -m unittest discover -s tests -q`. Incluem todos os bloqueios Telegram pedidos, limites, erros, snapshots, corrupção e recuperação. Os testes do percurso completo Birdeye usam transporte simulado; os do Telegram bloqueiam rede. |
| CI no GitHub e dependência exata | [Execução concluída com sucesso](https://github.com/urbanz95/wallets/actions/runs/40669883814), commit 4f37fd425d2340b085928839dcee46518c87bfdf: 170 testes aprovados e demonstração A/B correta. O runner instalou cryptography 46.0.3 conforme o pin do projeto. |
| Documentação real consultada | Documentação oficial Birdeye atual, contratos e tabelas de acesso/CU; metadata GitHub e checkout vazio confirmados. Fontes e contradições constam em capabilities.md. |
| Dados on-chain reais | Nenhuma consulta autenticada: não havia BIRDEYE_API_KEY no ambiente. Contratos documentados e fixtures não são verificação de acesso real. `probe` limita-se a um endpoint e 30 CU quando configurado; não certifica automaticamente os restantes. |
| Telegram real | Nenhum envio. Nenhuma carteira real foi aprovada. Credenciais e validação com dados reais ficam para a configuração posterior. |
| Limite material | A integração atual não demonstra toda a cobertura, inventário inicial, taxas, liquidez executável ou automatização/coordenação exigidos. Mantém flags desconhecidas e bloqueia aprovação real mesmo após configurar secrets. Esses requisitos não são desativáveis através da configuração. |
| Falta configurar | BIRDEYE_API_KEY Standard, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, STATE_ENCRYPTION_KEY e primeiro arranque manual do cofre. O utilizador pediu para adicionar os secrets depois da publicação. |

O sistema permite descoberta e análise parcial real depois da configuração, mas não deve ser apresentado como um serviço de sinais já validado. O acompanhamento posterior não inventa lucro do seguidor. A rotina diária está preparada para funcionar com o computador desligado; depende de Actions, secrets, quota e estado recuperável, com possíveis atrasos do GitHub.
