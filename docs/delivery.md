# Registo da entrega

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
