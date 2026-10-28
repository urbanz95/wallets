# Carteiras Solana — análise conservadora com Birdeye Standard

Esta ferramenta procura carteiras em tokens Solana (por defeito, da plataforma pump.fun, uma aproximação ao universo de memecoins), reúne dados permitidos pelo plano gratuito da Birdeye e aplica oito verificações obrigatórias. O Telegram recebe **apenas carteiras com todas as verificações APROVADAS e ainda atuais**. Se faltar informação, a verificação fica INDETERMINADA e não há envio. Zero carteiras aprovadas é um resultado válido.

O código não compra, vende, copia operações nem assina transações. Nunca precisa de seed phrases ou chaves privadas. GitHub aloja o código e o estado cifrado; Telegram apenas entrega as mensagens. Todos os dados de mercado e on-chain vêm da API pública Birdeye.

## Começar sem saber programar

1. Na sua conta Birdeye, obtenha uma chave do plano **Standard gratuito**. Não ative uma experiência paga nem faça um upgrade. Consulte o [relatório de capacidades](docs/capabilities.md): vários campos necessários à aprovação continuam sem cobertura suficiente.
2. No Telegram, abra o **@BotFather** oficial, envie `/newbot` e escolha um nome e um nome de utilizador. Guarde o token. Abra a conversa com o seu novo bot e envie `/start` **manualmente**; o programa não envia mensagens de arranque ou de teste.
3. Obtenha o identificador numérico do seu próprio chat a partir de `getUpdates` da Bot API, depois da mensagem manual. O procedimento sem publicar o token está em [Operação e recuperação](docs/operations.md). Não envie o token para esta conversa, para issues ou para ficheiros do repositório.
4. No GitHub, abra **urbanz95/wallets → Settings → Secrets and variables → Actions → New repository secret**. Crie estes quatro segredos:

   | Nome | O que colocar |
   | --- | --- |
   | `BIRDEYE_API_KEY` | Chave Birdeye Standard gratuita |
   | `TELEGRAM_BOT_TOKEN` | Token dado pelo BotFather |
   | `TELEGRAM_CHAT_ID` | Identificador do seu chat, nunca de um chat alheio |
   | `STATE_ENCRYPTION_KEY` | Chave Fernet; geração e cópia de segurança explicadas abaixo |

5. Guarde uma cópia segura da chave de cifragem fora do GitHub. Sem ela não poderá recuperar o estado. Gere-a uma única vez num terminal com Python e a biblioteca `cryptography`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Se não usa Python, peça ajuda a alguém de confiança para executar só este comando localmente. Não use um gerador de chaves num site público.
6. No GitHub, abra **Actions → Análise diária Birdeye Standard → Run workflow**. No primeiro arranque, marque `initialize_state`; deixe `notify` desmarcado. A ferramenta cria um cofre cifrado e executa a análise sem mensagens Telegram. Se o cofre já existir, essa opção recusa a reinicialização.
7. Nas execuções manuais seguintes, deixe `initialize_state` desmarcado. Marque `notify` apenas se quiser permitir mensagens elegíveis. A execução diária já permite esses envios; se nenhuma carteira passar, fica em silêncio.

Sem os segredos configurados, os testes, a demonstração e os relatórios de limitações continuam a funcionar, mas a análise on-chain real não é feita. A rotina diária exige os segredos antes de começar. As credenciais nunca devem ir para `config.toml`, `.env.example` ou comentários públicos.

## O que recebe e onde consultar

As análises geram HTML, CSV e JSON locais. Uma carteira pode ficar APROVADA, REPROVADA ou INDETERMINADA em cada verificação. Para voltar a exportar a partir de um estado restaurado, use `python -m wallets.cli report --db state/wallets.sqlite --output reports`. Relatórios e erros ficam na consola/localmente; nenhum destes avisos é enviado ao Telegram. As execuções automáticas guardam o histórico em SQLite cifrado; os relatórios em claro não são publicados como artefactos do repositório público.

A mensagem de uma carteira elegível inclui o endereço completo, período, tokens, resultado realizado e custos identificados, resultado sem o melhor token, concentração, perdas abertas, verificações, data e links públicos. “Aprovada” significa apenas que passou os critérios nessa avaliação. Não garante rentabilidade, identidade humana nem ausência de fraude.

## Utilização local

Precisa de Python 3.11 ou superior. Abra a pasta do repositório num terminal:

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
python -m wallets.cli demo --output reports
```

A demonstração usa dados fictícios e nunca pode enviar Telegram. Abra o relatório HTML produzido na pasta `reports`.

Com a chave Birdeye configurada como variável de ambiente, pode verificar o acesso com poucas consultas controladas e executar a análise:

```sh
python -m wallets.cli probe --wallet ENDERECO_PUBLICO_SOLANA --config config.toml --db state/wallets.sqlite
python -m wallets.cli run --config config.toml --db state/wallets.sqlite --output reports
```

Para analisar endereços introduzidos manualmente, acrescente `--wallet ENDERECO_PUBLICO_SOLANA` ao comando `run`; pode repetir a opção para várias carteiras. No GitHub, o formulário **Run workflow** também tem o campo opcional `wallet`. A descoberta automática não é substituída por uma lista fixa.

Use `python -m wallets.cli --help` e a ajuda de cada comando para as opções disponíveis. O modo local mantém o SQLite no seu disco. A execução GitHub usa checkpoints cifrados antes das operações que precisam de persistência durável.

## Como são tomadas as decisões

- **Dados:** atualidade, período, paginação, truncagem, todos os tokens e falhas registadas. Ausência de histórico não é prova de ausência de operações.
- **Contabilidade:** quantidades com `Decimal`, lotes FIFO, vendas parciais e recompras. Transferências e distribuições não têm custo zero presumido. Custos desconhecidos continuam desconhecidos. Métricas Birdeye são separadas dos cálculos próprios.
- **Consistência:** a unidade da amostra é o endereço do token; várias operações no mesmo token não aumentam artificialmente a amostra. Semanas não sobrepostas, perdas abertas e posições com custo desconhecido contam na avaliação.
- **Concentração:** calcula-se o resultado completo, sem os tokens da descoberta, sem o melhor e sem os três melhores. A concentração usa a soma dos resultados positivos, não o lucro total após perdas.
- **Automatização e possibilidade de acompanhar:** combinam-se vários sinais; um único indício não exclui automaticamente. Falta de dados sobre execução, coordenação e liquidez bloqueia conclusões fortes. Usar um bot de Telegram para executar decisões próprias não basta para excluir uma carteira.
- **Envio:** o motor volta a validar os oito requisitos e a atualidade imediatamente antes de reservar uma tentativa. A reserva é guardada de forma durável antes do pedido Telegram. Uma entrega incerta fica bloqueada para evitar repetições.

Os limites em [`config.toml`](config.toml) são **heurísticas conservadoras**, não parâmetros cientificamente validados nem otimizados para carteiras observadas. A versão, uma cópia dos valores efetivos e o hash dos critérios acompanham cada avaliação. A [justificação dos limites](docs/criteria.md) define todas as métricas e heurísticas. Não reduza verificações apenas para obter mensagens.

## Estado da validação e limites

| Área | O que significa nesta entrega |
| --- | --- |
| Implementado | Cliente limitado à Birdeye gratuita confirmada, descoberta, recolha, contabilidade, métricas, oito verificações, relatórios, notificações condicionadas e persistência cifrada |
| Testado com fixtures/mocks | Contabilidade, perdas abertas, concentração, paginação, quotas, indícios de automatização, dados em falta, bloqueios de envio, duplicações e cifragem/recuperação |
| Verificado com dados reais | A documentação oficial e o acesso ao repositório foram consultados. Não havia uma chave Birdeye nem credenciais Telegram no ambiente desta entrega: não foram feitas consultas on-chain autenticadas nem envios reais. O comando `probe --wallet ...` permitirá confirmar o acesso com uma consulta controlada depois da configuração |
| Limitações gratuitas | A disponibilidade de um endpoint não garante histórico completo, custos totais, inventário inicial, execução vendável, coordenação ou rótulos suficientes. Nesta versão o recolhedor mantém explicitamente a cobertura integral, taxas e execução como não demonstradas. Configurar secrets permite recolher e analisar dados parciais; **não torna carteiras reais aprováveis**. Essas lacunas mantêm verificações INDETERMINADAS |
| Falta configurar | Quatro segredos, primeiro arranque do cofre e confirmação de acesso da sua chave Standard; não é necessário um computador ligado depois disso |

O [acompanhamento posterior](docs/validation.md) usa snapshots imutáveis da seleção e separa o que já era conhecido do que só ocorreu depois. Não se atribui a um seguidor o preço original de entrada de outra carteira. Não há simulador de copy trading nem promessa de execução instantânea.

A rotina diária é às **07:17 UTC**: 07:17 em Lisboa no inverno e 08:17 no verão. Pode alterar o `cron` em [daily.yml](.github/workflows/daily.yml). O GitHub pode atrasar ou suspender agendamentos em repositórios públicos inativos; não há disponibilidade contínua garantida. O [guia de operação](docs/operations.md) explica retenção, recuperação e limites.
