A auditoria parte do main remoto confirmado em `7504d23d0203b6821ae498f1d1439424f263a3ac`. Reproduz dez causas de defeito em onze assertions estritas, sem alterar Core, persona, primary ou arquivos V2/V3. O relatório liga esperado/observado a funções, reproduções, propostas e riscos, e limita o plano de reorganização à futura decisão da Fase 2.

Inclui definições persistidas de exatamente dois agentes (`amora test`/`core especionar`), verificadas via config/read do Codex 0.167.0-alpha.4. Corrige duas expectativas obsoletas que chamavam JSON inválido de rejeição do Core e amplia o gate JavaScript para as suites Shell que estavam excluídas.

Validação executada: baseline Python928pass; última suíte completa934pass/10xfail; seleção final forense6pass/11xfail (inclui F10 adicionado depois); --runxfail11fail/6pass, como reprodução dos defeitos; Node132pass; Avatar7pass; build e smoke container; 58 testes no runtime Alpine; Chromium real com UI/SSE/fila, texto visível, 401/403 e zero page errors. Nenhuma geração live, áudio físico, deploy ou Fase 2/3.

Achados prioritários: SECRET no raw event, relato de terceiros concedendo preferência persistente, falhas de autoria, trimming sem pós-condição/plano stale, timeout NVIDIA confundido com budget do caller, father_of não reconhecido, replay incompatível com realize e literal ACIR no system. Xfails não representam bugs corrigidos; o relatório separa riscos demonstrados e hipóteses.

Relatório: docs/audit/PHASE1_CORE_FORENSICS_2026-11-08.md.
