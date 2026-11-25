# Diário de estabilização

2026-11-25 — RUN00: plano baseado nos 21 prints e logs fornecidos pelo proprietário; baseline e11011e. RUN01 IN_PROGRESS. Nenhuma mudança de produção.


## RUN01 A — orçamento, envelope e saída técnica

- 09104d7: reserva de failover no fluxo platform_messaging (inclusive fila off), min(30s, 35% saldo, 12s por candidato posterior elegível conhecido), sem reordenar preferências. 90s/3 alternativas => primeira janela 60s; cada tentativa recalcula. Tempo real de tentativa integral debitado, graça Numes limitada à janela atribuída; removida extensão durável não debitada no runtime (helper histórico mantido para testes). Novos campos numéricos de diagnóstico. Não garante latência de um provedor nem muda timeout HTTP global.
- Retry Groq 413/context_length_exceeded: estende mecanismo existente à DM privada owner textual, ContextVar vinculado a turno e lease do transporte, verificação live de identidade, consentimento, invalidação e renovação; uma projeção estritamente menor, nenhuma retry sem guard, nenhuma retry de visão/trechos web lidos. Mantém rota durável existente; não reduz memória canônica. Se irredutível, failover. Não confundir 413 com quota.
- Numes e Gemini usam referência ACIR idêntica no wire, sem remover fato/estado. Pedido explícito de documentação/explicação extensa privado aumenta limite de saída de Groq/Gemini para 2200, Numes 2600; sem mudança da conversa normal, schema mantém cinco moves x 1200 caracteres. Instrução de cobertura e de declarar insuficiência, não certificação semântica. Testes de limites e guard 413/400 em contextos válidos/revogados/ausentes/outro turno; testes de grace e orçamento.
- Corrigido escopo de import de InboundGenerationLost antes de tratamento e limitado detector de documentação a PERSONAL. CI pendente. RUN01 IN_PROGRESS: faltam roteamento empírico, medição real de bytes/token/latência, compactação mais ampla mantendo memória relevante, geração/validação e checkpoint por seção, perfil de CPU/RAM e certificação no Northflank. Não ativada fila durável, sem deploy ou alteração de main.
