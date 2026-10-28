# Helius Free: capacidades e limites reais

Verificação documental: **28 de outubro de2026**. A autorização posterior do utilizador permite acrescentar **Helius Free** à **Birdeye Standard gratuita**. Não autoriza outros fornecedores, upgrades, experiências pagas, compras de créditos ou envio/assinatura de transações. Helius fornece histórico on-chain; Birdeye continua a fornecer os dados de mercado integrados.

## Plano e evidência oficial

O plano Free inclui **1 milhão de créditos mensais** e **10 pedidos RPC/segundo**. A página oficial de RPC indica histórico em todos os planos, incluindo `getTransactionsForAddress`. O cliente adota um limite local mais baixo, no máximo5 pedidos/segundo, além dos tetos de pedidos e créditos por execução e por mês.

Fontes consultadas:

- [Planos atuais](https://www.helius.dev/docs/billing/plans): quotas Free e disponibilidade de serviços.
- [RPC e histórico nos planos](https://www.helius.dev/solana-rpc-nodes): FAQ «Does Helius RPC include historical Solana data?» e custos atuais.
- [Créditos atuais](https://www.helius.dev/docs/billing/credits): custo por resultados e restrições explícitas de outros métodos.
- [Contrato getTransactionsForAddress](https://www.helius.dev/docs/rpc/gettransactionsforaddress) e [referência API](https://www.helius.dev/docs/api-reference/rpc/http/gettransactionsforaddress).
- [Indexação de Solana](https://www.helius.dev/docs/rpc/how-to-index-solana-data): reconstrução, paginação, contas de token e limites das pesquisas por endereço.

O artigo de lançamento de outubro de2025 indicava100 créditos por pedido e acesso pago. Não é usado como contrato atual: as páginas acima documentam faturação variável de10 créditos por cada100 transações completas devolvidas. A documentação não substitui uma confirmação autenticada com uma chave Free. **Não foi feita nenhuma consulta Helius real nesta implementação sem chave configurada.**

## Contrato implementado

| Elemento | Contrato documentado e tratamento |
|---|---|
| Endpoint | `POST https://mainnet.helius-rpc.com/?api-key=…`, host fixo e TLS verificado. Redirecionamentos são recusados para proteger a chave. |
| Protocolo | JSON-RPC2.0, método `getTransactionsForAddress`. O cliente aceita exclusivamente este método de leitura. |
| Parâmetros | `params: [address, config]`. Configuração inclui `transactionDetails`, `sortOrder`, `limit`, `filters`, `paginationToken`, `encoding`, `maxSupportedTransactionVersion` e `commitment`, conforme referência. |
| Detalhes | `signatures` por defeito, ou `full`. Até1000 registos por resposta em ambos os modos. |
| Ordenação | `asc` do mais antigo para o mais recente; `desc` no sentido inverso. A ordem dentro de um slot pode usar `transactionIndex`; apenas o segundo do timestamp não demonstra ordem intrabloco. |
| Paginação | `result.data` e `result.paginationToken`, formato documental `slot:position`. Uma resposta incompleta ou cursor inválido não comprova o fim do histórico. |
| Filtros | Tempo `blockTime`, slots, estado `succeeded/failed/any`, movimentos e contas de token. Para historial integral de carteira, não limitar a swaps vencedores nem excluir falhas. |
| Contas de token | `filters.tokenAccounts`: `none` por defeito, `balanceChanged` ou `all`. Pesquisar somente o endereço principal sem contas de token pode omitir atividade. |
| Versões | O recolhedor deve definir explicitamente a versão máxima suportada e não assumir que só existem transações legacy. A documentação atual exemplifica `maxSupportedTransactionVersion: 1`. |
| Cobertura temporal | Mainnet: retenção documental ilimitada; devnet: duas semanas; testnet não suportada. O projeto consulta apenas mainnet. Retenção ilimitada não prova que uma consulta por um único endereço contenha todos os mercados de um token. |
| Depreciação | O método integrado não está marcado como depreciado nas páginas consultadas. Enhanced Transactions é legado em manutenção; não é chamado pelo cliente. |

### Consumo de créditos

| Resposta | Consumo documentado | Reserva local antes de cada tentativa |
|---|---:|---:|
| Apenas assinaturas |10 créditos por resposta |10 |
| Transações completas |10 por cada100 devolvidas, arredondando para cima, mínimo10 |10 × arredondamento superior de `limit/100` |
| Erro de API | A tabela atual indica que respostas de erro não são cobradas | A reserva é mantida, porque um timeout ou falha de processo pode esconder uma resposta aceite |

Exemplo: pedir até1000 transações completas reserva100 créditos, mesmo se o resultado final for vazio ou mais curto. Não há reembolso automático do contador local. Cada retentativa volta a reservar o máximo. Isto pode fazer parar a análise antes de esgotar efetivamente a quota, mas evita esquecer consumo após uma falha.

O contador tem o namespace `helius` separado do orçamento Birdeye. No GitHub Actions, o orçamento de cada fornecedor tem de ser guardado de forma durável antes de fazer consultas; a cache não substitui esse registo. O contador local não conhece o consumo de outras aplicações na mesma conta.

## O que este histórico permite avaliar

| Objetivo | Possibilidade e limitações |
|---|---|
| Descobrir compradores iniciais | É possível procurar atividade antiga e interpretar swaps. Não existe neste cliente um endpoint pronto «first buyers». A lista deve indicar o âmbito observado, a paginação e os mercados cobertos. |
| Histórico da carteira | Permite negociar operações observadas em todos os tokens, incluir contas de token, assinaturas falhadas e metadados. Paginação truncada ou respostas incoerentes continuam a bloquear cobertura completa. |
| Taxas | `meta.fee` identifica taxas de rede em lamports em transações completas. Não é conversão histórica USD, não demonstra todos os custos económicos nem autoriza somar prioridade duas vezes. |
| Sequência temporal | Slots e `transactionIndex` melhoram a ordem das operações. Ausência de campos necessários continua desconhecida. |
| Transfers, airdrops e liquidez | Os dados permitem separar movimentos quando as instruções são interpretáveis. Um saldo recebido, isoladamente, não demonstra compra nem custo zero. |
| PnL e consistência | Exigem normalização e reconciliação, custo histórico, inventário inicial e posições abertas. Histórico bruto não é lucro líquido calculado. |
| Automatização e acompanhamento | Histórico mais rico permite observar indícios adicionais. Não confirma identidade humana, coordenação global, liquidez executável ou capacidade de copiar operações. |

Para descobrir os primeiros compradores de um token, consultar apenas o mint pode omitir swaps que não referenciam esse endereço. É necessário considerar a curva de lançamento, os pools e as migrações relevantes. Contas atuais ou detentores atuais também não bastam para demonstrar quem comprou primeiro. Uma lista parcial pode originar candidatas, mas nunca deve ser apresentada como classificação global comprovada.

## Exclusões e falhas

- `getTransfersByAddress` exige Developer ou superior segundo a tabela atual: **não é chamado**.
- O cliente não chama endpoints de envio, simulação de bundles, trading, staking, streams pagos ou serviços de assinatura.
- HTTP401/402/403/404 e erros JSON-RPC interrompem o cliente; não desencadeiam upgrades ou experiências com outros endpoints.
- HTTP429 e falhas transitórias têm retentativas limitadas, com espera limitada e nova reserva por tentativa. Ao atingir a quota local, o pedido não é efetuado.
- Corpos, mensagens remotas e URLs autenticados não são apresentados em exceções, logs ou relatórios. Os únicos dados de autenticação necessários são guardados como GitHub Actions Secrets.

Cada uma das oito verificações obrigatórias mantém o seu papel. Acrescentar Helius melhora a evidência disponível, mas **não transforma cobertura desconhecida em aprovação**. Fixtures/mocks verificam o código e os bloqueios; não demonstram acesso real da chave Free nem validam rentabilidade de carteiras reais.
