# Estabilização operacional — quatro RUNs

Base: main e11011e (PR #388, CI 13/13). Northflank: inicialização observada; SHA/worker e recursos não confirmados. Fila durável off por padrão; não ativar sem backup/restore, schema e autorização. Esta fase não substitui os gates da evolução cognitiva V2.

## Evidência de campo (25/11/2026, UTC-3)
- Memória imediata e correção Vexora: PASS contextual, não prova memória consolidada após restart. Nome, 4 patas, caverna gelada, dourado -> vermelho. Sugestões de comportamento não são fatos.
- Mensagens PC -> Android -> offline/NPC: respostas 2,3,1 (fora de ordem); dispatcher durável ainda off.
- Economia três aldeias: parcial; respeita troca/transporte, mas sugere plantar apesar da exclusividade.
- Imagem única do avatar descrita como quatro painéis; segunda foto indisponível na primeira tentativa, depois leitura parcial, olhos abertos vs semicerrados não detectados. Recuperou descrição errada; afirmou olhar novamente sem prova de reinspeção.
- Pesquisa profunda sem links, continuação WEB_SEARCH_CONTEXT_MISSING / WEB_RETRIEVAL_UNAVAILABLE. URL direta WEB_SOURCE_RETRIEVED. Pedido "envie um print real dela": web_capture_request kind=open requested=False authorized=False. "Mim envie o print real" não acionou captura. Nenhuma prova de falha do Chromium nesse turno.
- Documentação extensa: só introdução; Numes cancelado 87s, 1340 chars parciais; Groq 413 (45659 bytes, 11307 tokens estimados, max_output_tokens=600); Gemini entregue. REALIZATION_SCHEMA_VALID não prova completude; RESPONSE_SEMANTIC_FILTER_DISABLED.
- Cinco turnos 05:13–05:21 UTC: total 99.476/43.266/59.173/56.324/98.503s, provider 89.039/33.638/43.412/49.199/88.477s. Numes first_content 13.657/25.867/33.691/59.266s nas amostras disponíveis. Contexto 1.93–2.61s, event loop pico 3.47–5.27s, claim 195–206ms, ACK 117–118ms. Não confundir métricas sintéticas com produção; sem logs visuais/Discord completos ou CPU/RSS.

## RUN01 — performance cognitiva
Objetivo: menor latência sem amputar memória/identidade ou alterar personalidade.
1. Orçamento real por tentativa e reserva de failover, progresso Numes limitado ao saldo, sem janela extra invisível; distinguir tempo de rede, espera de quota e CPU/SQL. Roteamento adaptativo baseado em resultados reais, não supor quota ou velocidade; preservar escolha explícita de provedor e backoff.
2. Medir bytes/tokens estimados antes/depois; deduplicar projeções de protocolo/contexto, preservar texto literal, restrições, memória relevante e privacidade. 413/context_length_exceeded: reduzir só projeção opcional, uma retry autorizada, nunca repetir envelope idêntico; se insuficiente, failover ou explicitar limite. Não tornar 413 um cooldown geral.
3. Solicitação extensa: distinguir saída detalhada de conversa, orçamento de saída adequado, planejar seções, produzir e validar cobertura sem afirmar conclusão falsa; persistir progresso por seção e recuperar sem duplicar entregas quando protocolo estiver disponível. Não expor JSON parcial nem raciocínio interno. Diferenciar falha de transporte vs geração.
4. Aceite: testes unitários e integração no caminho ativo off e candidato accept; 413, 429, 503, progress timeout, fallback, cancelamento, revogação, texto longo/negações, sem regressão de memória. Medir payload e p50/p95 em carga comparável; alvo inicial 0,2 vCPU/512MB, sem prometer redução antes de medir. Live separado.

## RUN02 — inteligência visual
Corrigir aquisição e diagnóstico por estágio (MIME, download, conversão, sensor, timeout, validação, projeção), preservar texto+anexos; não culpar visão sem evidência. Análise espacial (componentes, posição, tamanho, cor, expressão/estado), comparação direta quando pixels autorizados disponíveis, revisão direcionada e incerteza; histórico textual não é nova visão. Corrigir observação sem consolidar erro como fato; escopo owner DM/consentimento/revisão/TTL, sem URL assinada/bytes em memória canônica; retenção e revogação explícitas. Aceite: avatar olhos abertos vs semicerrados, inclinação, fundo, contagem de painéis, lote parcial e reenvio; testes semânticos reais e regressão de privacidade. Logs 04:57–05:06 UTC pendentes.

## RUN03 — pesquisa e navegação
Persistir assunto/progresso mesmo sem fontes, retomar com refinamento e TTL, separar encontrado/lido/falhou/cache, comparar fontes independentes e entregar links verificáveis; não confundir conhecimento com pesquisa. Corrigir intenção natural de print/retry, manter autorização de origem e SSRF/DNS/redirect, gerar e enviar anexo com confirmação física e estado real, sem prometer entrega futura. Aceite: pesquisa Godot, continuidade após falha, URL direta, screenshot real no Discord, referências corretas e segurança. Sem depender apenas de regex específica dos exemplos.

## RUN04 — comportamento cognitivo
Ordenação causal, agrupamento de complementos/correções sem cancelar assunto independente, replanejamento pré-commit e fence transacional, memória versionada (fato do usuário vs inferência/sugestão), restrições negativas, perguntas pertinentes, evitar repetição e excesso de persona em tarefa técnica sem eliminar personalidade. Certificar schema/backup/restore/drain e ativação da fila somente após autorização separada; voz/avatar e recuperação de recibo externo incerto são pendências explícitas. Aceite: três mensagens rápidas, Vexora após restart, economia sem produção alternativa, documentação completa; zero perdas/duplicatas nos cenários definidos.

## Regras obrigatórias
Branch/PR cumulativos por etapa, CI integral verde antes de avançar; preservar Avatar e alterações concorrentes, main apenas autorização explícita. Não relaxar benchmark/testes para passar. Não criar monitoramento. Não modificar secrets, banco efetivo ou deploy sem autorização. Registrar SHA, mudanças, falhas, correções, limites e evidências no RUN_LOG. Se apenas parcial, manter IN_PROGRESS; não declarar LIVE_CERTIFIED sem evidência física, recursos e rollback. Não repetir toda bateria manual entre incrementos; certificação cruzada após RUN04. 
