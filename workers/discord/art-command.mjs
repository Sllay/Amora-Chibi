import {
  discordDeferredInteractionResponse,
  discordInteractionEdit,
} from './commands.mjs';

// Command only exists in the test guild until an explicit live certification.
const PRESETS = new Set(['portrait', 'full_body', 'sticker', 'scene', 'expression']);
const MAX_ART_BYTES = 8 * 1024 * 1024;

export function discordArtCommandDefinition() {
  return {
    name: 'amora-arte',
    type: 1,
    description: 'Gerar uma ilustração com o estilo experimental da Amora',
    options: [
      {
        type: 3,
        name: 'prompt',
        description: 'O que a Amora deve ilustrar',
        required: true,
        min_length: 1,
        max_length: 500,
      },
      {
        type: 3,
        name: 'preset',
        description: 'Tipo de ilustração',
        required: false,
        choices: [...PRESETS].map((value) => ({ name: value, value })),
      },
    ],
  };
}

export function parseDiscordArtInteraction(interaction) {
  if (!interaction || interaction.type !== 2 || interaction.data?.name !== 'amora-arte') return null;
  const actorId = interaction.member?.user?.id || interaction.user?.id || null;
  if (!actorId || !/^\d{10,24}$/.test(String(actorId))) return null;
  if (!interaction.id || !interaction.token || !interaction.application_id) return null;
  const options = Array.isArray(interaction.data.options) ? interaction.data.options : [];
  const prompt = options.find((item) => item?.name === 'prompt')?.value;
  const preset = options.find((item) => item?.name === 'preset')?.value || 'sticker';
  if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 500 || !PRESETS.has(preset)) return null;
  return {
    actorId: String(actorId),
    applicationId: String(interaction.application_id),
    interactionId: String(interaction.id),
    interactionToken: String(interaction.token),
    prompt: prompt.trim(),
    preset,
  };
}

function imageBytes(response) {
  const encoded = response?.image_base64;
  if (typeof encoded !== 'string' || encoded.length > Math.ceil(MAX_ART_BYTES * 4 / 3) + 8) {
    throw new Error('ART_INVALID_RESPONSE');
  }
  const bytes = Buffer.from(encoded, 'base64');
  if (bytes.length < 32 || bytes.length > MAX_ART_BYTES) throw new Error('ART_INVALID_RESPONSE');
  if (response?.content_mime === 'image/png' && bytes.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex'))) {
    return { bytes, mime: 'image/png', extension: 'png' };
  }
  if (response?.content_mime === 'image/jpeg' && bytes[0] === 0xff && bytes[1] === 0xd8) {
    return { bytes, mime: 'image/jpeg', extension: 'jpg' };
  }
  if (response?.content_mime === 'image/webp' && bytes.toString('ascii', 0, 4) === 'RIFF' && bytes.toString('ascii', 8, 12) === 'WEBP') {
    return { bytes, mime: 'image/webp', extension: 'webp' };
  }
  throw new Error('ART_INVALID_RESPONSE');
}

export async function handleDiscordArtInteraction({
  command, core, rest, remoteProcessingAllowed = false, fetchImpl = fetch,
}) {
  const interactionPath = `/interactions/${encodeURIComponent(command.interactionId)}/${encodeURIComponent(command.interactionToken)}/callback`;
  const webhookPath = `/webhooks/${encodeURIComponent(command.applicationId)}/${encodeURIComponent(command.interactionToken)}/messages/@original`;
  // Never perform generation unless the Discord 3-second ACK succeeded.
  try {
    await rest.request(interactionPath, {
      method: 'POST', auth: false, maxAttempts: 1, timeoutMs: 1500,
      body: discordDeferredInteractionResponse({ ephemeral: true }),
    });
  } catch {
    return { status: 'ack_failed' };
  }
  const editText = async (message) => rest.request(webhookPath, {
    method: 'PATCH', auth: false, maxAttempts: 1, timeoutMs: 5000,
    body: discordInteractionEdit(message),
  });

  if (!remoteProcessingAllowed) {
    await editText('Geração remota desativada. Autorize o processamento remoto nas configurações.').catch(() => {});
    return { status: 'remote_processing_disabled' };
  }

  try {
    const result = await core.generateArt({
      prompt: command.prompt,
      preset: command.preset,
      remote_processing_allowed: true,
      discord_actor_id: command.actorId,
    });
    const file = imageBytes(result);
    const form = new FormData();
    form.set('payload_json', JSON.stringify({
      content: 'Arte gerada pela Amora (estilo experimental; identidade não certificada).',
      attachments: [{ id: 0, filename: `amora.${file.extension}` }],
      allowed_mentions: { parse: [] },
    }));
    form.set('files[0]', new Blob([file.bytes], { type: file.mime }), `amora.${file.extension}`);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const upload = await fetchImpl(`https://discord.com/api/v10${webhookPath}`, {
        method: 'PATCH',
        body: form,
        redirect: 'error',
        signal: controller.signal,
      });
      if (!upload.ok) throw new Error('ART_DISCORD_UPLOAD_FAILED');
    } finally {
      clearTimeout(timer);
    }
    return { status: 'sent' };
  } catch (error) {
    const errorCode = String(error?.payload?.detail || error?.code || 'ART_GENERATION_UNAVAILABLE');
    // Never echo provider bodies, arbitrary exception messages, prompts or secrets.
    const allowed = new Set([
      'ART_DISABLED', 'ART_ROLE_DENIED', 'ART_ACTOR_DENIED', 'ART_REMOTE_PROCESSING_NOT_ALLOWED',
      'ART_PROVIDER_UNCONFIGURED', 'ART_PROVIDER_RATE_LIMITED', 'ART_PROVIDER_AUTH_FAILED',
      'ART_PROVIDER_UNAVAILABLE', 'ART_PROVIDER_NETWORK_ERROR', 'ART_BUSY',
    ]);
    const visible = allowed.has(errorCode) ? errorCode : 'ART_GENERATION_UNAVAILABLE';
    await editText(`Não consegui gerar a imagem: ${visible}.`).catch(() => {});
    return { status: 'failed', code: visible };
  }
}
