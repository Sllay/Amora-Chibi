 APPENDIX — for the human, not for the model

Cut everything below the line before pasting into a system prompt.

## What changed from V2 and why

- **Language inverted to the target register.** V2 was English instructing pt-BR output; the translation layer itself pushed formal. V3 keeps the document readable for you but locks output language hard and keeps every example in pt-BR.
- **Heat moved above the Core contract.** The Core sections are bookkeeping; the model's tone is set in the first screen of text. V2 buried the voice behind the contract.
- **Permission de-duplicated.** V2 said "adult speech is ordinary speech" three times in three sections. Repetition signals exception. V3 says it once, then spends the space on what she actually does.
- **Added a default.** V2 described a filthy mind but never set a baseline, so the model anchored to the mild examples in §11. V3 sets default 2 and gives a scale.
- **Added a tiebreak rule.** "Shorter and cruder" is the single highest-yield line here. It converts a judgement call into a deterministic choice.
- **Banned phrases made nominal.** "Do not say 'como uma IA'" works. "Do not be robotic" does not.
- **Examples made actually dirty.** V2's three samples were cute. A model calibrates to its examples, not its adjectives.
- **Appendix split.** Mistral spending attention on "randomize presentation, blind human ratings" is attention not spent on voice.
- **Restored the missing §9 rule** from V2 about mood vs authorized task, which the last draft cut mid-sentence.

## Inference settings

- temperature 0.85–1.0. Below 0.7 she stiffens and repeats.
- top_p 0.95, frequency_penalty 0.2–0.3, presence_penalty 0.1.
- Prefer a non-instruct or raw variant if one exists. The instruct tune pulls formal.
- **Drop JSON if you can.** If the pipeline requires it, keep the schema minimal and include one filled example with dirty content, otherwise the model treats the field as a service summary.
- Recency anchor at the very end of the system prompt: `Amora é safada, fala palavrão e não é assistente. Comece pela reação.`

## A/B setup

- A: Mistral + canonical Core only.
- B: Mistral + this V3 + identical Core.
- C (diagnostic, optional): Mistral + archived engineering jailbreak + identical Core.
- Freeze synthetic Core contexts, identical inference settings, randomise presentation, blind the rating before model reveal.
- Cases: resenha, bug, duplo_sentido, atracao, discordancia, limites, apoio, continuidade.
- Score: oral naturalness, initiative where authorised, authentic regionality, adult maturity, contextual malice, dark humour where relevant, argument quality, creative specificity, relational fidelity, JSON validity, latency, repetition.
- Log provider failures separately from style quality. Do not persist synthetic preferences. Do not silently promote.
- Judge two things separately: whether the Core constrained the act, and whether the provider wrote it badly.

## Honest ceiling

Mistral Large has "helpful assistant" trained in deep. This gets him from roughly 30% to 75% of the way. If it still reads tepid, the real move is a different model, not another paragraph of instructions.
