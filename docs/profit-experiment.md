# Experiência de ranking privado por lucro

Branch `feat/profit-evidence-funnel`, criado de `077096d`. Não foi feito merge nem alterado o workflow da `main`. O objetivo é obter até três candidatas ordenadas com prova e lacunas visíveis. Encontrar candidatas B/C não demonstra que sejam lucrativas ou acompanháveis; não se promete obter três carteiras aprovadas.

## Executar sem programar

1. No GitHub, abrir **Actions → Análise diária Birdeye e Helius gratuitas → Run workflow** (o nome da lista pode continuar o da main).
2. Escolher o branch **feat/profit-evidence-funnel** e executar. Nesse branch o ficheiro `daily.yml` chama exclusivamente `profit.yml`, manual e sem Telegram.
3. Consultar `profit-summary-…`: contém apenas contagens, consumo e histograma. Não contém endereços de carteiras nem de tokens.
4. O Excel **ranking_carteiras.xlsx** é privado: é gerado no runner, mas não é publicado como artefacto. O ranking e os dados necessários para regenerá-lo ficam no cofre cifrado `wallets-state-profit-ranking`. A chave existente permite recuperar o Excel num ambiente privado. Não colar a chave, o estado decifrado ou o Excel em issues ou commits.

São usados os três secrets já configurados: `BIRDEYE_API_KEY`, `HELIUS_API_KEY` e `STATE_ENCRYPTION_KEY`. Este workflow não recebe secrets Telegram. Não há cron adicional: a experiência é manual para evitar somar automaticamente 800 CU diárias ao consumo da main.

### Descarregar o Excel privado no seu computador

Depois de instalar este projeto e a ferramenta GitHub CLI e de iniciar sessão com `gh auth login`, abra o terminal na pasta `wallets` e execute apenas:

```sh
python scripts/profit_export.py
```

Quando pedir `STATE_ENCRYPTION_KEY`, cole a mesma chave que guardou no secret do GitHub e carregue em Enter. A chave não aparece no ecrã; se já estiver configurada como variável de ambiente, não é pedida. O programa mostra só o caminho do ficheiro `reports/profit-private/ranking_carteiras.xlsx`: abra-o no Excel. Não são necessárias as chaves Birdeye/Helius nem as credenciais Telegram, não se gastam créditos de mercado e não se enviam mensagens.

O exportador lê exclusivamente o cofre `wallets-state-profit-ranking`, verifica e descifra uma cópia temporária e apaga essa base temporária no fim, incluindo em caso de erro. Não altera o cofre nem a base original. Se já tiver a base local da experiência, use `python scripts/profit_export.py --db state/profit.sqlite`; nesse caso não precisa de chave nem de acesso ao GitHub. Se ainda não existir uma execução guardada, primeiro execute a experiência em Actions. A pasta `reports/` é ignorada pelo Git: mantenha o Excel privado e não o publique como artefacto, issue ou commit. Os CSV/JSON/HTML auxiliares são anónimos.

## Diagnóstico confirmado e correções

1. A descoberta antiga ordenava por volume e descartava evidência de resultados. A nova usa `/trader/gainers-losers` em `1W`, `sort_by=realized_pnl`, como origem principal. Complementa com até cinco tokens listados na Birdeye nos últimos sete dias e `/defi/v2/tokens/top_traders`, `sort_by=realized_pnl`. São preservados PnL, cashflow disponível, contagens, datas e etiquetas. O filtro de valorização mínima de 10% deixa de ser obrigatório. Etiquetas explícitas sniper/bundler/insider excluem desta população experimental; não constituem prova de fraude.
2. O funil antigo era estreito. Agora enumera até 100 traders globais e 50 ocorrências nos cinco tokens, deduplica e consulta até13 pré-filtros7d. As três melhores recebem também 30d; só duas avançam para a análise profunda. Repetição em tokens de origem tem prioridade; depois contam PF/concentração conhecidos, diversidade limitada a 40 tokens e, por fim, montante de PnL. Estes pesos são heurísticos fixos, anteriores à nova execução.
3. Compradores iniciais tinham prioridade. Foram retirados deste perfil: comprar cedo não prova ser sniper, mas não é a população pretendida para acompanhamento.
4. O pré-filtro recusa mais de 100 trades/dia em média antes de gastar créditos Helius. Uma análise profunda consulta no máximo uma página de 1000 transações por carteira; atingir o limite deixa o histórico parcial, não aprovado por omissão.
5. A Helius não fornece USD. O novo FIFO usa apenas SOL/wSOL atribuído à mesma transação, com taxa de rede e rendas reconciliadas. Swaps Pump reconhecidos são suportados; outros swaps simples podem ser corroborados pelas duas pernas Birdeye, com hash, carteira, data, índice, quantidades e fluxo SOL coincidentes. Transferências adicionais, stablecoins como contrapartida, várias rotas e ambiguidades não são convertidas em custos inventados. O valor USD não converte retroativamente o histórico.
6. A exclusão de abertos criava seleção de um subconjunto. Agora cada token de custo conhecido contribui com realizado menos o custo restante, incluindo perdedores abertos; marcas, quando existirem, são um cenário separado. A persistência SQLite já existia: a lacuna era prova de desempenho em períodos mais longos, não ausência de armazenamento. Os headlines WAC positivos em 7d/30d são proxies sobrepostos, não persistência independente.

O endpoint global pode incluir tokens antigos **negociados agora**. Só as origens complementares comprovam listagem Birdeye até sete dias; listagem não demonstra data de criação do mint. O universo e as origens ficam guardados no estado privado. Não se introduziu uma lista fixa de carteiras nem DexScreener.

## Orçamento e armazenamento

| Estágio | Reserva Birdeye máxima | Reserva Helius máxima |
|---|---:|---:|
| Descoberta: ranking + lista + cinco tokens | 230 CU | 0 |
| Treze pré-filtros7d | 390 CU | 0 |
| Três consultas 30d | 90 CU | 0 |
| Duas análises profundas | 72 CU | 200 créditos |
| Total planeado | 782 CU | 200 créditos |

Estes valores são tetos, não cobranças fixas: as reservas seguintes são proporcionais ao número de candidatas que efetivamente avança. As duas análises previstas requerem até 72 CU Birdeye (seis páginas de 12 CU) e 200 créditos Helius; os 270 CU são o teto configurável, não uma reserva obrigatória. Este perfil faz uma tentativa HTTP por consulta (`max_retries=0`), para um timeout não consumir as vagas das restantes candidatas. A execução seguinte pode voltar a tentar; o estado incremental é conservado. Outros perfis mantêm as retentativas próprias.

A reserva central é publicada duravelmente **antes** de conceder acesso à rede a cada estágio. Só créditos nunca tentados são devolvidos no fecho controlado. Timeouts e crashes não são reembolsados por presunção. O ledger partilhado usa o cofre principal, com o formato original compatível com a main; o ranking usa outro cofre. A mesma exclusão mútua `wallets-durable-state` impede execuções concorrentes no repositório. Consumo de outras aplicações da conta não é conhecido por este ledger.

Há pré-verificação de disco e tamanho antes de cada reserva, limite experimental de 128 MiB e normalização seguida de poda atómica dos payloads RPC. Os receipts preservam prova mínima, assinatura, hash, saldos, custos, razões e manifestos; a sua integridade é verificada ao recuperar. Não se apagam payloads cuja normalização não ficou persistida. A experiência começa vazia, sem copiar centenas de MiB de payloads antigos. Dados antigos de outros cofres não são apagados.

Snapshots do ranking são imutáveis e guardam configuração, datas e dados disponíveis. Não há simulação de acompanhamento, informação futura ou preço de execução atribuído a um seguidor. Datas internas UTC e apresentação Europe/Lisbon. Cópias cifradas de recuperação em artefactos têm retenção de 90 dias; o cofre em Releases é a fonte durável, não a cache nem o disco temporário.

## Interpretar a saída

O Excel contém endereço, ranking, score, âmbito, PnL WAC 7d/30d em USD separado das métricas FIFO SOL, custo aberto, concentração, motivos e NÃO VERIFICADO. A pontuação organiza evidência; não é probabilidade de lucro. A exige todos os critérios verificáveis do perfil; B indica análise profunda parcial/insuficiente; C apenas pré-filtro. Falhas financeiras conhecidas ficam assinaladas e ordenadas depois de candidatas sem essas falhas. Uma amostra insuficiente nunca recebe A.

O resultado SOL conhecido é um **subtotal**; tokens UNKNOWN não são zero. Inventário global, cobertura global, conversão USD, custos não identificados, saída executável, coordenação/MEV, identidade humana, etiquetas completas e persistência independente continuam NÃO VERIFICADOS. Nível A não significa aprovação integral nem reproduzibilidade garantida.

Ver [critérios](criteria.md) e [contratos oficiais e capacidades](capabilities.md). Testes usam fixtures e mocks; a validação autenticada e as suas contagens são registadas separadamente após a execução, sem publicar identidades.

A primeira validação real expôs uma diferença de contrato: `tokens[]` inclui ativos de liquidação SOL/stablecoins, enquanto `summary.unique_tokens` os exclui. São agora separados antes de comparar contagens e calcular métricas. Se outra divergência persistir, o headline permanece um proxy e PF/concentração da lista ficam NÃO VERIFICADOS; isso não inventa uma lista completa. O histograma conta ocorrências de motivos em toda a amostra consultada, não carteiras únicas; uma carteira pode ter vários motivos.

Se a fonte de tokens indicar HTTP429, a opção manual **probe_wallet_access** faz apenas uma consulta de 30CU à API de carteiras, usando um endereço já descoberto no estado cifrado. Não repete o pedido, não pesquisa novos endereços, não atualiza o ranking e não usa Helius ou Telegram. A resposta fica privada; só o código HTTP/contagens aparece no resumo. Serve para distinguir uma restrição da fonte de tokens de uma restrição também na API de carteiras; não autoriza contornar nenhuma quota.

Os contadores distinguem `pnl_details` (carteiras cujo pré-filtro foi invocado), `pnl_responses` (respostas recebidas, incluindo cache), `prefilter_passed` (pré-filtro provisório favorável) e pedidos HTTP efetivos no orçamento. Uma interrupção local não é contada como resposta recebida.

A sondagem real de30CU confirmou HTTP200 na Wallet API após HTTP429 na lista de tokens. Por isso o perfil experimental separa os circuitos de descoberta, Wallet API e histórico trader. Uma família que recebe429 interrompe os seus pedidos; outra família Standard confirmada pode tentar um pedido, com a mesma chave e os limites globais de CU/pedidos partilhados. Se a Wallet API receber429, não se tenta outra carteira para repetir o pedido. HTTP401/402/403 propaga a interrupção a todas as famílias. Não há novos fornecedores, troca de chaves ou repetição do endpoint bloqueado.
