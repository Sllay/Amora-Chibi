# Altare Docker Apps — bulk encrypted env import from iPhone

NEVER commit an exported .env or JSON to Git, even to a private repo. Use GitHub Actions Secrets.

GitHub repository: Sllay/Amora-Chibi -> Settings -> Secrets and variables -> Actions -> New repository secret.

Save three secrets by pasting their entire values once:
- AMORA_CORE_ENV_BUNDLE : the complete exported Core .env or JSON
- AMORA_DISCORD_ENV_BUNDLE : the complete exported Discord .env or JSON
- ALTARE_TOKEN_CONFIG : an Altare Team API token scoped only to changing variables for Core-Amora and Discord-Amora

Run Actions -> Altare - import encrypted env bundle to stopped Docker App, from the main branch, signed in as Sllay.
Select core or discord and provide the ACTUAL HTTPS Core URL (e.g. https://core-amora.go.altare.global if that is its assigned name).
First select mode=check. This validates the bundle and only prints the number of variables or missing variable NAMES.
Stop the target Docker App before any mode=apply. Choose mode=apply and type APPLY_CORE_STOPPED for Core, or APPLY_DISCORD_STOPPED for Discord.
Discord-Amora must remain STOPPED until the Northflank Discord Gateway has been stopped and the single-gateway cutover explicitly authorized. Applying vars to a running app can restart it.

Security: the workflow runs only on main, only for Sllay; never send secrets as workflow inputs, issues, or comments. It does not start apps.
Core gets new AMORA_PUBLIC_ORIGINS, AMORA_MODE=cloud, AMORA_STORAGE_BACKEND=postgres, AMORA_ASSET_STORAGE=database, and AMORA_POSTGRES_CA_FILE=/var/lib/amora/secrets/aiven-ca.pem.
Discord gets the actual AMORA_CORE_URL and only allowlisted worker variables. Northflank-only tokens and paths are removed automatically.
The workflow DOES NOT create the Aiven certificate or approve a production DB migration. The verified CA PEM file must still be uploaded to /var/lib/amora/secrets/aiven-ca.pem and be readable by UID 10001.
The PG18 data, restore, endpoint pins, authorization and HTTPS /ready must be independently checked before enabling Core. Never activate two Discord Gateways.

Altare Docker Apps documentation only confirms altr docker env set for an EXISTING Docker App, not an --env-file option. The workflow therefore sets validated keys individually.
An interrupted import may leave a partial set; rerun while the app is stopped. The importer does not print secret values or the CLI output.
Limits: Altare <= 200 env vars and <= 32KB per app; GitHub Actions secrets <= 48KB each. No support for multiline secret values; use mounted secret files instead.

Docs: https://docs.altare.gg/apps/docker
Docs: https://docs.altare.gg/cli/reference
Docs: https://docs.github.com/en/actions/reference/security/secrets

## Importacao direta dos arquivos do celular (Android/Termux)

Para quem ja tem dois arquivos privados no armazenamento do celular, nao e necessario
transformar esses arquivos em segredos do GitHub. O importador de arquivos locais
faz a separacao e valida as regras de seguranca existentes.

Arquivos padrao (depois de `termux-setup-storage`):

- `~/storage/downloads/core/.env`
- `~/storage/downloads/discord/.env`

O programa tambem tenta `/storage/emulated/0/Download/{core,discord}/.env`.
**Nao copie nenhum arquivo .env para dentro do Git. Nao publique em issues,
Actions/artifacts ou chats.** Depois da importacao, remova os arquivos da pasta
publica Downloads e mantenha somente um backup privado protegido.

No Termux, na raiz do checkout atualizado do repositorio:

```bash
termux-setup-storage
python -m tools.ops.altare_phone_env_import
```

O comando padrao e **offline**, somente valida os dois arquivos, compara
`AMORA_MESSAGING_SERVICE_TOKEN` entre eles, verifica os campos obrigatorios,
remove chaves que nao pertencem ao destino e imprime APENAS contagens e codigos.
Pode-se usar `--core-file`, `--discord-file` e
`--core-origin https://core-amora.go.altare.global` com caminhos personalizados.

Para importar, voce precisa do **altr CLI oficial funcionando em ambiente
Linux suportado** e de um token Altare valido no ambiente local com
`servers:view` e `servers:settings`. A CLI oficial possui distribuicao
Linux ARM64, mas nao ha garantia de compatibilidade direta com Android/Termux;
se nao executar no Android, use uma distribuicao Linux privada via proot ou
outro terminal privado suportado. Verifique `altr --version`.
Nao coloque o token na linha de comando ou no historico:

```bash
read -rs -p 'Altare token: ' ALTARE_TOKEN_CONFIG; echo
export ALTARE_TOKEN_CONFIG
```

**Pare ambos os aplicativos no Altare e confira isso no painel antes da
importacao; especialmente Discord-Amora nunca deve ter outro Gateway ativo
junto com o Discord do Northflank.** Entao:

```bash
python -m tools.ops.altare_phone_env_import --mode apply --confirm BOTH_APPS_STOPPED
unset ALTARE_TOKEN_CONFIG
```

O importador NAO instala CLI, NAO inicia os aplicativos nem faz deploy
do codigo; envia cada variavel validada por
`altr docker env set <app> KEY=value`, o comando que a documentacao
oficial oferece para Docker Apps existentes. Cada aplicacao de variavel
pode reiniciar um app em execucao: por isso deve estar PARADO. Um erro no
meio pode deixar um conjunto parcial; execute novamente enquanto parado.

O importador assume o Core em `https://core-amora.go.altare.global`
(sobrescreva `--core-origin` se o painel mostrar outro endereco).
Ele fixa `AMORA_POSTGRES_CA_FILE` em
`/var/lib/amora/secrets/aiven-ca.pem`: **o certificado de CA real ainda
precisa ser instalado no volume persistente**. A importacao nao cria esse
arquivo e nao comprova que o Core esta pronto para iniciar.

Este procedimento exige que os arquivos estejam acessiveis ao terminal
executando o script. O ChatGPT e o GitHub Actions nao conseguem ler
automaticamente a pasta Downloads privada do seu celular.
