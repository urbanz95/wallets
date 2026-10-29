# Carteiras Solana — Birdeye Standard e Helius Free

A ferramenta descobre candidatas em tokens Solana, reconstrói o histórico disponível e aplica oito verificações obrigatórias. O Telegram recebe **apenas carteiras com todas as verificações APROVADAS e ainda atuais**. Dados insuficientes deixam a verificação INDETERMINADA e bloqueiam o envio. Zero carteiras aprovadas é um resultado válido.

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

A Helius não torna automaticamente uma carteira aprovável. Taxas em SOL não são custos históricos em USD sem uma conversão comprovada; movimentos desconhecidos não são compras de custo zero. Inventário, execução, liquidez, coordenação e automatização insuficientemente avaliados continuam a bloquear a aprovação. Consulta os [contratos e limites Birdeye](docs/capabilities.md) e [Helius](docs/helius-capabilities.md).

## O que recebes e onde consultar

As análises geram HTML, CSV e JSON locais. Para voltar a exportar a partir de um estado restaurado:

```sh
python -m wallets.cli report --db state/wallets.sqlite --output reports
```

As execuções automáticas guardam o histórico em SQLite cifrado. Relatórios em claro e SQLite nunca são publicados como artefactos do repositório público. Os logs de Actions mostram estados técnicos e limitações, sem credenciais. Nenhum erro, candidata, resultado vazio ou demonstração é enviado ao Telegram.

Uma mensagem elegível inclui endereço completo, rede, período, tokens, resultado realizado e tratamento de custos, resultado sem o melhor token, concentração, perdas abertas, verificações e data. “Aprovada” significa apenas que passou os critérios naquela avaliação: não garante rentabilidade, ausência de fraude ou identidade humana.

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

- **Dados:** atualidade, período, paginação, truncagem, universo de tokens e falhas registadas. Uma resposta bem-sucedida não prova cobertura completa.
- **Contabilidade:** `Decimal` e FIFO, com vendas parciais e recompras. Custos desconhecidos permanecem desconhecidos; métricas do fornecedor ficam separadas dos cálculos próprios.
- **Consistência:** a unidade da amostra é o endereço do token. Repetir operações no mesmo token não aumenta a amostra. Semanas não sobrepostas, perdas abertas e custos desconhecidos contam.
- **Concentração:** resultado completo, sem origens, sem o melhor e sem os três melhores. O denominador é a soma dos resultados positivos por token.
- **Automatização e acompanhamento:** vários sinais combinados; uma etiqueta ou o uso de uma interface de trading não excluem automaticamente. Falta de cobertura bloqueia conclusões fortes.
- **Envio:** todas as oito verificações devem passar. Critérios, evidência e atualidade são novamente verificados antes do HTTP. A tentativa é guardada remotamente antes do envio; entregas incertas não se repetem automaticamente.

Os limites em [`config.toml`](config.toml) são **heurísticas conservadoras**, sem otimização para fazer carteiras observadas passar. Cada fotografia guarda a versão, a configuração efetiva e o respetivo hash. A [justificação dos critérios](docs/criteria.md) define as métricas. Nenhuma pontuação compensa uma reprovação ou indeterminação.

## Estado da validação

| Categoria | Limite prático |
|---|---|
| Implementado | Descoberta, recolha e reconciliação conservadora Birdeye/Helius, contabilidade, métricas, oito gates, relatórios, Telegram condicionado e persistência cifrada |
| Testes com fixtures/mocks | Exercitam cálculos, normalização, paginação, quotas, falhas, proveniência, bloqueios Telegram e recuperação; não certificam dados reais |
| Dados reais | Acesso autenticado Birdeye/Helius confirmado; análise de29-10-2026 recuperada do cofre:10 carteiras avaliadas, zero aprovadas. Persistem lacunas de cobertura e erros de contrato; ver [entrega](docs/delivery.md) |
| Limitações | Contratos e histórico recolhido podem ser insuficientes. Falta de dados nunca se transforma em aprovação |
| Configuração desta instalação | Secrets adicionados pelo utilizador e cofre inicializado. Não voltar a marcar initialize_state; guardar uma cópia da chave. Entrega Telegram real ainda não testada por não existir carteira elegível |

O [acompanhamento posterior](docs/validation.md) guarda observações separadas das seleções imutáveis, sem usar informação futura e sem atribuir ao seguidor o preço original da carteira. Não há simulador de copy trading.

A rotina diária é às **07:17 UTC**: 07:17 em Lisboa no inverno e 08:17 no verão. Podes alterar o `cron` em [daily.yml](.github/workflows/daily.yml). O GitHub pode atrasar ou suspender agendamentos em repositórios públicos inativos. O [guia de operação](docs/operations.md) explica quotas, retenção e recuperação.
