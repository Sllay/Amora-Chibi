# Perfil social V3 — staging e rollback

1. Usar a imagem/commit da Fase2 em instância isolada com SQLite descartável,
   variáveis/SecretStore do operador e nenhuma conta produtiva.
2. Definir `AMORA_SOCIAL_EXPRESSION_PROFILE=amora-v3` explicitamente. O arquivo
   original deve corresponder ao SHA em expressive_profile.py. Não editar V2/V3.
3. Conferir readiness e `social_expression_profile` (status configured). Isso
   indica configuração; para comprovar uso, verificar turno validado e
   last_realization.expression_profile=amora-v3. Testes fakeHTTP registram bytes
   no request; nenhum endpoint de status comprova qualidade de uma inferência.
4. Executar as suites de staging. Só depois e com autorização/credenciais
   operacionais, executar piloto real controlado sem modelos pagos/fallback
   oculto. Não copiar credenciais para Codex, relatórios ou prompts.
5. Rever privacidade/autoridade/fatos e respostas humanas separadamente de JSON,
   latência/idle/timeout/quota. Fase3 será avaliação comportamental distinta.
6. Rollback: `AMORA_SOCIAL_EXPRESSION_PROFILE=core-v1` + reinício da instância.
   Nenhuma migration nova; não apagar banco, memórias ou relações para reverter.

A produção não foi alterada por esta entrega. Staging remoto e piloto live
não foram executados; não há domínio/deploy remoto certificado nesta fase.
