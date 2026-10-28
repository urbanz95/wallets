# Registo da entrega

Data:28-10-2026. Repositório existente usado:urbanz95/wallets, público e inicialmente vazio. Não se alterou a visibilidade nem se substituiu histórico.

| Categoria | Evidência e limite |
|---|---|
| Implementado | Python/SQLite; cliente HTTP só para endpoints Birdeye Standard confirmados; descoberta pump.fun configurável; análise manual; paginação e atualização incremental; FIFO Decimal; resultados e perdas abertas; exclusões e concentração; oito gates; Telegram condicionado; relatórios HTML/CSV/JSON; fotografias e observações posteriores imutáveis; cofre cifrado e execução diária GitHub. |
| Testado localmente com fixtures/mocks | 170 testes automatizados aprovados com `PYTHONPATH=src python -m unittest discover -s tests -q`. Incluem todos os bloqueios Telegram pedidos, limites, erros, snapshots, corrupção e recuperação. Os testes do percurso completo Birdeye usam transporte simulado; os do Telegram bloqueiam rede. |
| Dependência criptográfica | O ambiente de desenvolvimento tinha cryptography50.0.0. Um ambiente isolado com o pin46.0.3 de pyproject.toml passou143 testes durante a revisão; o CI instala o pin e verifica a suite completa. |
| Documentação real consultada | Documentação oficial Birdeye atual, contratos e tabelas de acesso/CU; metadata GitHub e checkout vazio confirmados. Fontes e contradições constam em capabilities.md. |
| Dados on-chain reais | Nenhuma consulta autenticada: não havia BIRDEYE_API_KEY no ambiente. Contratos documentados e fixtures não são verificação de acesso real. `probe` limita-se a um endpoint e30 CU quando configurado; não certifica automaticamente os restantes. |
| Telegram real | Nenhum envio. Nenhuma carteira real foi aprovada. Credenciais e validação com dados reais ficam para a configuração posterior. |
| Limite material | A integração atual não demonstra toda a cobertura, inventário inicial, taxas, liquidez executável ou automatização/coordenação exigidos. Mantém flags desconhecidas e bloqueia aprovação real mesmo após configurar secrets. Esses requisitos não são desativáveis através da configuração. |
| Falta configurar | BIRDEYE_API_KEY Standard, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, STATE_ENCRYPTION_KEY e primeiro arranque manual do cofre. O utilizador pediu para adicionar os secrets depois da publicação. |

O sistema permite descoberta e análise parcial real depois da configuração, mas não deve ser apresentado como um serviço de sinais já validado. O acompanhamento posterior não inventa lucro do seguidor. A rotina diária está preparada para funcionar com o computador desligado; depende de Actions, secrets, quota e estado recuperável, com possíveis atrasos do GitHub.
