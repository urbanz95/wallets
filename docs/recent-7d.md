# Experiência: carteiras ativas nos últimos sete dias

A branch `feat/recent-7d` testa o perfil `config.recent-7d.toml`. A `main` e a sua programação diária não são alteradas. Não há envio Telegram nesta experiência: o workflow não injeta credenciais Telegram no processo e a orquestração exige `notify=False`.

## O que muda

- Tokens usados na descoberta: listagem Birdeye entre0 e7 dias, liquidez mínima100000USD, volume24h mínimo100000USD e valorização24h mínima10%. Idade de listagem não prova criação do token.
- Até3 tokens por pesquisa, escolhidos dinamicamente; não existe lista fixa. Os traders vêm de `time_frame=7d`, com uma página por token. As candidatas dos vários tokens são intercaladas; carteiras presentes em vários tokens têm prioridade entre novas candidatas.
- Carteiras: até3 por execução, reservando espaço para2 análises pendentes e1 nova. A origem guardada precisa de continuar recente. Para passar a seleção observada, exige-se uma operação interpretável, com quantidade positiva, nas últimas24 horas.
- Avaliação: janela exata `[T−7 dias, T)`, com `T` guardado no início da pesquisa. Entram todos os tokens negociados nessa janela, incluindo perdedores, abertos e tokens antigos negociados agora. O filtro de idade aplica-se aos tokens que originam a descoberta.
- Consistência recente: pelo menos20 tokens fechados interpretáveis,4 blocos de24h ativos e3 blocos positivos. São sete blocos consecutivos ancorados em `T`, incluindo os inativos. Estes limites diários são heurísticas novas; não demonstram consistência mensal. Os campos estritos de14dias/4semanas continuam separados e não são aplicados à seleção recente.
- Mantêm-se os limites de desempenho, concentração e risco observável. Transferências e aquisições por apurar não passam a custo zero. A fração mínima de80% de tokens fechados continua explícita; é uma contagem de tokens, não cobertura do valor financeiro.

## Recolha e orçamento

Birdeye usa apenas `after_time`, com sobreposição de um segundo no início, e filtra o limite final localmente. Nunca envia os dois limites temporais que causaram HTTP422. Como o feed com offset pode mudar entre pedidos, duas ou mais páginas não certificam por si só a cobertura integral; Helius pode fornecer a cobertura paginada da janela.

Helius usa limites temporais documentados e cursores próprios do perfil recente, sem retomar a pesquisa legada desde1970. O tamanho da página é1000, dentro do contrato Free, com até5 páginas por endereço e o mesmo teto de5000 créditos por execução. Uma análise pendente conserva a janela original ao retomar; as métricas da nova pesquisa usam sempre apenas a sua janela de7 dias. Nenhuma atividade anterior entra no volume ou desempenho de seleção.

A descoberta Birdeye tem um teto de160CU e5 pedidos, dentro do teto global de800CU/40pedidos. O restante fica disponível para o histórico. PnL, holdings e detalhes opcionais estão adiados neste perfil; essa ausência permanece explicitamente NÃO VERIFICADA. Não se assume que os dados Helius sem USD permitem calcular resultados em USD.

Antes de qualquer consulta, a experiência reserva uma única vez os créditos no cofre principal, partilhando o teto mensal com a rotina diária. Só os contadores de quota desse cofre mudam. As avaliações, os cursores e os novos dados experimentais ficam no cofre cifrado separado `wallets-state-recent-7d`. As execuções usam o mesmo grupo de concorrência para não disputar o estado principal. Falhas conservam reservas e recuperação cifrada; não se usa cache como única persistência.

## Como executar no GitHub

1. Abre **Actions → Análise diária Birdeye e Helius gratuitas → Run workflow** no repositório.
2. No campo **Branch**, escolhe **feat/recent-7d** e executa. Nesta branch, esse workflow chama exclusivamente a experiência sem Telegram. A lista de workflows pode continuar a mostrar o nome da versão da `main`.
3. Consulta o resultado e o resumo. O artefacto `recent-7d-summary` contém apenas contagens e os tokens públicos usados na descoberta. Os endereços das carteiras e os relatórios completos ficam no estado cifrado, com cópia de recuperação de90dias.

Não são necessários novos secrets nem a reinicialização do cofre existente. Uma execução bem-sucedida significa que o programa terminou; zero selecionadas continua possível. O perfil observado não dá aprovação integral da carteira e não altera a política de envio Telegram.

## Tokens da última execução anterior à experiência

Fotografia de29-10-2026 às14:23UTC. As idades referem-se à listagem nessa data, não ao momento atual:

| Token | Endereço | Dias de listagem | Passaria o limite de7dias nessa fotografia? |
|---|---|---:|---|
| DogWifJob (JOB) | `9Pq7aLAbhDG2tTnS22cm6kuysZVcZpLFE368HWuypump` |1,80 | Sim |
| Effective Accelerationism (e/acc) | `CbcyNo7m1amFWqEQm2m4PLv1UNvpcL3C1Ujm6AkzpKoU` |33,71 | Não |
| USOE | `141R76cyQgaSH7p5EuSKUAmiBuGnLrtsaVRuLUikpump` |1,34 | Sim |
| Federal Oil Trust Fund (FOTF) | `5LNrYzNGUs9j6JXH397jTqAUMV8ayZC62zC3hedDpump` |1,11 | Sim |
| Veil (VEIL) | `HgTGdc29D5bDMrDvsCLnorxz899VyDj7ZN7FPa6vpump` |16,81 | Não |

O resultado do teste real desta branch é registado separadamente. A tabela anterior não é uma lista fixa de entrada nem uma recomendação de compra.

## Primeiro teste real e correções verificadas — 29-10-2026

A [execução real40927762747](https://github.com/urbanz95/wallets/actions/runs/40927762747), com o commit `9491296`, terminou com sucesso:25candidatas,3carteiras avaliadas e0selecionadas. Usou40pedidos Birdeye/536CU e27pedidos Helius/2700créditos reservados por pedido. A reserva mensal central conservadora foi800/5000, sem reembolso do que não foi usado. Não houve novos erros400/422 nem notificações.

Tokens dessa pesquisa, às18:08Europe/Lisbon:

| Token | Endereço | Dias de listagem na pesquisa |
|---|---|---:|
| HYPER INU (HI) | `3w2TvVmTrtq4kSkoFVrTFQgz6MX7ThzDVrx9r5c3pump` |0,06 |
| DogWifJob (JOB) | `9Pq7aLAbhDG2tTnS22cm6kuysZVcZpLFE368HWuypump` |1,95 |
| Dessa (DESSA) | `npz2FhoufQ7pKqhGpaipYWacrAkidyW2yDpVWSvpump` |3,21 |

O estado cifrado foi descarregado e autenticado. A análise identificou96colisões internas: o normalizador usava a mesma ID para saldo wSOL e movimento SOL nativo desconhecidos, revertendo o lote SQLite. A correção distingue o componente nativo apenas quando há colisão, conserva os restantes IDs e mantém ambos os custos/interpretações desconhecidos. O reprocessamento local de10570transações reais persistiu16452eventos, sem colisões, de forma idempotente. Os tempos originais e as fotografias anteriores foram preservados; este reprocessamento não fez novas consultas nem constitui uma nova captura de mercado.

Depois da correção, as3carteiras continuam bloqueadas:2têm histórico parcial por atingir5000transações nas5páginas;1tem histórico Helius completo com570transações, mas uma divergência de quantidades entre as fontes. A atividade interpretada nas últimas24horas ficou confirmada nas3. Não se alteraram limiares para as fazer passar.

O SQLite reprocessado ultrapassou o teto anterior de256MiB. O cofre experimental passou a usar compressão zlib sem perda antes da cifragem e um limite explícito de512MiB para o SQLite descomprimido. Na medição real,283,49MiB comprimiram para158,69MiB, com igualdade integral após descompressão. O hash continua a validar o SQLite original. O formato e o limite de256MiB do cofre principal permanecem iguais e compatíveis com o leitor da `main`; a compressão é ativada exclusivamente no cofre experimental. A recuperação valida tamanho, autenticação, hash, fim da compressão e integridade SQLite.

A suíte local passou441testes, incluindo15novos casos para colisões SOL/wSOL e armazenamento comprimido, além dos49casos acrescentados no perfil inicial de7dias. Testes com fixtures verificam algoritmos; as contagens anteriores vêm da captura real e do reprocessamento explicitamente separado.
