"""Conservative character-based envelope estimates, explicitly not tokenizer counts."""
import json
import math


class ContextBudgetExceeded(ValueError):
    code = 'CONTEXT_ENVELOPE_OVERFLOW'
    def __init__(self, estimated, budget):
        self.estimated = estimated
        self.budget = budget
        super().__init__(self.code)


def estimate_payload(payload):
    return math.ceil(len(json.dumps(payload, ensure_ascii=False, separators=(',', ':'))) / 4)


def ensure_payload(context, payload):
    estimated = estimate_payload(payload)
    if estimated > context.token_budget:
        raise ContextBudgetExceeded(estimated, context.token_budget)
    return estimated


def estimate_context(context):
    from .prompt_frame import render_trusted_protocol, render_current_turn
    from .realization import realization_instruction
    from .providers_identity import _identity_history
    messages = [{'role': 'system', 'content': render_trusted_protocol(context) + '\n\n' + realization_instruction(context)}]
    messages.extend({'role': role, 'content': content} for role, content in _identity_history(context))
    messages.append({'role': 'user', 'content': render_current_turn(context)})
    return estimate_payload({'messages': messages}) + 128  # reserve transport fields
