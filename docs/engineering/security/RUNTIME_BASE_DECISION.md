# Base de runtime do Amora Core

Análise: 2026-10-24 UTC. Origem: `feat/security-predeploy`, `eb03eb8`.
Escopo: imagem Core/Web, empacotamento e gates locais/CI. Nenhum deploy.

## Decisão e limite da aprovação

**A — PASS para o gate de runtime local:** selecionar a imagem oficial
`python:3.12.15-alpine3.24`, índice OCI
`sha256:b658b05ce9c4eb97ec6fc19424818a1f31993659c2b505a532f470e83807815d`.
A variante foi construída e validada separadamente antes de promover o Dockerfile.
O mesmo Dockerfile conserva `PYTHON_BASE` para comparar/substituir a distribuição;
não incorpora dependência de Koyeb, Turso ou Modal.

Alpine passou regressões com musl, sem compilação nativa de dependências, com
runtime aproximadamente metade do tamanho. Trivy 0.76.0 não reportou CVEs na
imagem selecionada. O scanner e seu digest permanecem os mesmos do gate anterior;
HIGH/CRITICAL continuam bloqueantes, inclusive sem correção. Não há VEX,
`.trivyignore`, redução de severidade ou exclusão de pacote. A imagem Debian
original continua falhando no mesmo gate, como controle negativo.

Isso não aprova staging/produção. Ingress, DNS, egress, persistência remota,
capabilities reais do host, limites de recursos, Safari físico e voz nativa real
continuam gates separados. Não foram acessadas contas dos três serviços.

## Evidências e método

Resultados e versões estão em [runtime-evidence](runtime-evidence/): inventários
das duas imagens, snapshot das oito CVEs, logs das suítes, disponibilidade de
wheels e `comparison.json` com medições, hashes e códigos de saída. O relatório
[image-audit.json](image-audit.json) anterior preserva os findings Debian por
pacote. Os resultados são do ambiente Linux amd64, kernel 6.18.55, não de Koyeb.

Fontes: registros CNA em `CVEProject/cvelistV5`, advisories/patches upstream
referenciados abaixo e dados do Debian Security Tracker. O domínio do Tracker
retornou **403 do proxy**; não foi contornado. A posição Debian foi consultada no
espelho `aquasecurity/vuln-list-debian`, revisão
`6c329505afdcca15201771b56096db4ad66f1db3`, de 2026-10-23T18:13:16Z.
O snapshot versionado distingue explicitamente essa posição de uma consulta
direta atual ao domínio Debian. NVD/CVSS não foram usados como prova de alcance.

Inspeção dentro das imagens: banco dpkg/apk, presença de executáveis/módulos,
`ldd` de extensões Python, bits setuid/setgid, file capabilities, namespaces,
UID/GID, `CapEff/CapPrm/CapBnd`, seccomp, NoNewPrivs, fstab e código do Core.
Busca por subprocessos, shell, ctypes, mount, nsenter, infocmp e ACL foi revista
manualmente: mounts ASGI de arquivos estáticos não são chamadas de mount(2).
O carregamento ctypes do DSP vocal é opcional e seu bundle não integra esta
imagem. Requests, uploads, texto, URLs e bancos não são convertidos em comandos
de shell pelo Core. Isso não prova segurança de plugins futuros arbitrários.
Inventário confirma imports reais das extensões. O ldd musl foi executado com
libpython preloaded, reproduzindo a resolução de símbolos Python do interpreter;
ldd isolado sobre uma extensão reporta símbolos Py* ausentes, mesmo quando o
import funciona. Não se confundiu esse diagnóstico com incompatibilidade ABI.

O perfil ensaiado usa rede Docker isolada, namespaces privados padrão, filesystem
read-only, tmpfs limitado, volume exclusivo de estado, UID/GID 10001,
`--cap-drop ALL` e `--security-opt no-new-privileges`. Foram observados
CapEff/CapPrm/CapBnd zerados, NoNewPrivs=1 e seccomp ativo. Não há file capabilities
nos diretórios de executáveis auditados. **USER no Dockerfile não impõe esses
controles de execução ao host.** Não presumir que Koyeb os reproduza sem verificar.
Também não usar non-root, sozinho, como justificativa de não afetado.

## Por que oito CVEs viram 44 findings

Trivy associa avisos ao pacote-fonte Debian e os replica nos binários derivados
instalados. A presença do pacote derivado não prova presença da função afetada.
Não são 44 falhas independentes e nenhum finding foi descartado da contagem.

| Fonte | Binários instalados reportados | CVEs | Findings HIGH |
|---|---|---|---:|
| util-linux | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | 76642, 78408, 78409, 78410 (2026) | 9 × 4 = 36 |
| acl | libacl1 | CVE-2026-54369 | 1 |
| systemd | libsystemd0, libudev1 | CVE-2026-16742 | 2 |
| perl | perl-base | CVE-2026-9538 | 1 |
| ncurses | libncursesw6, libtinfo6, ncurses-base, ncurses-bin | CVE-2025-69720 | 4 |
| **Total** | | **8 distintas** | **44** |

Versões: util-linux 2.41.5-0+deb13u1 (bsdutils tem epoch 1; login usa versão
transicional 1:4.16.0-2+really2.41.5-0+deb13u1); acl 2.3.2-2+b1;
systemd 257.13-1~deb13u1; perl 5.40.1-6+deb13u1;
ncurses 6.5+20250216-2. `dpkg-query` confirma os pacotes-fonte, inclusive login.

## Análise individual da imagem Debian original

Classificações abaixo descrevem o Core e o perfil ensaiado, não uma exceção
formal de scanner nem segurança universal da distribuição. Bibliotecas
vulneráveis presentes continuam identificadas. A imagem Debian não foi aprovada
por essas classificações; não se criou VEX para tornar seu gate verde.

### CVE-2026-76642 — util-linux / libmount

- **Presença:** os nove binários derivados acima estão instalados; `mount` existe
  com modo 4755 e `libmount.so.1` está presente. A falha reside na execução de
  hooks privilegiados após falha do mount helper, não em libuuid/libblkid por
  compartilharem fonte.
- **Exploração:** mount/helper privilegiado, operação autorizada/selecionada de
  filesystem e hooks `X-mount.idmap`/`X-mount.owner` sobre filesystem preexistente.
  Pode herdar suid ou alterar permissões apesar da falha do helper.
- **Alcance:** Core não invoca mount/helpers nem libmount. fstab contém apenas
  comentário; não há entrada user-mount/hook. Input HTTP não define fstab,
  mountpoint ou opções. Estado gravável não vira script/helper executado.
- **Privilégios:** suid está realmente presente no Debian; UID 10001 sozinho
  não bastaria. No perfil ensaiado, no-new-privileges impede ganho por suid,
  CAP_SYS_ADMIN ausente e não há namespaces/mounts do host compartilhados.
- **Debian:** trixie `no-dsa`, “Minor issue”; corrigida em 2.42.3-1 na linha
  principal, sem FixedVersion para a versão trixie no scan.
- **Classificação: MITIGATED_BUT_PRESENT.** Reavaliar se houver mount/helper,
  fstab autorizado, CAP_SYS_ADMIN, suid efetivo ou mudança dos namespaces.
- Fonte: [advisory upstream](https://github.com/util-linux/util-linux/security/advisories/GHSA-m25x-3hj9-m26f).

### CVE-2026-78408 — util-linux / nsenter

- **Presença:** mesmos nove findings derivados; `nsenter` e sua opção
  `--join-cgroup` existem. O código mantém aberto um FD de `cgroup.procs` após
  mudança de credenciais/exec, preservando a autoridade do opener privilegiado.
- **Exploração:** um operador privilegiado precisa executar nsenter com
  `--join-cgroup` contra alvo controlado pelo atacante. O atacante herda o FD
  aberto com credenciais de root e pode migrar processos de outros cgroups.
  Não é necessário que o programa final conserve capabilities: o FD já as delegou.
- **Alcance:** Core não executa nsenter, não entra em namespaces e não recebe
  esse FD por sua inicialização normal. HTTP/WebSocket não oferecem esse comando.
  Não há PID/cgroup do host exposto no perfil, nem cgroup de host gravável.
- **Privilégios:** nsenter não é setuid no inventário. O problema começa na
  abertura privilegiada do FD; cap-drop sozinho não revoga um FD já herdado.
  Proibir inicialização por um nsenter vulnerável também é condição operacional.
- **Debian:** trixie `no-dsa`, “Minor issue”; corrigida em 2.42.4-1 na linha principal.
- **Classificação: NOT_REACHABLE.** Reavaliar com wrapper nsenter, FD herdado,
  acesso privilegiado a cgroups, host PID ou console que execute comandos do host.
- Fonte: [patch upstream](https://github.com/util-linux/util-linux/commit/286dd3ff41526b582ef48830de239dffbaa61f90).

### CVE-2026-78409 — util-linux / X-mount.subdir

- **Presença:** mesmos nove findings; mount/libmount vulneráveis presentes.
- **Exploração:** caminho detached-tree em Linux >=6.15, fstab autorizando
  `X-mount.subdir` para usuário local e symlink intermediário controlável.
  `AT_SYMLINK_NOFOLLOW` não contém a resolução dentro da árvore montada.
  **O kernel ensaiado é 6.18.55: a pré-condição de versão existe.**
- **Alcance:** nenhuma entrada fstab autorizada, nenhuma execução mount pelo
  Core nem transformação de input remoto em opções de mount. Uploads não são
  instalados como filesystems/terminfo/fstab.
- **Privilégios:** mount 4755 presente, mas sem privilégio adquirido via suid no
  perfil NNP; CAP_SYS_ADMIN ausente, raiz read-only, namespaces privados.
- **Debian:** trixie `no-dsa`, “Minor issue”; corrigida em 2.42.3-1 na linha principal.
- **Classificação: MITIGATED_BUT_PRESENT.** Reavaliar ao autorizar subdir mounts,
  executar helpers privilegiados ou alterar capabilities/fstab/namespaces.
- Fonte: [advisory upstream](https://github.com/util-linux/util-linux/security/advisories/GHSA-8f2p-47x3-43mv).

### CVE-2026-78410 — util-linux / restricted bind mounts

- **Presença:** mesmos nove findings; mount/libmount presentes, mount 4755.
- **Exploração:** fstab autoriza bind mount restrito e atacante substitui fonte
  ou ancestral gravável antes do mount privilegiado. Com `X-mount.owner/group/mode`,
  root modifica o inode redirecionado. Fonte não é fixada antes do uso.
- **Alcance:** Core não faz bind mounts nem escreve fstab. Volume de dados e
  input HTTP não participam de uma operação mount autorizada executada pelo Core.
- **Privilégios:** ausência de entrada autorizada e de caller privilegiado,
  NNP/cap-drop e raiz read-only quebram o caminho ensaiado; non-root isolado não.
- **Debian:** trixie `no-dsa`, “Minor issue”; corrigida em 2.42.3-1 na linha principal.
- **Classificação: MITIGATED_BUT_PRESENT.** Reavaliar com bind mounts dinâmicos,
  X-mount owner/group/mode, helpers privilegiados ou nova entrada fstab.
- Fonte: [advisory upstream](https://github.com/util-linux/util-linux/security/advisories/GHSA-rh77-686x-2f2m).

### CVE-2026-54369 — acl / libacl1

- **Presença:** `libacl.so.1` realmente instalado; ferramentas getfacl/setfacl
  ausentes. Código afetado: `acl_get_file`, `acl_set_file`, `acl_extended_file`,
  `acl_delete_def_file` com pathname e resolução de symlinks.
- **Exploração:** caller privilegiado usa essas APIs em caminho cujo componente
  atacante pode substituir; acesso ACL é redirecionado para inode arbitrário.
  Não exige CAP_SYS_ADMIN especificamente; autoridade do caller sobre ACLs basta.
- **Alcance:** Core/Python não chamam essas funções, não lançam ferramentas ACL
  e não carregam libacl por suas extensões auditadas. Copiar xattrs via APIs do
  Python não equivale a invocar libacl pathname APIs. Input/upload gravável sozinho
  não cria o caller privilegiado. Não alegamos que toda ferramenta Debian seja segura.
- **Privilégios:** UID sem autoridade sobre outros owners, NNP e raiz read-only
  limitam consequências, mas a conclusão depende também da ausência de caller.
- **Debian:** trixie `no-dsa`: correção primeiro em unstable, depois point release,
  sem backport individual; versão principal corrigida 2.4.0-1. ABI nova `acl_*_at`
  exige cuidado: não copiar biblioteca mais nova de outra release cegamente.
- **Classificação: NOT_REACHABLE.** Reavaliar com ferramentas ACL, cópia/restore
  privilegiado de arquivos externos ou plugin nativo que invoque libacl.
- Fonte: descrição CNA e notas/patches do Tracker no snapshot versionado.

### CVE-2026-16742 — systemd / libsystemd0 e libudev1

- **Presença:** duas bibliotecas presentes, mas **systemd-homed não está na
  imagem**, nem como executável encontrado sob /usr, nem pacote instalado.
  A falha não reside nas APIs de libsystemd/libudev usadas por outras ferramentas.
- **Exploração:** serviço homed com usuário local já logado e gerenciado por
  homed; authenticate aceita home-record sem verificar assinatura e permite
  acrescentar grupos. Exige serviço privilegiado e esse modelo de usuário.
- **Alcance:** PID 1 é Amora/Uvicorn, não systemd. Não existe caminho HTTP até
  homed, home-record assinado, D-Bus homed ou gerenciador homed de usuários.
- **Privilégios:** a ausência do componente precede análise de UID/capabilities.
  Compartilhar D-Bus/serviço homed do host mudaria essa conclusão mesmo non-root.
- **Debian:** trixie `no-dsa`, “Minor issue”; corrigida em 261.2-1 na linha principal;
  Tracker também referencia backports upstream 258.10.
- **Classificação: NOT_AFFECTED (componente vulnerável ausente).** Reavaliar se
  instalar systemd-homed, compartilhar D-Bus/homes do host ou mudar init.
- Fonte: [patch upstream](https://github.com/systemd/systemd/commit/d392d17143423e7af0cdb5bd0f64b43da8952662).

### CVE-2026-9538 — perl / perl-base / Archive::Tar

- **Presença:** interpretador Perl instalado, mas **Archive::Tar ausente**.
  Busca de `Tar.pm` sob /usr vazia e `perl -MArchive::Tar -e 1` falha com
  “Can't locate Archive/Tar.pm”. perl-base não é a distribuição completa.
- **Exploração:** Archive::Tar <3.10 lê TAR externo cujo campo size declara
  payload gigantesco, causando alocação/memory exhaustion. Não exige root,
  setuid, namespaces ou capabilities. Non-root não impediria esse DoS.
- **Alcance:** Core não invoca Perl/TAR Perl. Python/SQLite/ZIP do Avatar não
  usam Archive::Tar; input HTTP não encontra o módulo ausente.
- **Debian:** trixie `postponed`, “Minor issue; wait for regressions upstream
  sorted out”; scanner `fix_deferred`; corrigida na linha principal perl 5.42.3-1,
  Archive::Tar 3.10 upstream.
- **Classificação: NOT_AFFECTED (módulo vulnerável ausente).** Reavaliar ao
  instalar Perl completo/Archive::Tar ou processar TAR via Perl, inclusive em jobs.
- Fonte: [patch upstream](https://github.com/jib/archive-tar-new/commit/f9af01426038e29d9578825a0cd3626946ab08c7).

### CVE-2025-69720 — ncurses / infocmp

- **Presença:** quatro findings derivados; **infocmp existe realmente**, em
  ncurses-bin. Overflow está em `analyze_string`, `progs/infocmp.c`, não na
  biblioteca curses carregada pelo Python só por compartilhar fonte.
- **Exploração:** executar `infocmp -i` sobre terminfo malicioso com sequência
  CSI longa. Stack overflow não exige root/capabilities. Read-only sozinho não
  impede ler entrada de /tmp; ASLR/stack protector não são prova de não afetado.
- **Alcance:** Core não executa infocmp nem compila terminfo enviado por usuário;
  uploads não entram em base terminfo e HTTP não controla argv/env do processo.
  `_curses` existe e se liga a libncursesw/libtinfo, mas não à função do programa
  infocmp. UUID/curses não devem ser quebrados removendo bibliotecas por impulso.
- **Privilégios:** binário não é suid; exploração seria no contexto do processo
  que o executar. A fronteira é ausência dessa execução sobre input externo.
- **Debian:** trixie `no-dsa`, “Minor issue”; corrigida em 6.6+20251231-1 na linha
  principal; registro ainda contém TODO de conferência upstream, preservado no
  snapshot. CNA/reprodutor referenciam patch upstream 20251213.
- **Classificação: NOT_REACHABLE.** Reavaliar ao executar infocmp/tic ou aceitar
  bases terminfo externas, inclusive por console/plugin.
- Fonte: [reprodutor do pesquisador](https://github.com/Cao-Wuhui/CVE-2025-69720).

## Minimização e diferenças da Alpine

| Componente | Debian final original | Necessidade do Core / escolha |
|---|---|---|
| util-linux | ferramentas e bibliotecas herdadas | Não usa ferramentas. Alpine só conserva libuuid da fonte util-linux (2.42.3-r1); mount/nsenter são applets BusyBox, não o código util-linux dessas CVEs. |
| mount/login | executáveis Debian, mount suid | Sem uso pelo Core. Alpine herda applets BusyBox, sem bits setuid/setgid no inventário; não se afirma ausência desses nomes. |
| Perl | perl-base, sem Archive::Tar | Não necessário; ausente em Alpine. Não foi instalado para criar usuário/build. |
| ncurses utilities | infocmp/tic presentes | Ausentes em Alpine. Manter libncursesw/libpanelw/terminfo-base exigidos pelo Python; versão 6.6_p20260516-r0. |
| systemd libraries | libsystemd0/libudev1, sem homed | Ausentes em Alpine; não são requisito do Core. |
| ACL tools/libs | libacl1 presente; tools ausentes | Tools e libacl ausentes em Alpine. |
| compiladores/build tools | ausentes no runtime Debian | Também ausentes no runtime Alpine. Build Amora não instalou gcc, make, Rust ou headers nem no builder. |
| pip/setuptools/wheel | removidos do runtime | Continuam fora do ambiente runtime instalado; pip só existe na camada descartável de validação. |

Nenhum `apt purge --force`, deleção de banco dpkg ou cópia avulsa de glibc foi
usada para esconder findings. A redução vem de selecionar uma base oficial que
não instala os componentes desnecessários. Bibliotecas Python foram verificadas
com ldd, não removidas por associação ao nome da CVE.

musl 1.2.6-r3 substitui glibc 2.41; DNS/NSS, locales, stack/thread e ABI diferem.
SQLite passa de 3.46.1 para 3.53.4; OpenSSL de 3.5.7 para 3.5.9; tzdata também
difere. A suíte exercita FTS/migrations/transações/backup e adapters mockados,
não apenas imports. Não instalar camada de compatibilidade glibc ou reutilizar
venv/ELF Debian em Alpine. Builder e runtime usam o mesmo digest.

`pydantic_core==2.49.0` disponibiliza wheels CPython 3.12 musllinux para x86_64,
aarch64 e armv7l. O wheel x86_64 foi instalado e seu ELF musl carregado. Todas as
dependências de runtime e testes foram instaladas com `--only-binary=:all:`;
apenas o pacote Python puro da Amora foi empacotado localmente. Falta de wheel
passará a falhar o build, em vez de adicionar compilador silenciosamente.
Native TTS/Sherpa/ONNX/WORLD continua no worker separado, sem migração de libc
ou alteração de voz/contrato nesta missão. Plugins nativos futuros exigem nova
validação ABI antes de entrar no Core.

## Comparação objetiva

Contagens de todas as severidades: mesmo Trivy, mesma DB de 2026-10-23T19:15:43Z,
sem `ignore-unfixed`. Tamanho = bytes locais da imagem descomprimida, não download.

| Critério | Debian slim original | Alpine selecionada |
|---|---|---|
| Base / Python | Debian trixie / 3.12.15 | Alpine 3.24.3 / 3.12.15 |
| Tamanho final | 139.163.245 bytes (~132,72 MiB) | 69.694.167 bytes (~66,47 MiB) |
| LOW / MEDIUM / HIGH / CRITICAL | 61 / 58 / 44 / 0; mais 1 UNKNOWN | 0 / 0 / 0 / 0; 0 UNKNOWN |
| CVEs HIGH únicas | 8 | 0 reportadas |
| HIGH alcançável pelo Core no perfil ensaiado | Nenhuma demonstrada; 3 mitigadas presentes, 3 não alcançáveis e 2 componentes ausentes | Nenhuma reportada; manter scans contínuos |
| Python / Avatar | 347 PASS / 5 PASS | 347 PASS / mesmos 5 PASS |
| Runtime catálogo antes desta correção | HTTP 500, arquivo omitido | HTTP 200 após empacotar os mesmos bytes |
| pip check | PASS | PASS, inclusive /opt/venv instalado |
| Cold start mediano, 3 amostras | 2,937 s | 2,621 s |
| RSS idle mediano, 3 amostras | 69.104 KiB | 67.336 KiB |
| Build sem cache de camadas, base já local | 24,93 s | 22,55 s |
| Multiarch oficial | amd64, arm/v5, arm/v7, arm64/v8, 386, ppc64le, riscv64, s390x (manifest versionado) | amd64, arm/v6, arm/v7, arm64/v8, 386, riscv64, s390x (manifest versionado) |
| libc / compatibilidade | glibc; mais wheels manylinux | musl; wheel nativo validado em amd64; arm64 só disponibilidade verificada |
| Manutenção | base oficial; oito avisos trixie sem patch publicado para a release ensaiada | base oficial; exigir wheels musllinux, repinar digest e repetir gate |
| Koyeb | OCI Linux; operação remota ainda não ensaiada | OCI Linux, mesma porta/UID/health/secrets; menor transferência; ingress/egress/DNS reais ainda pendentes |
| Risco operacional | mais ferramentas/suid herdados; VEX teria manutenção por perfil e CVE | DNS/locale/ABI de musl; plugins nativos e outras arquiteturas exigem novo ensaio |

Cold start inclui Docker create/start e polling de readiness a cada 0,5s, com base
local, volume novo e sem provider. Não mede pull, autoscaling ou TLS remoto.
RSS é VmRSS do PID 1 após smoke sem requests ativos, não limite/reserva de memória
nem cgroup com page cache. Os jobs de benchmark foram sequenciais; houve outras
atividades de validação na máquina. Amostras pequenas são descritivas, não promessa
de performance. Digest multiarch não significa testes executados em todas as CPUs.

## Regressões, bug e reprodução

Arquivos de implementação alterados: `Dockerfile`,
`.github/workflows/cloud-contracts.yml`, `tools/cloud/smoke-container.py`;
novos `deploy/containers/Dockerfile.validation`, seu `.dockerignore` específico
e `tools/cloud/runtime-inventory.py`. Documentação: este relatório, evidências,
ADR-0017, índice ADR, PREDEPLOY, CLOUD_OPERATIONS e PROJECT_STATE.
Nenhum arquivo em `core/`, `config/`, `web/`, `avatar/` ou lock de dependências
foi alterado. O conteúdo de voz apenas passa a ser incluído corretamente na imagem.

347 testes por base cobrem SQLite, migrations, restart lógico, memória, FTS,
Event Bus/dedupe, remote storage mockado, voz remota mockada funcionando/falhando,
texto independente da voz, ACK/cancel, auth/pairing/WebSocket, rotas Web, probes,
secrets, configuração ausente, shutdown e operações. Cinco avisos existentes de
Pydantic/Starlette, sem skips. Cinco testes Avatar executados no host com a
distribuição oficial Node 22.23.4; Node não integra a imagem Python.

O primeiro harness não copiava `config/`, causando duas falhas iguais nas duas
bases. A inspeção do pacote instalado confirmou um bug real adicional ao harness:
o Dockerfile anterior também omitia o catálogo e a rota autorizada retornava 500.
O Dockerfile agora copia **o mesmo JSON**, read-only, para o caminho que o pacote
instalado já resolve. Não muda código de voz, defaults, parâmetros ou identidade.
O smoke verifica essa rota no pacote instalado; a suíte testa o código-fonte.

Comandos portáveis, sem proxy específico da sessão:

```sh
docker build -t amora-core:runtime-security .
python3 tools/cloud/smoke-container.py amora-core:runtime-security metrics.json
docker build --build-arg RUNTIME_IMAGE=amora-core:runtime-security \
  -f deploy/containers/Dockerfile.validation -t amora-validation:test .
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,size=256m amora-validation:test
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges amora-validation:test \
  /testenv/bin/python -m pip --python /opt/venv/bin/python check
docker run --rm -i --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges amora-core:runtime-security python - \
  < tools/cloud/runtime-inventory.py > inventory.json
```

Para comparação/reversão da distribuição, sem mudar contratos, usar
`--build-arg PYTHON_BASE=python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704`.
Isso não aprova automaticamente Debian: o gate deve continuar falhando enquanto
houver seus HIGH. A camada de validação não é imagem de produção, não instala
build tools e não é publicada. `cloud-contracts.yml` passa a executá-la na CI.

O comando de scanner permanece no workflow `security-predeploy.yml`:
Trivy pinado, `image --scanners vuln --severity HIGH,CRITICAL --exit-code 1`,
sem exceções. Relatórios brutos são artifacts da CI; hashes/contagens e resultados
locais estão versionados para revisão. Scan é dependente da data: novo HIGH
não avaliado volta a bloquear sem qualquer alteração deste documento.

## Reavaliação e próximo gate

Reavaliar a decisão ao repinar base/dependências, adicionar wheel nativo, mudar
arquitetura CPU, executar plugins/console com shell, permitir mount/nsenter/ACL/
terminfo/TAR Perl, instalar homed, compartilhar namespaces/FDs do host ou conceder
CAP_SYS_ADMIN. Não há decisão pendente de personalidade/produto nesta mudança.

Próximo passo: revisão do PR e CI remoto, depois gate separado de staging que
verifique os controles reais de execução, resolução DNS musl, secrets/egress,
quotas/latência/storage remoto e restart/restore. Manter voz, contratos Turso/Modal,
providers, memória e semântica social intactos. **Nenhum deploy foi realizado.**
