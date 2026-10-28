# Configurar as cinco credenciais

Não precisas de escrever código para criar os segredos no GitHub. Os dois pequenos auxiliares abaixo servem apenas para obter o identificador do chat e criar a chave de cifragem no teu computador. Nunca coloques valores reais nesta conversa, em issues, em screenshots ou em ficheiros enviados para o repositório.

## 1. Obter as duas chaves gratuitas

**Birdeye:** abre o [início de sessão oficial](https://data.birdeye.so/user/auth/sign-in). Mantém o plano **Standard gratuito**. Na área `Settings → Security`, usa `Generate key` e copia a chave. Os passos oficiais estão no [Quickstart Birdeye](https://data.birdeye.so/docs/quickstart). Não atives uma experiência paga nem compres créditos.

**Helius:** abre o [dashboard oficial](https://dashboard.helius.dev), cria ou seleciona o teu projeto no plano **Free** e copia a API key. Para `HELIUS_API_KEY` queremos só a chave: não copies o URL RPC completo nem o texto `?api-key=`. Não atives planos pagos, pagamentos adicionais ou upgrades.

As chaves permitem gastar a quota gratuita da tua conta. Se o limite for atingido, a ferramenta adia a recolha; não compra capacidade adicional.

## 2. Criar o bot Telegram

1. No Telegram, procura o **@BotFather** oficial, com a identificação oficial do Telegram.
2. Envia `/newbot`, escolhe um nome e um nome de utilizador para o bot.
3. Guarda o token que o BotFather te apresentar: será `TELEGRAM_BOT_TOKEN`.
4. Abre a tua conversa privada com o novo bot e envia `/start` **manualmente**. Esta mensagem é tua; a ferramenta não envia testes nem mensagens de arranque.

O identificador do chat é um número; não é o teu nome de utilizador nem o nome do bot. O bot enviará exclusivamente para esse chat configurado.

## 3. Obter o identificador e criar a chave de cifragem

Estes passos são executados num terminal interativo no teu computador; não redireciones a saída para logs. Se não tens Python, instala-o a partir de [python.org](https://www.python.org/downloads/) ou pede ajuda a alguém de confiança para executar apenas estes auxiliares localmente. Não uses páginas públicas para gerar chaves ou descobrir o chat com o teu token.

Na página do repositório, usa **Code → Download ZIP**, extrai a pasta e abre um terminal nessa pasta. Executa:

```sh
python -m pip install -e .
python scripts/telegram_chat_id.py
```

O segundo comando pede o token do BotFather de forma oculta. Faz apenas uma leitura `getUpdates`; **não envia mensagens**. Depois de confirmares que a conversa é tua, copia o número para `TELEGRAM_CHAT_ID`. Se não encontrar mensagens, envia outra mensagem manual ao teu bot e repete. Se observar várias conversas privadas, não escolhe uma automaticamente.

Para criar a chave do cofre uma única vez:

```sh
python scripts/generate_state_key.py --show-key
```

Copia a chave mostrada para `STATE_ENCRYPTION_KEY` e guarda uma cópia num gestor de palavras-passe. O auxiliar também cria `.local-secrets/state-encryption.key`, com permissões privadas. Esse ficheiro não deve ser publicado. Não voltes a gerar uma chave se o cofre já tiver sido usado: sem a original não recuperas o histórico e os registos de envio. O auxiliar recusa substituir uma chave existente.

Os dois auxiliares recusam execução dentro de GitHub Actions para evitar publicar chaves ou identificadores nos logs. O histórico cifrado não precisa de seed phrase nem de uma chave privada de carteira.

## 4. Colocar os valores no sítio certo

Abre [Settings → Secrets and variables → Actions](https://github.com/urbanz95/wallets/settings/secrets/actions) no repositório **urbanz95/wallets**. Para cada linha da tabela:

1. Clica em **New repository secret**.
2. Em **Name**, escreve o nome exatamente como aparece abaixo.
3. Em **Secret**, cola só o valor correspondente, sem aspas.
4. Clica em **Add secret**.

| Name | Secret |
|---|---|
| `BIRDEYE_API_KEY` | A chave da Birdeye Standard gratuita |
| `HELIUS_API_KEY` | Só a chave da Helius Free, sem o URL RPC |
| `TELEGRAM_BOT_TOKEN` | O token fornecido pelo BotFather |
| `TELEGRAM_CHAT_ID` | O número da tua própria conversa privada com o bot |
| `STATE_ENCRYPTION_KEY` | A chave criada pelo auxiliar local |

Usa **Secrets**, não o separador **Variables**. Não coloques os valores em `config.toml`, `.env.example`, código ou comentários. Depois de guardares um secret, o GitHub não volta a mostrar o valor; podes substituí-lo quando necessário. Conserva especialmente a chave original do cofre.

## 5. Primeiro arranque: poucas consultas e nenhum Telegram

Abre **Actions → Análise diária Birdeye e Helius gratuitas → Run workflow**.

- Marca `initialize_state` apenas se nunca criaste o cofre.
- Marca `probe_only` para confirmar acesso com consultas mínimas.
- No campo `wallet`, coloca um **endereço público Solana** que queiras consultar. Nunca uma seed phrase ou chave privada.
- Deixa `notify` desmarcado.

A sondagem faz no máximo uma consulta Birdeye e uma Helius, sem retentativas: reserva 30 CU Birdeye e 10 créditos Helius. Não percorre listas de endpoints, não analisa nem aprova carteiras e não recebe credenciais Telegram no passo de consulta. Usa o mesmo cofre e as mesmas proteções de quota da execução diária.

Se passar, confirmaste apenas esses pedidos e o acesso das tuas chaves naquele instante. Não demonstraste a cobertura de todos os dados ou endpoints.

Nas execuções seguintes, deixa `initialize_state` desmarcado. Para recolher e analisar, deixa também `probe_only` desmarcado. O campo `wallet` passa a ser opcional para análise manual; a descoberta automática continua a funcionar. Podes deixar `notify` desmarcado durante a primeira análise completa.

A execução diária permite notificações elegíveis. Se nenhuma carteira passar **todas** as verificações, não chega mensagem alguma. A Helius melhora a informação disponível, mas não remove as limitações de cobertura, contabilidade ou automatização: os casos insuficientes continuam INDETERMINADOS.

## 6. Depois da configuração

O computador pode ficar desligado: a rotina é executada no GitHub. Os resultados técnicos ficam em Actions e no estado cifrado, nunca no Telegram. Consulta o [guia de operação e recuperação](operations.md) para exportar relatórios, recuperar o cofre e compreender a retenção. O GitHub pode atrasar ou suspender agendamentos; não se promete funcionamento contínuo.
