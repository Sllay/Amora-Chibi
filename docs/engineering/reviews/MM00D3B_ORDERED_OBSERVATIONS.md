# MM.00D.3B — Ordem temporal e backpressure

Status: plano pré-código; dependência #242 CI/merge. Issue #238.

## B.1: buffer semântico por stream (primeiro recorte)
- Entrada: ObservationContext já emitido/revalidado por autoridade interna; SensoryObservation canônica, não frame/áudio bruto. Sem API externa, provider, DB ou bus. Caller é responsável por checar consentimento antes de cada admissão; revogação deve fechar stream na integração futura.
- Sequência inicial explícita (0 por padrão), epoch de autorização, escopo e correlação imutáveis. Buffer por stream, ordem crescente de sequence, entrega contígua; reordenar somente dentro de janela de distância e espera configuradas. Não inferir ordem global entre relógios distintos. Atraso não altera presente.
- Reenvio igual (exceto received_at) é duplicata; divergente no buffer/cache recente é conflito. Se sequência anterior saiu do cache, rejeitar como late, nunca reentregar. Event store D.1 continua autoridade durável para conflitos/replay após restart.
- Limites: total de streams, pending por stream, bytes globais e tamanho de evento; rejeição de pressão explícita, sem sobrescrever buffer. Cache de fingerprints bounded por stream. Gap por timeout monotônico, saída estruturada com intervalo ausente; não inventar observação nem persistir automaticamente. Reconnect troca epoch ou stream com autorização; epoch antigo rejeitado, pendentes descartados explicitamente no fechamento.
- Sem correlação automática entre call/tela, sem fila durável, sem armazenar amostra bruta. B.2 (adaptação do transporte, raw buffer, métricas e tempo de captura sincronizado) depende de formatos e testes reais. D.3C fará seleção e replay durável; não usar este buffer como garantia de memória.
- Testes: ordem, buracos, timeout, duplicata/conflito, sequência tardia, isolamento/epoch, rejeição de contexto adulterado, limites e descarte. Teste de pressão sintético com milhares de eventos, contagens e bytes bounded. Sem mudança de schema ou runtime.

## Critério de saída
CI exato, nenhum frame bruto, nenhum evento persistido por efeito colateral, limites medidos em teste, checkpoint atualizado. Os valores iniciais são parâmetros de engenharia ajustáveis, não metas de desempenho já medidas em iPhone/Discord.
