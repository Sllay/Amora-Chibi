# MM.00D.3B.2 — Sincronização sensorial

Status: B.2A em implementação; B.1 aprovado #243 (7/7 jobs, HEAD 0f8d3b6c901d695859ca2d45108caf0b7e8fb839, merge 705608575a757e70ed1107bef63715afc2be2de9). Issue #238.

## Checkpoints
- B.2A: contrato interno de estimativa de tempo para observações semânticas de streams autorizados. Usar coordenadas monotônicas locais do receptor, não confiar em relógio de dispositivo ou timestamp de chegada como captura. Calibração explícita via intervalo de referência local que contém o instante remoto; offset intervalar, drift conservador, expiração; nenhuma alegação de sincronização precisa sem fonte calibrada. Aceitar apenas domínio/epoch/stream autorizado; não transportar timestamps brutos em memória. Correlacionar fala/visão somente quando mesma sessão/correlação, audiência comum autorizada, intervalos com incerteza dentro do orçamento, distância garantida dentro de janela. Diferenciar possível de confirmado; sem afirmar relação causal. Testes offsets, drift, stale, atraso de chegada, intervalos, conflito de escopo e limites. Sem provider/DB/rota/runtime.
- B.2B: captura raw real, RTP/RTMP timestamps, clock mapping, jitter e filas por bytes/duração, decodificação e medição de latência. Depende do transporte específico, portanto fica para MM.02–05; não bloquear CH2 imagem pontual por inexistência de vídeo contínuo. Cada adaptador terá gate próprio e não pode declarar sincronização sem calibração real.
- C: seleção e recuperação durável continuam independentes de B.2B; CH2 pontual não necessita clock entre dispositivos.

## Decisão
Não implementar sincronização fictícia por comparação de timestamps UTC fornecidos por clientes. A autoridade de sessão existente não compartilha automaticamente correlação entre DeviceSensoryAuthority e PlatformSensoryAuthority; integração futura deve emitir vínculo explícito com consentimento e audiência intersecada. B.2A só compara contextos já legitimamente correlacionados. Estimativa temporal não concede consentimento. Valores iniciais de tolerância são limites de engenharia, não latência medida. Sem alteração de schema/produção.
