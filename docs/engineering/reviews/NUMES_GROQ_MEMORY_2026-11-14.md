# Opt-in Numes dialogue and Groq memory specialist

Northflank Core environment (only after the supporting revision is deployed):
- AMORA_SECRET_NUMES_API_KEY: inference key from Numes. Never commit it.
- AMORA_SOCIAL_PRIMARY=numes
- AMORA_NUMES_DIALOGUE_MODEL=dealignai/deepseek-v4.1-flash-uncensored-fp8
- AMORA_GROQ_MEMORY_MODEL=openai/gpt-oss-120b

Existing AMORA_SECRET_GROQ_API_KEY stays. AMORA_GROQ_DIALOGUE_MODEL remains independent. Defaults unchanged when no new variables are set. Numes is registered only on explicit selection with a key, social-only; Groq specialist is source-only durable reflection, with existing Groq Qwen and other eligible providers as fallbacks. Operator memory reflection probe uses specialist if configured. The Numes adapter uses standard nonsexual conversational expression, not the external optional persona file. Existing identity/memory/privacy gates and output validation are unchanged; no database migration, worker update, or reset.

Numes sends Chat Completions JSON object with zero prompt/completion price ceilings, store=false, data_collection=deny, zdr=true, require_parameters=true. These are documented request controls, not evidence a compatible free ZDR endpoint currently exists. Flat request charges and account-level fees are separate; keep a spending cap at the provider. No automatic relaxation of privacy or pricing. A provider error falls through existing routing; no authenticated live tests have been run.

Check health social_primary_provider=numes, groq_memory_model=openai/gpt-oss-120b. Test synthetic nonsexual social reply and memory reflection probe (no writes), then real memory write and recall. Check logs for provider/model on realization and reflection. Revert AMORA_SOCIAL_PRIMARY=groq and unset AMORA_GROQ_MEMORY_MODEL to restore old routing. Limits/rates and endpoint capabilities still need authenticated verification.