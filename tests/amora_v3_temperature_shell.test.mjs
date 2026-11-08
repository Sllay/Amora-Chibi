import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import assert from "node:assert/strict";
import test from "node:test";

const source = readFileSync(new URL("../web/amora-shell.js", import.meta.url), "utf8");
const formatter = runInNewContext(
  source.slice(source.indexOf("function formatSocialPersonaABC("),
               source.indexOf("function formatPartialProgress("))
  + "\nformatSocialPersonaABC",
  { seconds: (ms) => (ms / 1000).toFixed(2) + "s" },
);

test("V3 temperature A/B displays full blind output and calibrated copy signals", () => {
  const output = "Oxente, a criatividade vale mais que a cópia. ".repeat(16);
  const display = formatter({
    kind: "amora_v3_temperature_ab", case: "safada",
    review_instruction: "Avaliar originalidade primeiro.",
    sampling_profiles: {
      control: {temperature: 0.50, top_p: 1.0},
      test: {temperature: 0.95, top_p: 0.90},
    },
    sampling_comparison_note: "Dois parâmetros alterados.",
    variants: [
      { candidate_id: "candidate-1", validated: true, text: output,
        calibration_overlap: {max_consecutive_example_words: 9, example_copy_signal: true},
        latency_ms: 17500 },
      { candidate_id: "candidate-2", validated: false, candidate_output: '{"moves":',
        error_code: "EXPERIMENT_JSON_INVALID", latency_ms: 9000 },
    ],
    rubric: {"novidade frente aos exemplos": null},
    reveal_token: "concealed",
  });
  assert.ok(display.includes("temperature/top_p dinâmicos"));
  assert.ok(display.includes(output));
  assert.ok(display.includes("máximo=9 palavras consecutivas"));
  assert.ok(display.includes("sinal de cópia=true"));
  assert.ok(display.includes("controle: temperature=0.5 · top_p=1"));
  assert.ok(display.includes("teste: temperature=0.95 · top_p=0.9"));
  assert.ok(display.includes("não vinculados a candidate-1/2"));
  assert.ok(display.includes("Dois parâmetros alterados."));
  assert.ok(display.includes("JSON INVÁLIDO · Core não executado"));
  assert.ok(!display.includes("A_v3_base_temp050"));
  assert.ok(!display.includes("B_v3_roleplay_temp075"));
});

test("V3 temperature job is routed to blinded display", () => {
  assert.ok(source.includes('job.result?.kind === "amora_v3_temperature_ab"'));
  assert.ok(source.includes("formatSocialPersonaABC(job.result)"));
});

test("NVIDIA timeout is displayed as a transport failure, never Core refusal", () => {
  const result = formatter({
    kind: "amora_v3_temperature_ab",
    case: "sacanagem",
    variants: [
      {
        candidate_id: "candidate-1",
        validated: false,
        validation_status: "not_run",
        error_code: "EXPERIMENT_NVIDIA_TIMEOUT",
        failure_stage: "waiting_first_event",
        timeout_kind: "idle",
        stream_diagnostics: {elapsed_ms: 55090, first_visible_ms: null},
        latency_ms: 55090,
      },
      {
        candidate_id: "candidate-2",
        validated: false,
        validation_status: "rejected_by_core",
        candidate_output: '{"moves":[],"closing":"statement"}',
        error_code: "EMPTY_REALIZATION",
      },
    ],
    transport_deadlines: {
      idle_seconds: 100,
      max_total_seconds: 225,
      production_unchanged: true,
    },
    rubric: {},
    reveal_token: "secret-looking-but-not-sensitive",
  });
  assert.ok(result.includes("TIMEOUT NVIDIA · Core não executado"));
  assert.ok(result.includes("Nenhuma fala recebida da NVIDIA"));
  assert.ok(result.includes("etapa=waiting_first_event · timeout=idle"));
  assert.ok(result.includes("REJEITADO pelo Core"));
  assert.ok(result.includes("idle=100s · teto=225s"));
  assert.ok(!result.includes("candidate-1 · REJEITADO pelo Core"));
});
