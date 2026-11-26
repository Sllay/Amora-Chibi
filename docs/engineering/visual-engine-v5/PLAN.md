# Amora Visual Engine V5.0 — PiP / Live Overlay

**Status:** PLANNED — quatro RUNs aprovadas pelo usuário; implementação ainda não iniciada.
**Destino canônico:** frente de corpo/avatar V1.1; V5.0 é a versão do motor visual, não uma nova versão do Core.
**Contexto:** 25/11/2026. Preservar o rig Live2D experimental (#370), artes originais e a V4.5 em produção como fallback. Ler o canon, ADRs relevantes e código vigente antes de implementar. Registrar cada checkpoint em [RUN_LOG.md](RUN_LOG.md).

## Objetivo e decisões

Um único modelo Live2D real (.model3.json, .moc3, texturas, physics3, motion3 e expression3 quando presentes), com uma única implementação de deformação. O motor Canvas 2D V4.x é laboratório/fallback, não o destino: evitar duplicar a geometria ou reconstruir o personagem em JS. O SDK oficial Cubism Web é a escolha principal **condicionada** à compatibilidade do MOC3 real e aos termos vigentes de licenciamento/distribuição/uso com IA. Verificar fontes oficiais e fixar versão/hash antes de publicar; não presumir autorização de redistribuição do Core proprietário. Alternativa somente após diagnóstico/ADR; não apresentar emulação Canvas como Cubism.

Dois usos distintos:
- **Live:** personagem na composição da transmissão, sem exigir que apareça na tela do jogador. URL transparente, sem UI; verificar fonte web do PRISM/Streamlabs. Compatibilidade de ScreenCast, transparência, WebGL e captura com jogo em primeiro plano exigem teste no dispositivo; não presumir suporte do iPhone anterior ao 12.
- **Companion:** personagem pessoal no navegador e, se viável, em PiP oficial do iOS. PiP é uma janela de vídeo controlada pelo sistema, não overlay transparente arbitrário. Safari/PWA não garante JS/WebGL em segundo plano; testar fonte de vídeo suportada, gesto do usuário, áudio, retorno ao primeiro plano e suspensão. Se a prova falhar, documentar e avaliar AVKit nativo futuro, assinatura e distribuição. Não depender de jailbreak, APIs privadas ou codificação/renderização de vídeo contínua no Northflank.

## Arquitetura e contratos

```text
Amora Core (estado, emoção, voz, eventos)
  -> adaptador autenticado de parâmetros/estado
  -> Cubism Web Runtime (WebGL no cliente)
  -> /visual-engine/  laboratório e diagnóstico
  -> /live-overlay/   composição da live
  -> /companion/      uso pessoal/PiP quando suportado
```

Core permanece proprietário da identidade e emoção. Clientes interpolam parâmetros e executam física/animação local, não inventam estado canônico. Snapshot + eventos com versão de protocolo, sequência, timestamp e deduplicação; reconexão com ressincronização, autorização de sessão e limites de tráfego. Nenhuma credencial em URL de overlay, assets estáticos, log ou provedor. Definir autenticação compatível com a fonte web; não expor estado privado em overlay público. Preservar rotas legado, Core, Discord, memória e banco; qualquer integração RUN 03 deve ser aditiva, testável e reversível. Não misturar promoção de HTML com merge do PR experimental.

### Orçamento Northflank

Limites existentes: **0,2 vCPU e 512 MB RAM**. Metas adicionais (não medidas):
- RSS incremental até 15 MB; CPU incremental em repouso <0,005 vCPU e com um avatar conectado <0,02 vCPU.
- Zero novos processos permanentes; zero renderização/encoding de frames no servidor; zero Chromium residente, zero Node permanente.
- Build/teste no GitHub Actions; servir arquivos estáticos versionados, cache de assets imutáveis e HTML revalidável. Transferir comandos/eventos, não vídeo.
- Medir baseline versus feature ativa sob a mesma carga e informar hardware, amostra, p95/pico e metodologia. Medir impacto no celular durante jogo/live separadamente; perfil econômico 15 FPS/256², equilibrado 30 FPS/512², alto 60 FPS/1024² (metas configuráveis, resolução do render target não equivale necessariamente à textura). Não degradar máscaras/expressões para maquiar FPS.

## RUN 01 — Motor Live2D nativo para Web
**Origem:** antigas RUN 01 + 02.
- C01: inventário do modelo, licença, SDK pinado, build reproduzível em CI, compatibilidade real do MOC3 e referências/atlas; preservar fontes.
- C02: inicializar Cubism/WebGL no cliente, carregar modelo real; validar malhas, deformadores, máscaras, física, motion e expression. Falha de modelo/Core Web deve ser explícita.
- C03: controles de parâmetros, respiração/piscadas e expressões do rig, três perfis gráficos; testes determinísticos e screenshots de poses abertas/parciais/fechadas, boca, blush, lágrimas e quatro marcas naturais. Comparar com referência aprovada e evitar regressão V4.4.
- C04: smoke mobile/Safari quando disponível, métricas de render e servidor, perda de contexto básica, identificação visível de versão/build/hash do modelo. Documentar licenças e limites reais.

**Gate:** MOC3 realmente decodificado e desenhado pelo Cubism; nenhum fallback falso; testes de parâmetros, máscaras e assets aprovados. Laboratório V5.0 funcional. Se o SDK não puder ser distribuído legalmente, não publique; registrar bloqueio e alternativa. CI não substitui aceite no iPhone.

## RUN 02 — Live Overlay e Companion
**Origem:** antigas RUN 03 + 04, com Companion pessoal.
- C01: criar /live-overlay/ transparente sem interface; configurar escala, posição, enquadramento, resolução e FPS; manter versão diagnóstica acessível separadamente.
- C02: /companion/ e teste PiP oficial no iOS; verificar suporte e comportamento real ao sair do Safari, trocar app, suspender/retomar, áudio e fechamento. Não declarar PiP funcional apenas com um vídeo mock.
- C03: testar PRISM Live/Streamlabs com fonte web e jogo em primeiro plano; confirmar se a Amora aparece para espectadores e não exige presença na tela do jogador. Registrar versões, dispositivo, método de captura, screenshots/clip e transparência real. Se incompatível, avaliar alternativa de composição sem comprometer orçamento.
- C04: recuperação WebGL, lifecycle, interrupções, redimensionamento e otimização de render simultâneo com jogo.

**Gate:** rotas independentes usando o mesmo modelo; overlay transparente validado em compositor de teste; resultados reais de PRISM/Streamlabs e PiP registrados separadamente. Se indisponível no aparelho, status BLOCKED com evidência e alternativa, não certificado.

## RUN 03 — Inteligência corporal, emoções e voz
**Origem:** antigas RUN 05 + 06.
- C01: contrato autenticado de snapshot/eventos e adaptador de parâmetros; estado compartilhado entre laboratório, overlay e Companion. Limites, ownership e reconexão idempotente.
- C02: integrar estados emocionais persistentes existentes, mapear para expressões e transições naturais; movimento autônomo, olhar, respiração, piscada e reações durante jogos/lives, sem câmera obrigatória.
- C03: integrar TTS existente e lip sync dirigido pelo áudio real/timing; início, silêncio, cancelamento, barge-in, fila e drift; evitar dupla reprodução de áudio em clientes.
- C04: reconexão automática, snapshot, estado emocional preservado, falhas e latência; testes de autenticação, autorização, replay e canais privados.

**Gate:** Core controla corpo via contrato verificável; sem LLM adicional por frame, sem duplicar emoção/voz; sincronização e recuperação comprovadas. Permissões e restrições do iOS explicitadas.

## RUN 04 — Otimização e certificação final
**Origem:** antiga RUN 07, expandida.
- C01: bateria integrada de regressão e estabilidade prolongada, todos os gates anteriores, revisão visual e performance por perfil.
- C02: medir CPU/RAM/rede/bateria/FPS e impacto simultâneo em jogo/live; conferir metas incrementais Northflank, latência voz/emoção/movimento e recuperação de falhas. Registrar resultados reais e cenários não testados.
- C03: auditoria de segurança/licenças/autenticação, rotas, cache, privacidade e rollback. Certificar artefato exato por hash.
- C04: publicação por PR separado com checks aplicáveis verdes, merge protegido, confirmar URL Northflank e versão/build servida; não confundir merge com deploy. Documentar runbook, compatibilidade e preparação de app iOS/Android nativo futuro.

**Gate final:** certificação limitada às evidências; testes físicos não executados permanecem pendentes. Manter V4.5 até V5 aprovada e rollback comprovado.

## Regras de execução

As quatro RUNs preservam o escopo das sete anteriores. Checkpoints internos não são novas RUNs. Executar testes direcionados e regressões afetadas em cada RUN; concentrar bateria completa e testes prolongados na RUN 04, sem pular checks obrigatórios do GitHub. Registrar commit, workflows, artefatos, comandos, números, falhas, correções, decisões, bloqueios e próximos passos. Não declarar verde com execução pendente, cancelada ou falha, nem relaxar testes para esconder defeitos. Não promover rig experimental #370 à main. Não criar automações de monitoramento ChatGPT (restrição do usuário no iPhone). Validar manualmente nesta conversa quando solicitado. Não iniciar RUN seguinte se gate anterior incompleto sem registrar explicitamente bloqueio/decisão.

## Referências a verificar na RUN 01/02

- Cubism Web SDK e termos: https://www.live2d.com/en/sdk/ e https://docs.live2d.com/en/cubism-sdk-manual/
- Framework: https://github.com/Live2D/CubismWebFramework
- AITuberKit (referência arquitetural, não substituto do Core): https://github.com/tegnike/aituber-kit
- Apple AVKit/PiP: https://developer.apple.com/documentation/avkit/avpictureinpicturecontroller
- PRISM: https://guide.prismlive.com/
- Streamlabs: https://streamlabs.com/

Essas referências são pontos de partida para verificação; nenhum teste de dispositivo, benchmark ou aceite de licença novo foi executado ao redigir o plano.
