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

## Diagnóstico confirmado e correções — perfil atual `2026-11-03.profit.5`

1. A descoberta antiga ordenava por volume e descartava evidência de resultados. A nova usa `/trader/gainers-losers` em `1W`, `sort_by=realized_pnl`, como origem principal. Complementa com até cinco tokens listados na Birdeye nos últimos sete dias e `/defi/v2/tokens/top_traders`, `sort_by=realized_pnl`. São preservados PnL, cashflow disponível, contagens, datas e etiquetas. O filtro de valorização mínima de 10% deixa de ser obrigatório. Etiquetas explícitas sniper/bundler/insider excluem desta população experimental; não constituem prova de fraude.
2. O funil antigo era estreito. Agora enumera até 100 traders globais e 50 ocorrências nos cinco tokens, deduplica e consulta até nove pré-filtros 7d. Reserva depois até 120 CU para completar páginas de candidatas prioritárias; uma carteira parcial não é excluída por esse motivo nem tratada como integral. As três melhores recebem também 30d; só duas avançam para a análise profunda. A fila mantém rotação e usa repetição entre tokens e PnL recente nos empates. Para aprofundar, após a prioridade manual e o pré-filtro, contam a amostra potencial de 40 tokens, PF/concentração conhecidos, repetição entre tokens e, por fim, o montante de PnL. São prioridades heurísticas, não aprovação financeira.
3. Compradores iniciais tinham prioridade. Foram retirados deste perfil: comprar cedo não prova ser sniper, mas não é a população pretendida para acompanhamento.
4. A descoberta já não envia `max_trade=700` e frequência superior a 100 operações/dia gera um aviso, sem excluir a investigação económica. A avaliação comportamental combina frequência, fechos rápidos e duração das posições com cobertura suficiente. O horizonte mínimo de 300 segundos mantém-se como requisito separado. Cada análise profunda admite até seis páginas Helius de 1000 transações, mais o backfill limitado já documentado; atingir um limite mantém a cobertura parcial, sem aprovação por omissão.
5. A Helius não fornece USD. O novo FIFO usa apenas SOL/wSOL atribuído à mesma transação, com taxa de rede e rendas reconciliadas. Swaps Pump reconhecidos são suportados; outros swaps simples podem ser corroborados pelas duas pernas Birdeye, com hash, carteira, data, índice, quantidades e fluxo SOL coincidentes. Transferências adicionais, stablecoins como contrapartida, várias rotas e ambiguidades não são convertidas em custos inventados. O valor USD não converte retroativamente o histórico.
6. A exclusão de abertos criava seleção de um subconjunto. Agora cada token de custo conhecido contribui com realizado menos o custo restante, incluindo perdedores abertos; marcas, quando existirem, são um cenário separado. A persistência SQLite já existia: a lacuna era prova de desempenho em períodos mais longos, não ausência de armazenamento. Os headlines WAC positivos em 7d/30d são proxies sobrepostos, não persistência independente.

O endpoint global pode incluir tokens antigos **negociados agora**. Só as origens complementares comprovam listagem Birdeye até sete dias; listagem não demonstra data de criação do mint. O universo e as origens ficam guardados no estado privado. Não se introduziu uma lista fixa de carteiras nem DexScreener.

## Orçamento e armazenamento

| Estágio | Reserva Birdeye máxima | Reserva Helius máxima |
|---|---:|---:|
| Descoberta: ranking + lista + cinco tokens | 230 CU | 0 |
| Nove pré-filtros 7d | 270 CU | 0 |
| Refinamento 7d das candidatas prioritárias | 120 CU | 0 |
| Três consultas 30d | 90 CU | 0 |
| Duas análises profundas | 72 CU | 1 720 créditos |
| Total planeado | **782 CU** | **1 720 créditos** |

Estes valores são tetos, não cobranças fixas: as reservas seguintes são proporcionais ao número de candidatas que efetivamente avança. As duas análises previstas reservam até 72 CU Birdeye (seis páginas de 12 CU) e 1 720 créditos Helius, incluindo paginação recente, backfill e Parsed Events. O teto global Birdeye mantém-se em 800 CU. A consulta inicial continua limitada a uma página por carteira; não se multiplica esse custo por toda a população. Este perfil faz uma tentativa HTTP por consulta (`max_retries=0`), para um timeout não consumir as vagas das restantes candidatas. A execução seguinte pode voltar a tentar; o estado incremental é conservado. Outros perfis mantêm as retentativas próprias.

O refinamento automático tem até quatro páginas planeadas no total (120 CU), distribuídas por até duas candidatas. A estimativa inclui a primeira página, mesmo quando venha da cache; páginas não tentadas são reembolsadas, sem criar vagas adicionais nessa passagem. Só se tentam carteiras cuja estimativa cabe na reserva. Cada carteira admite no máximo dez páginas. Se as páginas mudarem de conteúdo, a consulta falhar ou o orçamento não chegar, conserva-se o resumo inicial e a paginação fica NÃO VERIFICADA; não se misturam páginas de execuções diferentes. Uma resposta nova completa com PnL não positivo atualiza a decisão corrente; o resumo positivo anterior fica apenas como histórico.

Num pedido manual cifrado não há descoberta e o teto de refinamento é 300 CU, até dez páginas planeadas, dentro dos mesmos 800 CU globais. Isso permite investigar uma carteira com mais de 100 tokens sem elevar o orçamento de todas as pesquisas. É um teto de trabalho, não uma promessa de completude: o feed pode mudar e a interpretação contabilística pode continuar insuficiente.

A reserva central é publicada duravelmente **antes** de conceder acesso à rede a cada estágio. Só créditos nunca tentados são devolvidos no fecho controlado. Timeouts e crashes não são reembolsados por presunção. O ledger partilhado usa o cofre principal, com o formato original compatível com a main; o ranking usa outro cofre. A mesma exclusão mútua `wallets-durable-state` impede execuções concorrentes no repositório. Consumo de outras aplicações da conta não é conhecido por este ledger.

Há pré-verificação de disco e tamanho antes de cada reserva e limite experimental de 128 MiB. Desde a revisão de 02-11-2026, a origem RPC comprimida fica conservada durante 35 dias para permitir reinterpretação; elimina-se apenas a cópia redundante depois de persistir a evidência autenticada. Receipts, interpretações versionadas e snapshots preservam hashes, saldos, custos e razões. Dados antigos de outros cofres não são apagados.

Snapshots do ranking são imutáveis e guardam configuração, datas e dados disponíveis. Não há simulação de acompanhamento, informação futura ou preço de execução atribuído a um seguidor. Datas internas UTC e apresentação Europe/Lisbon. Cópias cifradas de recuperação em artefactos têm retenção de 90 dias; o cofre em Releases é a fonte durável, não a cache nem o disco temporário.

## Interpretar a saída

O Excel contém endereço, ranking, score, âmbito, PnL WAC 7d/30d em USD separado das métricas FIFO SOL, custo aberto, concentração, motivos e NÃO VERIFICADO. A pontuação organiza evidência; não é probabilidade de lucro. A exige todos os critérios verificáveis do perfil; B indica análise profunda parcial/insuficiente; C apenas pré-filtro. Falhas financeiras conhecidas ficam assinaladas e ordenadas depois de candidatas sem essas falhas. Uma amostra insuficiente nunca recebe A.

O Excel separa **Pré-filtro económico 7d (Birdeye)**, **Acompanhamento observado** e **Avisos de investigação**. PnL favorável pode coexistir com acompanhamento por apurar. O resumo público separa o histograma de avisos do de reprovações e mantém apenas contagens, sem identidades.

O resultado SOL conhecido é um **subtotal**; tokens UNKNOWN não são zero. Inventário global, cobertura global, conversão USD, custos não identificados, saída executável, coordenação/MEV, identidade humana, etiquetas completas e persistência independente continuam NÃO VERIFICADOS. Nível A não significa aprovação integral nem reproduzibilidade garantida.

Ver [critérios](criteria.md) e [contratos oficiais e capacidades](capabilities.md). Testes usam fixtures e mocks; a validação autenticada e as suas contagens são registadas separadamente após a execução, sem publicar identidades.

A primeira validação real expôs uma diferença de contrato: `tokens[]` inclui ativos de liquidação SOL/stablecoins, enquanto `summary.unique_tokens` os exclui. São agora separados antes de comparar contagens e calcular métricas. Se outra divergência persistir, o headline permanece um proxy e PF/concentração da lista ficam NÃO VERIFICADOS; isso não inventa uma lista completa. O histograma conta ocorrências de motivos em toda a amostra consultada, não carteiras únicas; uma carteira pode ter vários motivos.

Se a fonte de tokens indicar HTTP429, a opção manual **probe_wallet_access** faz apenas uma consulta de 30CU à API de carteiras, usando um endereço já descoberto no estado cifrado. Não repete o pedido, não pesquisa novos endereços, não atualiza o ranking e não usa Helius ou Telegram. A resposta fica privada; só o código HTTP/contagens aparece no resumo. Serve para distinguir uma restrição da fonte de tokens de uma restrição também na API de carteiras; não autoriza contornar nenhuma quota.

Os contadores distinguem `pnl_details` (carteiras cujo pré-filtro foi invocado), `pnl_responses` (respostas recebidas, incluindo cache), `prefilter_passed` (pré-filtro provisório favorável) e pedidos HTTP efetivos no orçamento. Uma interrupção local não é contada como resposta recebida.

A sondagem real de30CU confirmou HTTP200 na Wallet API após HTTP429 na lista de tokens. Por isso o perfil experimental separa os circuitos de descoberta, Wallet API e histórico trader. Uma família que recebe429 interrompe os seus pedidos; outra família Standard confirmada pode tentar um pedido, com a mesma chave e os limites globais de CU/pedidos partilhados. Se a Wallet API receber429, não se tenta outra carteira para repetir o pedido. HTTP401/402/403 propaga a interrupção a todas as famílias. Não há novos fornecedores, troca de chaves ou repetição do endpoint bloqueado.

Os circuitos partilham também o último instante de pedido (1 pedido/s por conta) e `Retry-After`. O prazo é guardado no estado privado; esperas superiores a60s adiam a consulta antes de reservar créditos, em vez de reduzir o prazo ou insistir. Na ausência de `Retry-After`, um429 impõe60s antes de outra família tentar acesso.

A revisão `2026-10-30.profit.3` pode aprofundar7d quando a consulta30d falha tecnicamente, apenas para obter evidência B. PnL30d positivo continua obrigatório para A; dados30d desconhecidos não são presumidos positivos. Falhas conhecidas do pré-filtro continuam a excluir dessa passagem.


## Se o GitHub rejeitar o arranque

O workflow da main e o experimental partilham o nome registado em Actions, mas têm opções diferentes. O stub experimental aceita agora também os cinco campos antigos: `initialize_state`, `probe_only`, `probe_contracts`, `wallet` e `notify`. Assim, um formulário com esses campos não é rejeitado por nomes de inputs desconhecidos.

Neste branch `notify` e `wallet` são ignorados; não são enviados para comandos nem aparecem nos logs. `initialize_state` faz apenas validação sem quota e nunca reinicializa um cofre. `probe_only` e `probe_contracts` fazem somente a sondagem de um pedido Wallet API, até30CU; não executam a análise completa. Com todas as opções desmarcadas e wallet vazio, executa-se a experiência normal.

`validate_only` permite confirmar instalação, testes e arranque do workflow sem receber credenciais de mercado, consultar fornecedores, restaurar/gravar cofres ou enviar Telegram. É também usado para validar a compatibilidade do formulário sem gastar quota. Uma rejeição do formulário pode não criar uma execução; nesse caso, a mensagem apresentada pelo GitHub permite confirmar a causa. Falhas antigas continuam visíveis no histórico, mesmo depois da correção.

## Correções de 02-11-2026 e carteiras introduzidas manualmente

O perfil passa a completar mais páginas, guardar a origem comprimida para novas interpretações e rodar candidatas em fila privada. O Excel inclui a folha **Pré-filtros** com todas as carteiras consultadas, mesmo fora do top-3. O resumo público separa motivos de reprovação e falta de dados.

Para pedir uma carteira sem a publicar, executar localmente `PYTHONPATH=src:scripts python scripts/profit_enqueue.py`: pede a chave e o endereço com entrada oculta, cifra um pedido autenticado válido por12horas e inicia o workflow no branch experimental. O endereço não segue em claro no formulário nem na linha de comando. Actions descifra e guarda a fila no cofre, sob a exclusão mútua do workflow. O pedido tem identidade e proteção contra repetição. Não são necessárias permissões locais de upload de Releases nem novos secrets. O campo legado `wallet` continua vazio. O Telegram mantém-se desligado.

Parsed Events e o backfill não garantem que uma carteira passe. No Excel, um resultado Birdeye favorável continua a ser uma métrica do fornecedor; rentabilidade própria e acompanhamento exigem as restantes provas. A main e a política Telegram não foram alteradas.


## Correção de 03-11-2026: admissão económica e evidência de acompanhamento

Esta revisão substitui o veto de frequência das versões anteriores; não muda os resultados históricos guardados nem os limiares financeiros. O limite de 100 operações/dia continua visível como sinal heurístico. Sozinho não demonstra uma estratégia impossível de acompanhar. O pré-filtro conserva bloqueios por PnL não positivo, ausência de atividade, dados inválidos e informação futura. As etiquetas excluídas mantêm-se.

A fila conserva tokens de origem com a data de observação e expira-os aos sete dias. Mantém pedidos manuais, pendências e rotação; nos empates dá prioridade à repetição entre tokens e a PnL recente válido. Um pedido manual não renova artificialmente uma origem antiga.

Os testes desta revisão distinguem admissão para investigar de aprovação final, preservação de resumos quando a paginação falha, reservas limitadas e avisos comportamentais. A validação real da revisão deve ser consultada em [profit-validation.md](profit-validation.md); esta descrição da implementação não afirma que uma nova carteira já tenha sido aprovada. Os critérios de 40 tokens conhecidos, PF ≥1,3, concentração, resultados sem vencedores e restantes provas continuam em vigor. Telegram permanece desligado e a main não é alterada.
