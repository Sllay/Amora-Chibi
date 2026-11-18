# Amora Art — infraestrutura inicial SDXL (experimental, opt-in)

## Escopo implementado
- Módulo isolado `core/amora/art/`, sem mudanças em schema, ACIR, memória ou migração Aiven.
- Estilo-base versionado `amora-cartoon-handdrawn-v1` e cinco presets (portrait, full_body, sticker, scene, expression).
- Rota autenticada `GET /v1/art/capabilities`.
- Rota `POST /v1/art/generate`, desativada por padrão (`AMORA_ART_ENABLED=1` para habilitar).
- O Core aplica autenticação, vínculo de ator para worker Discord, autorização explícita de processamento remoto, fila de uma geração por vez, validação superficial de bytes, limite de 8 MiB e erros tipados.
- O Core não salva automaticamente imagens geradas; seleção e publicação dos exemplos precisam ser intencionais.
- Sem envio de imagens canônicas para providers nesta fase.

## OVHcloud: contrato confirmado em 17/11/2026
- Endpoint: `POST https://stable-diffusion-xl.endpoints.kepler.ai.cloud.ovh.net/api/text2image`.
- Corpo: `{"prompt":"...","negative_prompt":"..."}`. Retorna bytes da imagem.
- Token opcional pela SecretStore da Amora: `AMORA_SECRET_OVH_AI_ENDPOINTS_ACCESS_TOKEN`. Sem token, o adapter usa modo anônimo de baixa taxa; nunca envia `Bearer None`.
- A OVH anuncia SDXL como gratuito, mas custo/limites podem mudar.
- **Não** está documentado nesse endpoint: carregamento de LoRA própria, img2img, pose/ControlNet, seed ou upload de imagem de referência.
- Degradação honesta: requests com `reference_ids` ou `lora_id` retornam erro explícito, não ignoram controles silenciosamente.
- O catálogo anuncia descontinuação do SDXL em **10/02/2027**. A arquitetura não pode depender dele permanentemente.

## Certificação live independente (Amora offline)

O Core, o Discord, Northflank e os bancos SQL/PostgreSQL **não precisam estar
ativos** para o teste de geração:

- Script isolado: `tools/art/ovh_live_probe.py`. Usa o **mesmo adapter** de
  produção em memória, sem inicializar `AmoraCore`, sem escrever estado ou fazer
  migrations.
- Prompt sintético de bichinho cartoon; nenhuma imagem original da Amora ou
  dado privado vai para a OVH.
- A documentação oficial OVH admite **até 2 requisições/minuto anônimas** por
  IP/modelo, mesmo sem token. Ao testar com token, o limite divulgado é 400
  requisições/minuto/projeto/modelo. São limites sujeitos a revisão.
- Executar em ambiente com Python e pacote Core instalado:
  `python tools/art/ovh_live_probe.py --output reports/art-probe`.
- O script gera `probe-result.json` com códigos de erro e, se houver sucesso,
  `ovh_sdxl_live.jpg/png/webp`. Status 429/403 ou falha de rede **não** é PASS.
- A execução GitHub Actions é **exclusivamente opt-in**, em PR experimental, sem
  secrets, banco, persistência ou publicação. O artefato permite revisão visual.
- A imagem recebida confirma somente geração `txt2img`, não fidelidade de
  identidade ou suporte a LoRAs.

## Configuração mínima
1. Opcional: provisionar credencial de API no painel OVH para taxa autenticada; não colocar no Git, docs, prompts ou mensagens. O modo anônimo pode ser usado para certificação experimental.
2. Configurar no servidor Core, como secret `AMORA_SECRET_OVH_AI_ENDPOINTS_ACCESS_TOKEN`.
3. Habilitar `AMORA_ART_ENABLED=1` no Core após consentimento sobre envio remoto de prompts.
4. Requisição operator/admin: `POST /v1/art/generate` com
   `{"prompt":"uma expressão alegre","preset":"sticker","remote_processing_allowed":true}`.
   É obrigatório autenticar a requisição com credencial autorizada. A resposta possui `image_base64`, `content_mime`, `style_version`, `provider` e `prompt_sha256`.
5. O worker de mensagens deve fornecer um ator previamente vinculado a `PERSON_BK` no `PeopleProfileService`; spoof de ator ou usuário desconhecido é negado.

## Caderno visual canônico (a completar com arquivos aprovados)
O usuário possui vistas frontal, traseira, superior, inferior e lateral. Não presumir que
os pixels estão disponíveis no servidor nem inventar suas características. Registrar
arquivos originais e SHA-256 no pacote canônico, com licença/autoria, vista, data,
uso autorizado e aprovação manual; vista 3/4 é conveniente, não bloqueia a etapa 1.

Identidade **não** é equivalente a estilo:
- Identidade: proporções, linha do rosto, cabelo, chifres, roupas, cores, rig/body.
- Estilo: mão leve, silhueta clara, lineart orgânica, cor suave e sombreamento contido.
- Prompt fixo apenas estabelece um visual aproximado. Não afirmar que reproduz fielmente a aparência antes de verificar referências com um backend que as aceite.

## LoRA personalizada, sem pipeline ilusório
1. Selecionar uma LoRA de estilo **compatível com SDXL** e de licença verificada em backend que suporte seu carregamento. A OVH atual não serve essa finalidade.
2. Produzir imagens candidatas do personagem com controle visual real, não só texto; rejeitar desenhos com deriva anatômica ou visual.
3. Preferir combinar material original do autor com exemplos gerados *manualmente revisados*. Não treinar apenas em saídas geradas e presumir fidelidade.
4. Montar dataset inicial de aproximadamente 20–40 imagens **aprovadas**, diversidade de poses e vistas, arquivo + caption para cada uma, separar validação, registrar modelo/LoRA/fonte/versão.
5. Treinar em cloud usando notebook oficial SDXL DreamBooth LoRA da Hugging Face no Colab ou GPU Kaggle; ambos gratuitos sob cotas dinâmicas, sem garantia de aceleração disponível.
6. Validar em seeds e prompts não vistos, comparar identidade/traço/falhas e só então promover adapter LoRA ao runtime da Amora.
7. O formato de artefato previsto para uma LoRA é `safetensors`; salvar hashes, modelo base, licença e instruções de inferência. Nunca subir checkpoints pesados no Git.

## LoRAs SDXL candidatas para obter o traço-base

Catálogo apenas para **avaliação**, não instaladas na OVH nem no Core:

| Candidata | Objetivo | Fonte |
|---|---|---|
| `xinhai342/lora-trained-style_cartoonline` | linhas cartoon legíveis; candidata ao traço desenhado à mão | https://huggingface.co/xinhai342/lora-trained-style_cartoonline |
| `shawn323/sd-xl-lora-cartoon` | acabamento cartoon generalista | https://huggingface.co/shawn323/sd-xl-lora-cartoon |
| `issaccyj/cartoonillustration` | variação de cartoon ilustrado | https://huggingface.co/issaccyj/cartoonillustration |

As model cards indicam SDXL/openrail++; cada licença e derivação devem ser
revalidadas no momento da utilização. Nenhuma está visualmente aprovada para a
Amora, nem pode ser servida pelo endpoint OVH atual. Testar em backend de
inferência SDXL com `load_lora_weights`/ComfyUI, comparar com desenhos
canônicos, então curar somente outputs aprovados.

As cinco vistas originais **não** viram 20 imagens por espelhamento/rotação
artificial; a diversidade de pose e composição precisa ser real. Preservar
conjunto separado de referência/validação para detectar overfitting.

## Próximas gates, antes de produção
- Teste offline de contrato; teste live da OVH requer credencial e não foi executado neste PR.
- Validar que a plataforma real não altera MIME/dimensões e que mensagens remotas ficam dentro do budget.
- Adicionar backend de referências e LoRA certificado, com comparação visual humana e fallback explícito; **não** ativar failover ainda.
- Adicionar repositório de referências canônicas com acesso controlado, dedupe e consistência de versão para V1.2.
- Certificar o transporte de anexos no Discord de teste e permissões do bot antes de anunciar disponibilidade para todos.

Fontes oficiais:
- https://www.ovhcloud.com/en/public-cloud/ai-endpoints/catalog/stable-diffusion-xl/
- https://www.kaggle.com/docs/efficient-gpu-usage
- https://colab.research.google.com/github/huggingface/notebooks/blob/main/diffusers/SDXL_DreamBooth_LoRA_.ipynb
