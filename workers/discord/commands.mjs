export const AMORA_COMMAND_NAME = 'amora';

const ACTION_CHOICES = [
  { name: 'Status', value: 'status' },
  { name: 'Conectar', value: 'connect' },
  { name: 'Desconectar', value: 'disconnect' },
  { name: 'Checar', value: 'check' },
];
const ACTION_VALUES = new Set(ACTION_CHOICES.map((item) => item.value));

export function discordAmoraCommandDefinition() {
  return {
    name: AMORA_COMMAND_NAME,
    type: 1,
    description: 'Controle de presença textual da Amora no Discord',
    options: [
      {
        type: 3,
        name: 'acao',
        description: 'Ação de presença',
        required: true,
        choices: ACTION_CHOICES.map((item) => ({ ...item })),
      },
    ],
  };
}

export function parseDiscordAmoraInteraction(interaction) {
  if (!interaction || interaction.type !== 2 || interaction.data?.name !== AMORA_COMMAND_NAME) return null;
  const actorId = interaction.member?.user?.id || interaction.user?.id || null;
  if (!actorId) return null;
  const option = Array.isArray(interaction.data?.options)
    ? interaction.data.options.find((item) => item?.name === 'acao')
    : null;
  const action = String(option?.value || '');
  if (!ACTION_VALUES.has(action)) return null;
  return {
    action,
    actorId: String(actorId),
    interactionId: String(interaction.id || ''),
    interactionToken: String(interaction.token || ''),
    guildId: interaction.guild_id ? String(interaction.guild_id) : null,
    channelId: interaction.channel_id ? String(interaction.channel_id) : null,
  };
}

export function discordInteractionResponse(content, { ephemeral = true } = {}) {
  return {
    type: 4,
    data: {
      content: String(content || '').slice(0, 2000),
      allowed_mentions: { parse: [] },
      ...(ephemeral ? { flags: 64 } : {}),
    },
  };
}
