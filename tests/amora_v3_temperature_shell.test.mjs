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
  assert.ok(display.includes("temperatura 0,50 × 0,75"));
  assert.ok(display.includes(output));
  assert.ok(display.includes("máximo=9 palavras consecutivas"));
  assert.ok(display.includes("sinal de cópia=true"));
  assert.ok(display.includes("REJEITADO pelo Core"));
  assert.ok(!display.includes("A_v3_base_temp050"));
  assert.ok(!display.includes("B_v3_roleplay_temp075"));
});

test("V3 temperature job is routed to blinded display", () => {
  assert.ok(source.includes('job.result?.kind === "amora_v3_temperature_ab"'));
  assert.ok(source.includes("formatSocialPersonaABC(job.result)"));
});
