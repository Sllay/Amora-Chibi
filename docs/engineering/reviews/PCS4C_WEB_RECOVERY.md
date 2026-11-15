# PCS.4C — Painel web privado (sem Shell)

A interface é um serviço temporário separado, com a MESMA imagem do Core.
Não é a Web de conversas, não inicia AmoraCore de produção nem Discord, não
adiciona rotas à API de produção. Não há endpoint de download, upload,
execução arbitrária, entrada de URL/token de banco ou reset de banco.
O painel possui uma única execução ordenada por volume: backup, verify,
restore, restart. Estados e arquivos ficam em volume privado persistente.
Não trate este painel como backup automático ou ativação da projeção local.

## Preparação pelo dashboard do Northflank

1. Depois de CI verde e merge, use a imagem Core construída no commit
   integrado. Crie um deployment service temporário com essa mesma imagem,
   uma réplica, porta HTTP 8765, TLS e health check /ready. Não altere o
   comando do Core de produção. Use acesso privado/restrito onde disponível.
   Não crie novos serviços pagos sem conferir o orçamento.
2. Na página CMD override, selecione Custom command e informe
   `python -m uvicorn amora.storage.pcs4_web:create_app --factory --host 0.0.0.0 --port 8765 --workers 1 --no-access-log`.
   Isso é configuração no painel, não um comando no Shell. Use o usuário
   non-root da imagem (UID 10001). Não exponha uma porta de banco.
3. Monte um volume privado realmente persistente em /private, gravável
   pelo UID 10001. Mantenha o volume após apagar o serviço. A configuração
   verifica mountpoint e rejeita tmpfs/overlay, mas não prova a política de
   retenção do host: confirme a retenção e, preferencialmente, uma cópia
   independente protegida fora do serviço. Não use filesystem efêmero.
4. Configure as variáveis apenas no serviço de recuperação:
   - AMORA_PCS4_WEB_ENABLED=1
   - AMORA_PCS4_WEB_DURABLE=1
   - AMORA_PCS4_WEB_VOLUME=/private
   - AMORA_PCS4_WEB_TOKEN: segredo aleatório exclusivo do painel (32 bytes
     ou mais), diferente dos tokens de Core e bancos. Nunca colocar em URL.
   - AMORA_LIBSQL_URL e AMORA_SECRET_LIBSQL_AUTH_TOKEN: configuração já
     autorizada da origem Turso. Não injete chaves de IA, Discord ou admin.
   - AMORA_PCS4C_DESTINATION_URL e AMORA_PCS4C_DESTINATION_TOKEN: somente
     o banco independente novo, vazio, token específico. Pode configurar
     esses dois depois do verify; o progresso persiste no volume.
   Não cole valores privados em GitHub/chat. Use o painel de secrets.
5. Abra a URL HTTPS do serviço. Digite apenas o token exclusivo do painel
   na tela de acesso; fica em memória da página, não em cookies, URL ou
   localStorage. A página e o health check não expõem dados; endpoints de
   operações exigem Bearer. Use uma conexão de confiança e restrição de
   acesso do host; não habilite HTTP público sem TLS.

## Quatro botões

- **Criar backup:** pause Core, Discord e outros escritores no dashboard,
  confirme a pausa na interface. A exportação existente do driver mantém
  transação consistente, sem bootstrap do Core na origem. O ZIP dos assets
  usa a mesma cópia SQLite. O backup fica privado, nunca no browser.
- **Verificar backup:** verifica a cópia no volume e compara os hashes
  privados registrados na criação. Após confirmação do volume/retenção e
  verify bem-sucedido, retome a produção. Não é preciso pausar para os passos
  seguintes.
- **Restaurar banco de teste:** confirme pelo dashboard do Turso que o
  destino é outro banco, vazio e independente. Hostnames diferentes não
  comprovam isolamento físico. A ferramenta rejeita mesma origem/token e
  destino não vazio; reutiliza certify_restore, sem sobrescrever banco.
- **Certificar reinicialização:** reutiliza certify_restart no destino
  verificado. A execução abre três processos isolados (referência local,
  dois boots remotos) sem providers/Discord e compara ranking e memória.
  Bootstrap pode alterar metadados no banco de teste. Nunca executar na origem.

O painel atualiza o estado a cada dois segundos e pode continuar a operação
quando o navegador fecha. Não desligue o serviço enquanto uma etapa roda.
Um lock de filesystem impede operações simultâneas; se o processo morrer
durante uma operação, o estado fica interrompido e nenhuma etapa é repetida
automaticamente. Não apague state.json, não sobrescreva backup ou banco.
Em caso de falha preserve o volume/destino e diagnostique antes de iniciar
nova execução com volume/destino novos. O estado é de uma certificação,
não um gerenciador de backups ou botão de recuperação em produção.

Copie apenas os relatórios sanitizados da tela (status, contagens e
equivalência). Não há retorno de hashes, paths, conteúdo ou arquivos.
Zero registros de uma classe de privacidade não significa cobertura positiva
dessa classe. A issue #221 continua aberta até retenção privada e testes
reais serem comprovados; projeção local permanece desativada.

Referência Northflank (CMD override):
https://northflank.com/docs/v1/application/run/override-command-entrypoint
