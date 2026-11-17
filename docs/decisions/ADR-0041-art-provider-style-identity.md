# ADR-0041 — Geração de arte com identidade visual fora do provider

Status: ACCEPTED (implementação experimental, sem certificação live)
Data: 2026-11-17

## Problema
A Amora precisa desenhar com identidade visual consistente e custo alvo R$ 0,
sem acoplar a personalidade/autoimagem a um endpoint SDXL específico.
O proprietário já possui cinco direções da personagem; imagens ainda não foram
fornecidas ao runtime canônico neste PR.

## Alternativas
1. Somente prompt em API SDXL gratuita: simples, mas identidade variável.
2. Carregar LoRA e referências diretamente no SDXL da OVH: **não documentado**.
3. Serviço adapter com estilo/preset/versionamento canônicos e futura troca
   para backend com referências/LoRA: decidido.

## Decisão
- Criar `amora.art`, isolado do chat e persistência SQL em migração.
- A identidade estética do Core **não** é um prompt ou um provider.
- `OVH SDXL` é o primeiro adapter, exclusivo text2img, de forma opt-in.
- Requisições autenticadas e consentimento remoto explícito; worker Discord
  restringe o uso a ator canônico autorizado (`PERSON_BK`).
- Não enviar referências/LoRA para OVH; requests com essas características
  devem falhar de maneira verificável.
- Gerar arte não muda, por si, aparência ou memória canônica.
- Nunca selecionar fallback automaticamente sem contrato e aprovação.
- Curadoria local antes de LoRA; dataset com licença/sha/visão/caption;
  evitar treinamento em deriva de imagens não revisadas.
- Produção e promoção dependem de CI, avaliação humana das imagens,
  teste live com credencial e revisão dos custos.

## Consequências
O estilo inicial é somente aproximação. A interface pode chamar outros
renderizadores no futuro, mas não promete consistência de identidade no OVH.
O modelo SDXL da OVH tem EOL anunciado para 2027-02-10.

## Revisitar quando
O usuário disponibilizar folhas canônicas e houver backend com img2img/LoRA,
privacidade e cotas adequadas e comprovadas.
