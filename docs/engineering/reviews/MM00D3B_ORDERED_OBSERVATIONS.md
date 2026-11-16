# MM.00D.3B — Ordem temporal e backpressure

Status: B.1 aprovado e integrado (#243); #242 aprovado e integrado. Issue #238.

## B.1: buffer semântico por stream (primeiro recorte)
- Entrada: ObservationContext já emitido/revalidado por autoridade interna; SensoryObservation canônica, não frame/áudio bruto. Sem API externa, provider, DB ou bus. Caller é responsável por checar consentimento antes de cada admissão; revogação deve fechar stream na integração futura.
- Sequência inicial explícita (0 por padrão), epoch de autorização, escopo e correlação imutáveis. Buffer por stream, ordem crescente de sequence, entrega contígua; reordenar somente dentro de janela de distância e espera configuradas. Não inferir ordem global entre relógios distintos. Atraso não altera presente.
- Reenvio igual (exceto received_at) é duplicata; divergente no buffer/cache recente é conflito. Se sequência anterior saiu do cache, rejeitar como late, nunca reentregar. Event store D.1 continua autoridade durável para conflitos/replay após restart.
- Limites: total de streams, pending por stream, bytes globais e tamanho de evento; rejeição de pressão explícita, sem sobrescrever buffer. Cache de fingerprints bounded por stream. Gap por timeout monotônico, saída estruturada com intervalo ausente; não inventar observação nem persistir automaticamente. Reconnect troca epoch ou stream com autorização; epoch antigo rejeitado, pendentes descartados explicitamente no fechamento.
- Sem correlação automática entre call/tela, sem fila durável, sem armazenar amostra bruta. B.2 (adaptação do transporte, raw buffer, métricas e tempo de captura sincronizado) depende de formatos e testes reais. D.3C fará seleção e replay durável; não usar este buffer como garantia de memória.
- Testes: ordem, buracos, timeout, duplicata/conflito, sequência tardia, isolamento/epoch, rejeição de contexto adulterado, limites e descarte. Teste de pressão sintético com milhares de eventos, contagens e bytes bounded. Sem mudança de schema ou runtime.

## Critério de saída
CI exato, nenhum frame bruto, nenhum evento persistido por efeito colateral, limites medidos em teste, checkpoint atualizado. Os valores iniciais são parâmetros de engenharia ajustáveis, não metas de desempenho já medidas em iPhone/Discord.

## Evidência B.1

- #242 HEAD `40e869161d4dc6324292c003151257620a1d4a95`, 3/3 workflows e 7/7 jobs aprovados; merge `296bd61dd0466c531741c4fae23a2ac38aea6d10`.
- `467974fb` / `5b7e6cc8` / `e05daa11`: OrderedObservationBuffer, sequence/epoch, contexto canônico, fingerprint com received_at excluído, gaps monotônicos, pressão explícita, cache e pending bounded, close/reconnect. Sem efeito de persistência ou publicação.
- `ecb1f6ad`: testes de ordem, gaps, conflito, late, bytes globais, capacidade por stream, tamanho, troca de epoch/contexto, descarte e 2.000 eventos sintéticos com histórico limitado a 16. CI aprovado (7/7 jobs) no HEAD `0f8d3b6c901d695859ca2d45108caf0b7e8fb839`; merge `705608575a757e70ed1107bef63715afc2be2de9`.
- Defaults iniciais: 64 streams, 32 pendentes por stream, 2 MiB de payload JSON pendente total, 32 KiB por evento, distância de 128, histórico 128, 1 segundo de espera. O limite de bytes é estimativa do payload serializado, não medição da RAM total do processo; estruturas Python têm overhead. Medir RAM/latência e ajustar no B.2 real.
- Limites: sequência é local por stream; sincronização entre relógios/capturas, raw media, integração de revogação e epoch com autoridade real, fila durável e seleção permanecem B.2/C/D. Não reabrir o mesmo stream/epoch após close sem nova autoridade; buffer não é registro durável nem permite reentregar eventos tardios.
