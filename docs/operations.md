# Operação, privacidade e recuperação

## Configuração segura

Segue o [guia passo a passo](configuration.md). Os cinco segredos são `BIRDEYE_API_KEY`, `HELIUS_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` e `STATE_ENCRYPTION_KEY`. Criam-se em [Settings → Secrets and variables → Actions](https://github.com/urbanz95/wallets/settings/secrets/actions), através de **New repository secret**. Não usar **Variables** nem publicar os valores em código, issues ou relatórios. `.env.example` contém apenas nomes vazios.

Os auxiliares locais `scripts/telegram_chat_id.py` e `scripts/generate_state_key.py` ajudam a obter o chat e a criar a chave. O primeiro só lê `getUpdates` depois de uma mensagem manual tua; nunca envia testes. O segundo cria uma chave Fernet e recusa substituir um ficheiro existente. Ambos recusam execução em Actions para não publicar identificadores ou chaves nos logs.

Guarda a chave do cofre num gestor de palavras-passe e no secret `STATE_ENCRYPTION_KEY`. Uma chave errada, conteúdo alterado ou snapshot inválido interrompem a execução. Não substituas a chave apenas no GitHub: snapshots antigos continuam a precisar da original. Uma migração exige recuperação controlada. A ferramenta nunca pede chaves privadas Solana.

## Avaliação observada e regra de envio

A versão de critérios `2026-10-29.3` usa por defeito `selection.mode = "observed"` para acrescentar a seleção dos dados observados aos relatórios: dados, amostra, contabilidade, consistência, concentração, âmbito suficiente, automatização e horizonte. Uma seleção pode gerar uma pré-visualização local, identificada como **SEM ENVIO**, com os aspetos sem prova como **NÃO VERIFICADO**. Não ativa notificações observadas. `selection.notifications = "strict"` é a única configuração aceite: o Telegram continua a exigir as oito verificações estritas originais. Mudar essa regra depende de autorização expressa e ainda não está implementado.

A seleção usa saldo bruto anterior às taxas dos tokens fechados e interpretáveis; exige pelo menos 20 tokens,14 dias,4 semanas e 80% dos tokens observados no conjunto fechado. Os restantes limiares são os [critérios documentados](criteria.md), não parâmetros ajustados aos resultados já vistos. Tokens excluídos, abertos ou desconhecidos têm de ser apresentados com motivo. Não há aprovação de lucro líquido, cobertura global ou estratégia reproduzível por omissão.

O modo `selection.mode = "strict"` mantém as oito verificações integrais originais e bloqueia quando faltam provas de inventário, custos/taxas, cobertura, posições, liquidez, execução, coordenação ou etiquetas. A avaliação estrita continua no relatório em ambos os modos; uma reprovação estrita também veta a seleção observada. Dados incorretos, futuros, desatualizados, contraditórios ou insuficientes para os critérios observados bloqueiam essa seleção. A conclusão da paginação do feed consultado é distinta da prova de cobertura global da carteira.

A política, a configuração e o hash fazem parte da fotografia imutável. Mudar o modo ou os critérios exige nova avaliação antes de qualquer envio; avaliações antigas não são reenviadas automaticamente. Os mesmos cinco secrets e o mesmo cofre continuam a ser usados. Não voltar a inicializar nem substituir a chave para aplicar esta atualização.

## Primeiro arranque e execução diária

Em **Actions → Análise diária Birdeye e Helius gratuitas → Run workflow**, marque `initialize_state` uma única vez. O cofre usa o release `wallets-state` neste mesmo repositório. Se já existir, o programa recusa voltar a inicializar, mesmo que esteja vazio ou corrompido.

Para o primeiro teste de acesso, marca também `probe_only`, fornece um endereço público em `wallet` e mantém `notify` desmarcado. São feitas apenas duas consultas controladas, sem análises ou Telegram. Para diagnosticar contratos de carteira, `probe_contracts` executa até cinco consultas Birdeye (92 CU reservados; uma por endpoint e uma continuação de transferências quando existir cursor, sem retentativas), exige `wallet` e também impede análise e Telegram. Não é necessário marcar as duas opções. Se algum contrato falhar ou a estrutura necessária estiver ausente, a sondagem termina com código 2 e aparece a vermelho; isto não autoriza análise nem Telegram. O checkpoint final conserva as reservas e o diagnóstico usa apenas métodos, caminhos públicos, tipos de campos e contagens. Nas execuções seguintes, deixa `initialize_state` desmarcado; desmarca também `probe_only` e `probe_contracts` para a análise completa.

A rotina é sequencial:

1. Restaurar e autenticar a geração cifrada mais recente.
2. Reservar orçamento e guardar checkpoints antes de consultas reais.
3. Recolher e avaliar; guardar o resultado de forma durável.
4. Se o envio estiver permitido, verificar novamente as oito condições estritas, a proveniência, o estado persistido e a atualidade; a seleção observada não substitui estas condições.
5. Registar uma tentativa pendente e guardar esse estado remotamente **antes** de contactar o Telegram.
6. Guardar o resultado conhecido do envio, ou a incerteza de entrega, e o checkpoint final.

A sondagem e a análise usam o mesmo workflow, as mesmas permissões previamente existentes e o grupo de concorrência `wallets-durable-state`, para não alterar o mesmo estado em simultâneo. `cancel-in-progress: false` evita cancelar uma execução a meio de um envio. Não execute outra cópia do programa sobre o mesmo estado ao mesmo tempo; a cópia local deve ficar separada.

O `cron` é UTC, não acompanha automaticamente a mudança de hora de Lisboa. As datas guardadas são UTC; os relatórios apresentam Europe/Lisbon. O agendamento GitHub pode atrasar e pode ser desativado após inatividade do repositório público. Consulte Actions regularmente; não há mensagens Telegram de erro, ausência de resultados ou arranque.

## Persistência e retenção

O disco do runner GitHub desaparece no fim de cada execução. Por isso, o SQLite não depende desse disco nem da cache do Actions:

- Cada checkpoint produz um snapshot SQLite consistente, cifrado e autenticado.
- Os snapshots são novos assets no release `wallets-state`; os anteriores não são substituídos. O nome inclui a data UTC e um identificador aleatório.
- A geração local tem de corresponder à última geração remota. Uma cópia antiga não pode substituir silenciosamente o estado mais recente.
- Só ficheiros cifrados vão para o artefacto de recuperação do Actions, com retenção configurada de 90 dias: `.enc` contém o último checkpoint confirmado e `.pending.enc`, quando existir, contém uma tentativa ainda não confirmada. A política da conta GitHub pode encurtar esse prazo.
- Os assets de releases não têm expiração automática por retenção de Actions. Continuam a existir até serem apagados ou até ocorrer uma limitação da plataforma. Não são uma garantia eterna.
- O código limita cada estado a 256 MiB. O número de snapshots cresce a cada checkpoint. Acompanhe o espaço ocupado e mantenha uma cópia cifrada externa. A limpeza é manual e deve preservar sempre a geração mais recente, várias anteriores e os backups externos.

A cache e as transações Helius de origem usam compressão sem perda; payloads TEXT de estados antigos continuam legíveis. As páginas completas Helius não são duplicadas na cache porque as transações e os cursores já ficam guardados. Só a cache expirada é removida: provas, avaliações, notificações e reservas de quota são preservadas. O snapshot SQLite é compactado antes da cifragem, mantendo o limite de 256 MiB.

O repositório permanece público. O cofre usa **cifragem**, não uma branch pública apresentada como privada. Os nomes dos assets revelam horários e frequência de checkpoints, mas não o conteúdo. Uma pessoa com escrita no repositório pode apagar os assets ou alterar o código; proteja a conta GitHub e reveja alterações antes de lhes dar acesso aos secrets.

Não se publicam relatórios HTML/CSV/JSON nem SQLite em claro como artefactos. Para consultar localmente, descarregue o estado cifrado com GitHub CLI autenticado, configure a chave localmente e use `python scripts/state_vault.py restore --db state/wallets.sqlite`; não publique esse ficheiro restaurado.

## Recuperação de falhas

Se a rede ou o último checkpoint falhar, as operações dependentes ficam bloqueadas. Na execução seguinte, a ferramenta restaura a geração remota mais recente. Não usa automaticamente uma geração anterior: isso poderia perder reservas de envio e voltar a notificar uma carteira.

Uma falha de upload conserva também uma cópia local `.pending.enc` no artefacto cifrado. Essa cópia **não é restaurada automaticamente** e não prova que o checkpoint remoto foi confirmado. A recuperação exige autenticar o conteúdo, reconciliar a geração e verificar as reservas de quota e de envio antes de retomar; não se deve renomear ou publicar a cópia pendente às cegas. Os logs de erro apresentam apenas a causa sanitizada, códigos HTTP quando disponíveis e contagens/tamanhos de armazenamento, sem payloads nem credenciais.

Se o último asset estiver corrompido, a chave tiver sido perdida, o release tiver sido apagado ou não houver um snapshot autenticável, a rotina **para sem reinicializar**. Verifique:

1. Se o secret `STATE_ENCRYPTION_KEY` é o original.
2. Se existe o release `wallets-state` e a geração mais recente.
3. Se há uma cópia cifrada válida nos artefactos da execução correspondente, dentro do prazo de retenção, ou no backup externo.

Uma cópia com a **mesma geração mais recente** pode repor um asset danificado depois de verificar a sua autenticidade. Se só restarem cópias antigas, não basta apagar o cofre e marcar `initialize_state`: é preciso reconciliar envios posteriores e quotas já gastas antes de retomar. Até isso estar resolvido, mantenha as notificações desativadas. Não existe recuperação automática por apagar o histórico ou ignorar a integridade.

Se o Telegram aceitar uma mensagem mas a ligação cair antes de responder, não é possível provar a entrega. A tentativa fica incerta e não é repetida automaticamente. Esta escolha pode perder uma notificação; evita repetições indefinidas e mantém o Telegram reservado a avaliações elegíveis. Consulte o estado local para uma decisão manual.

## Diagnóstico privado da Birdeye

A resposta oficial de erro pode usar `message` em vez de uma lista `detail`. Em HTTP400/422, a ferramenta guarda a mensagem limitada e redigida na tabela imutável `api_failures` do SQLite. São removidos a chave API, variantes codificadas e URLs; cabeçalhos, pedidos, inputs e restantes campos remotos não são guardados. O diagnóstico integra apenas o cofre cifrado e não entra nos logs de Actions, HTML/CSV/JSON ou Telegram. Não altera nenhuma verificação de elegibilidade.

Este registo permite distinguir um parâmetro recusado, falta de dados ou indisponibilidade efetiva, sem experimentar endpoints ou planos alternativos. Os logs públicos continuam a mostrar apenas método/caminho, estado HTTP e estrutura limitada. Uma sondagem que falha mantém código de saída não zero; não se muda para sucesso apenas para remover o indicador vermelho.

## Orçamento gratuito e dados insuficientes

O cliente limita pedidos, créditos por execução, consumo mensal reservado e tentativas. Birdeye e Helius têm contadores e reservas independentes, conforme `config.toml`. A configuração inicial Birdeye reserva até 800 CU por execução e 28 000 CU por mês; 31 reservas de 800 CU totalizam 24 800 CU. A Helius tem limites próprios inferiores à quota Free. São limites locais conservadores, não créditos adicionais. Cada repetição consome orçamento. A reserva durável antes da rede evita que uma falha do runner faça esquecer o consumo. Reservas conservadoras podem fazer parar a análise antes de esgotar efetivamente a quota da conta.

Os limites das contas Birdeye/Helius e a acessibilidade dos endpoints podem mudar. `probe --provider both` faz uma consulta Birdeye (30 CU) e uma Helius (10 créditos), sem retentativas. Confirma apenas os pedidos efetuados; não certifica automaticamente todos os endpoints e não aprova carteiras. Uma resposta 401, 403, 429, quota insuficiente, paginação interrompida ou resposta incompatível fica registada e bloqueia conclusões dependentes. Só se usam Birdeye Standard e o RPC Helius Free explicitamente autorizado. Não há scraping, endpoints privados, serviços adicionais ou upgrades automáticos.

`config.toml` identifica o modo, a versão e os limites heurísticos: amostra mínima, períodos, concentração, fração fechada, frequência e cadência de notificações. A seleção observada exige as suas oito condições para aparecer como selecionada no relatório; uma pontuação elevada não compensa uma reprovação ou indeterminação. As provas globais ausentes continuam separadas e explicitamente NÃO VERIFICADAS. O envio exige sempre as oito verificações estritas, cujas lacunas continuam a bloquear a aprovação.

## Testes, snapshots e informação futura

Os testes usam fixtures/mocks e não enviam mensagens reais. `demo` identifica os dados como fictícios e nunca pode tornar uma carteira elegível para entrega. As fotografias da seleção são imutáveis e guardam a data, versão dos critérios e dados disponíveis nessa altura. Eventos posteriores não pertencem à seleção original.

O sistema não simula preços de execução do seguidor e não faz copy trading. O comando [followup](validation.md) guarda observações posteriores noutra tabela imutável, sem chamar PnL a dados incompletos. Qualquer avaliação posterior deve usar dados posteriores guardados separadamente; um resultado retroativo não é acompanhamento real.
