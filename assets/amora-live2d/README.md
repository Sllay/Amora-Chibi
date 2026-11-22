# Amora Bolinha — organização original e prova Ayatsuri2D

**Estado: 10 camadas fonte preservadas; rig parametrizado experimental com deformações reais em MOC3 (ainda sem validação visual em VTube Studio/Cubism).**

Os dez arquivos em `layers/` são os originais do usuário em 1254 × 1254.
**Somente a camada #1 (branco esquerdo na tela) e #3 (branco direito na tela)
foram substituídas pelas duas novas versões ampliadas**.
Os demais oito arquivos foram carregados byte por byte, sem mudar desenho,
efeito, posição, nome da fonte, ou pixels.

## Ordem EXATA enviada (fundos -> frente)
1. Branco esquerdo na tela — **NOVO** (20261121200803)
2. Íris esquerda — original
3. Branco direito na tela — **NOVO** (20261121200806)
4. Íris direita — original
5. Corpo + dois chifres — JPEG original
6. Pálpebra inferior esquerda — original
7. Pálpebra inferior direita — original
8. Pálpebra superior direita — original
9. Pálpebra superior esquerda — original
10. Boca — original

### Exceção necessária somente para a prévia e exportação
A camada #5 chegou em JPEG opaco: se colocada literalmente por cima das quatro
camadas de olho, irá escondê-las. O arquivo original NÃO é modificado:
a etapa `prepare_assets.py` apenas cria uma cópia PNG técnica e desenha o
corpo antes dos olhos para visualizar / exportar corretamente. Os nomes, PNGs,
JPEG e ordem original ficam preservados na pasta `layers/` e `manifest.json`.

## GitHub Actions no celular
Rodar o workflow `Amora Ayatsuri2D headless export (experimental)`
gera arte organizada e prévia, um HTML offline interativo para experimentar expressões no celular, e o arquivo `.moc3` com keyforms vinculados aos parâmetros do Cubism.
Depois, baixar o artefato. A versão experimental inclui fechamento dos dois olhos, deslocamento das íris (limitado pelo branco), pálpebras móveis, abertura/forma simplificada da boca e inclinação/respiração.

**Limitações honestas:** a região do rosto está num JPEG opaco sem segmentação facial própria; não existe ainda mesh local de pele puxando o contorno como um rig profissional. Nesta etapa as pálpebras e o branco do olho se deformam, com o rosto original por trás. A boca de uma única imagem original não consegue criar um novo interior da boca ou fonemas reais. Respiração é uma escala global discreta, não física slime. HTML é uma prévia de comportamento, NÃO é o mesmo renderizador do MOC3.

## Testar no telefone antes da integração

1. Abra [GitHub Actions — exportação Ayatsuri2D](../../.github/workflows/ayatsuri2d-avatar-export.yml) e a última execução bem-sucedida.
2. Baixe o artefato `amora-bolinha-rig-live2d-teste-mobile`.
3. Abra `artifacts/amora-mobile-preview.html` em navegador compatível com HTML local. As 10 imagens estão embutidas; não precisa internet.
4. Teste as expressões, sliders, piscadas automáticas e acompanhamento do olhar por toque. O bundle real para VTube Studio/Cubism fica em `artifacts/AmoraBolinha`.

**Não foi feita integração no Core, Discord ou app da Amora.** O PR permanece isolado até avaliação visual do usuário e validação no runtime Cubism.

