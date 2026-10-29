# Carteiras Solana — Birdeye Standard e Helius Free

A ferramenta descobre carteiras em tokens Solana e avalia a consistência das operações disponíveis. O modo predefinido **observed** acrescenta uma seleção para consulta nos relatórios, com oito verificações dos dados observados. Mostra resultados brutos antes de taxas e enumera os aspetos **NÃO VERIFICADOS**. Esta seleção não representa aprovação integral da carteira nem autoriza o seu envio para Telegram.

A avaliação observada separa o que os dados permitem medir das provas globais em falta. As oito verificações **estritas** originais continuam guardadas e são sempre obrigatórias para Telegram: `selection.notifications = "strict"` é a única configuração de envio aceite. Uma reprovação estrita também impede a seleção observada; uma indeterminação estrita fica explícita no relatório. Ativar notificações de seleções observadas depende de autorização expressa para alterar a política de envio e ainda não está implementado. Zero seleções continua a ser um resultado válido.

A integração Helius Free foi autorizada em 28-10-2026 como complemento à Birdeye Standard. Birdeye fornece a seleção de tokens, medidas de mercado e dados já integrados; Helius acrescenta histórico on-chain e procura compradores iniciais observados. Não há fornecedores adicionais nem chamadas a endpoints pagos. GitHub aloja o código e o estado cifrado; Telegram entrega as mensagens. Não se compram ou vendem ativos, não se assinam transações e nunca se pedem seed phrases ou chaves privadas.

## Começar sem saber programar

O [guia de configuração passo a passo](docs/configuration.md) explica como criar o bot, obter o identificador do teu chat, gerar a chave de cifragem e fazer o primeiro arranque sem Telegram.

Os cinco segredos vão para [GitHub → Settings → Secrets and variables → Actions](https://github.com/urbanz95/wallets/settings/secrets/actions), usando **New repository secret**:

| Nome | Valor |
|---|---|
| `BIRDEYE_API_KEY` | Chave do plano Standard gratuito da Birdeye |
| `HELIUS_API_KEY` | Só a chave do plano Free da Helius |
| `TELEGRAM_BOT_TOKEN` | Token dado pelo `@BotFather` oficial |
| `TELEGRAM_CHAT_ID` | Número da tua própria conversa privada com o bot |
| `STATE_ENCRYPTION_KEY` | Chave de cifragem criada localmente e guardada também fora do GitHub |

Não coloques estes valores nesta conversa, em **Variables**, em ficheiros ou em comentários públicos. Sem as chaves, os testes e a demonstração funcionam, mas não confirmam acesso real aos serviços.

O primeiro arranque recomendado é **Actions → Análise diária Birdeye e Helius gratuitas → Run workflow**: marca `initialize_state` uma única vez, marca `probe_only`, preenche `wallet` com um endereço público e deixa `notify` desmarcado. A sondagem faz poucas consultas e nunca envia Telegram. Depois, desmarca `initialize_state` e `probe_only` para recolher e analisar. Os detalhes estão no [guia de configuração](docs/configuration.md).

## Descoberta e limites reais

A seleção inicial de tokens usa idade de listagem, liquidez, volume e valorização configuráveis. A plataforma pump.fun é a aproximação inicial ao universo de memecoins; não prova a categoria de cada ativo. Traders relevantes da Birdeye e compradores observados no histórico Helius originam candidatas, com registo dos tokens e datas da descoberta.

“Primeiros compradores observados” não significa uma lista global garantida: consultar só o mint pode não cobrir todas as curvas, pools e migrações. Transações, transferências, distribuições, liquidez e falhas precisam de interpretação e cobertura adequadas. Depois da descoberta, a análise usa o histórico disponível de todos os tokens da carteira, incluindo perdas e posições abertas; não avalia apenas o token que a revelou.

A seleção observada exige pelo menos 20 tokens distintos com operações fechadas e interpretáveis,14 dias de atividade e resultados em 4 semanas, além dos critérios de consistência, concentração, automatização e tempo de manutenção. Pelo menos 80% dos tokens observados têm de pertencer ao conjunto fechado incluído. Os restantes ficam enumerados como abertos ou desconhecidos, com motivo; não se escolhem só vencedores nem se inventa custo zero para transferências.

A Helius não prova, por si só, inventário inicial, taxas USD completas, cobertura global, liquidez ou execução reproduzível. No modo observado essas lacunas acompanham a seleção como NÃO VERIFICADO; no modo estrito bloqueiam aprovação. Histórico insuficiente para as métricas observadas, informação futura, dados desatualizados ou fontes contraditórias bloqueiam essa seleção. A conclusão da paginação de uma fonte no período não prova cobertura integral da carteira. Consulta os [critérios](docs/criteria.md), os [contratos Birdeye](docs/capabilities.md) e [Helius](docs/helius-capabilities.md).

## O que recebes e onde consultar

As análises geram HTML, CSV e JSON locais. Para voltar a exportar a partir de um estado restaurado:

```sh
python -m wallets.cli report --db state/wallets.sqlite --output reports
```

As execuções automáticas guardam o histórico em SQLite cifrado. Relatórios em claro e SQLite nunca são publicados como artefactos do repositório público. Os logs de Actions mostram estados técnicos e limitações, sem credenciais. Nenhum erro, lista sem critérios, resultado vazio ou demonstração é enviado ao Telegram.

O relatório pode mostrar uma **pré-visualização local, sem envio**, com endereço completo, rede, período, tokens fechados e universo observado, saldo bruto antes de taxas, resultados sem origens/melhor/três melhores, concentração, exclusões, verificações, data e limitações. Posições abertas ou desconhecidas não são apresentadas como perdas zero. “Selecionada” significa apenas que passou os critérios observáveis naquela avaliação: não garante lucro líquido, liquidez, ausência de fraude, identidade humana ou capacidade de repetir a estratégia.

## Utilização local

Precisas de Python 3.11 ou superior. Na pasta do repositório:

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
python -m wallets.cli demo --output reports
```

A demonstração usa dados fictícios, mantém-se separada do estado operacional e nunca envia Telegram. Abre o relatório HTML na pasta `reports`.

Com as chaves configuradas como variáveis de ambiente, podes sondar ambos os serviços ou recolher dados:

```sh
python -m wallets.cli probe --provider both --wallet ENDERECO_PUBLICO_SOLANA --db state/wallets.sqlite
python -m wallets.cli run --config config.toml --db state/wallets.sqlite --output reports
```

Para análise manual, acrescenta `--wallet ENDERECO_PUBLICO_SOLANA` ao comando `run`; podes repetir a opção. O modo local mantém o SQLite no teu disco; o GitHub usa checkpoints cifrados antes das operações que precisam de persistência durável. `python -m wallets.cli --help` mostra as opções.

## Como são tomadas as decisões

A configuração predefinida é:

```toml
[selection]
mode = "observed"
min_closed_token_fraction = 0.8
notifications = "strict"
```

- **Dados:** pelo menos uma fonte com histórico paginado suficiente no período; atualidade, identidades e quantidades coerentes; sem informação futura. Uma fonte recolhida não prova todo o universo da carteira.
- **Contabilidade observada:** valores históricos USD e FIFO dos tokens fechados interpretáveis. O resultado é anterior às taxas. Transferências, rotas desconhecidas, tokens abertos e custos por apurar ficam identificados e fora dessas métricas.
- **Consistência:** tokens distintos, dias, semanas não sobrepostas, taxa de sucesso, profit factor e retorno mediano do conjunto incluído. Os limiares de amostra e desempenho existentes são preservados.
- **Concentração:** resultado sem origens, sem o melhor e sem os três melhores; o denominador é a soma dos resultados positivos por token. Uma concentração excessiva bloqueia independentemente das restantes métricas.
- **Âmbito:** tokens fechados incluídos representam pelo menos 80% dos tokens observados. A percentagem é uma heurística, não cobertura do património. Os tokens excluídos e motivos ficam visíveis.
- **Automatização e horizonte:** sinais observados combinados e mediana de manutenção de pelo menos 300 segundos; até 20% de fechos rápidos. Uma etiqueta isolada não exclui e não há declaração de identidade humana.
- **Envio:** as oito verificações estritas originais têm de passar, mesmo com `mode = "observed"`; critérios, estado e atualidade são novamente verificados antes do HTTP. A tentativa é guardada remotamente antes do envio e entregas incertas não se repetem automaticamente.

Para produzir apenas a avaliação original, usa `selection.mode = "strict"` e uma nova versão dos critérios. As verificações estritas são calculadas nos dois modos e continuam a ser a regra de envio. Os resultados parciais observados não as substituem.

Os limites em [`config.toml`](config.toml) são **heurísticas**, sem otimização para fazer carteiras observadas passar. A [justificação dos critérios](docs/criteria.md) explica os dois âmbitos. A versão `2026-10-29.3`, a configuração efetiva e o respetivo hash ficam em cada fotografia. Avaliações antigas não se tornam elegíveis automaticamente por mudar a política.

## Estado da validação

| Categoria | Limite prático |
|---|---|
| Implementado | Descoberta e reconciliação Birdeye/Helius; avaliação estrita preservada e seleção observada com critérios próprios; relatórios e pré-visualização local; Telegram com regra estrita original; persistência cifrada |
| Testes com fixtures/mocks | Exercitam cálculos, normalização, paginação, quotas, proveniência, bloqueios, seleção observada e recuperação; um teste positivo não certifica uma carteira real |
| Dados reais | Reavaliação local de dados reais já guardados: 40 carteiras, 741 tokens observados e 218 ciclos fechados com valores conhecidos em históricos ainda insuficientes ou contraditórios; zero seleções e zero envios. Sem novas consultas de mercado. Ver [entrega](docs/delivery.md) |
| Limitações | Contratos e histórico podem ser insuficientes. Métricas observadas não demonstram o resultado de toda a carteira; requisitos sem prova ficam expressamente NÃO VERIFICADOS |
| Configuração desta instalação | Secrets adicionados pelo utilizador e cofre inicializado. Não voltar a marcar initialize_state; guardar uma cópia da chave. A nova política exige nova avaliação; não é necessário criar outros secrets nem reinicializar o cofre |

O [acompanhamento posterior](docs/validation.md) guarda observações separadas das seleções imutáveis, sem usar informação futura e sem atribuir ao seguidor o preço original da carteira. Não há simulador de copy trading.

A rotina diária é às **07:17 UTC**: 07:17 em Lisboa no inverno e 08:17 no verão. Podes alterar o `cron` em [daily.yml](.github/workflows/daily.yml). O GitHub pode atrasar ou suspender agendamentos em repositórios públicos inativos. O [guia de operação](docs/operations.md) explica quotas, retenção e recuperação.
