# Gate de dependências — OmniVoice experimental CPU

Data: 2026-10-26. **BLOCKED para publicação do worker.** Não é reprovação da
imagem Core Alpine, que não instala esse runtime. Não houve deploy ou billing.

Instalação real, em venv Linux/glibc separado, do lock
`deploy/omnivoice-worker.lock`: 91 pacotes, hashes de wheels, CPython 3.12 x86_64,
torch/torchaudio 2.11.0+cpu e omnivoice 0.2.1. Importação oficial passou,
torch.version.cuda=None e pip check sem inconsistências. Nenhum peso/modelo ou
referência real foi carregado. Nenhuma dependência entrou no deploy/python.lock,
no deploy/voice-worker.lock congelado ou no Dockerfile Core.

pip-audit 2.10.1 encontrou **41 advisories em três pacotes**:

| Pacote instalado | Findings | Correção indicada pelo advisory | Bloqueio de compatibilidade |
|---|---:|---|---|
| Pillow 12.3.0 | 37 | 13.0.0 | Gradio 6.30.0 exige pillow>=8,<13 |
| setuptools 81.0.0 | 3 | 83.0.0 / 84.1.0 | Torch CPU 2.11.0 exige setuptools<82 |
| torch 2.11.0+cpu | 1 | 2.13.0 | Torchaudio CPU 2.11.0 é pareado com Torch 2.11.0 |

Não atualizar versões além dos constraints à força nem alterar metadados do
wheel oficial para fazer pip check passar. Não instalar wheel Torch PyPI com
CUDA para resolver essa combinação. Também não remover Gradio/Pillow/setuptools
com --no-deps fingindo completude do ambiente oficial. Custos e compatibilidade
de um futuro perfil inference-only exigem revisão própria, sem alterar pesos.

O primeiro audit direto pulava torch/torchaudio porque PyPI não tem release com
sufixo +cpu. `tools/security/audit-omnivoice.py` consulta public version
2.11.0, a versão correspondente dos mesmos fontes; **não altera o artifact de
instalação**. Cobertura completa de 91 pacotes, sem skips. Isso expôs também
PYSEC-2025-194 no Torch. O script falha para advisories, cobertura incompleta,
versões locais não mapeadas ou wheel que não seja CPU. A CI executa esse gate
adicional; não há continue-on-error, redução de severidade ou troca de scanner.

Inventário resumido verificável:
[omnivoice-advisories-2026-10-26.json](omnivoice-advisories-2026-10-26.json).
Os relatórios completos são artifacts de auditoria/CI; não contêm material da
referência. Findings não equivalem a exploração comprovada pelo endpoint TTS.
Não se produziu análise de reachability/VEX individual dessas 41 entradas;
logo não se declara NOT_AFFECTED nem se suprime nenhuma delas por inferência.

Comando reproduzível, sem instalar o runtime neural no ambiente de auditoria:

```bash
python -m pip install pip-audit==2.10.1
python tools/security/audit-omnivoice.py --output reports/omnivoice-audit.json
```

Resultado nesta revisão: exit 1, coverage_complete=True, affected_packages=3.
Gate HIGH/CRITICAL incluindo unfixed da imagem Core permanece exatamente como
estava. Core e worker canônico continuam com locks separados e audits próprios.

Para reavaliar: patches oficiais compatíveis (incluindo torch/torchaudio CPU),
constraints upstream corrigidos, ou perfil de inferência oficialmente suportado
e auditável. Depois reinstalar lock/hash, pip check, import oficial CPU, regressões
e benchmark real. Até lá não publicar porta Lightning nem ligar Northflank ao
worker experimental. Experimento privado isolado exige assets próprios e não
constitui certificação de segurança. Não existe fallback pago ou automático.
