import { StickerMediaError, fetchDiscordStickerMedia } from './sticker-media.mjs';

const DEFAULT_LEASE_SECONDS = 120;
const DEFAULT_MAX_DRAIN = 2;

function safeLog(code, detail = '') {
  const suffix = detail ? ` ${String(detail).slice(0, 180)}` : '';
  process.stderr.write(`[amora-discord-visual] ${code}${suffix}\n`);
}

function errorLabel(error) {
  if (error?.code) return String(error.code);
  if (error?.status) return `HTTP_${error.status}`;
  return String(error?.name || 'Error');
}

function retrySecondsForMediaError(code) {
  switch (String(code || '')) {
    case 'STICKER_FETCH_TIMEOUT':
    case 'STICKER_FETCH_FAILED':
    case 'STICKER_FETCH_HTTP_ERROR':
      return 120;
    case 'STICKER_TOO_LARGE':
    case 'INVALID_STICKER_CONTENT':
    case 'UNEXPECTED_STICKER_MIME':
    case 'UNSUPPORTED_STICKER_FORMAT':
    case 'INVALID_STICKER_ID':
      return 21_600;
    default:
      return 300;
  }
}

function normalizeFormatType(work) {
  const value = Number(work?.metadata?.format_type);
  return Number.isInteger(value) ? value : null;
}

export class DiscordVisualEnrichmentWorker {
  constructor({
    core,
    fetchMedia = fetchDiscordStickerMedia,
    leaseSeconds = DEFAULT_LEASE_SECONDS,
    maxDrain = DEFAULT_MAX_DRAIN,
  }) {
    if (!core) throw new Error('visual worker requires a Core client');
    this.core = core;
    this.fetchMedia = fetchMedia;
    this.leaseSeconds = Math.max(5, Math.min(900, Number(leaseSeconds) || DEFAULT_LEASE_SECONDS));
    this.maxDrain = Math.max(1, Math.min(8, Number(maxDrain) || DEFAULT_MAX_DRAIN));
    this.processing = false;
    this.stopped = false;
  }

  stop() {
    this.stopped = true;
  }

  async drain() {
    if (this.processing || this.stopped || typeof this.core.claimVisual !== 'function') return 0;
    this.processing = true;
    let processed = 0;
    try {
      for (let index = 0; index < this.maxDrain && !this.stopped; index += 1) {
        const claimed = await this.core.claimVisual(1, this.leaseSeconds);
        const work = claimed?.work?.[0] || null;
        if (!work) break;
        processed += 1;

        if (work.platform !== 'discord' || work.kind !== 'sticker') {
          await this._deferDeterministic(work, 'DISCORD_UNSUPPORTED_VISUAL_KIND', 21_600);
          continue;
        }

        const formatType = normalizeFormatType(work);
        if (formatType === null) {
          await this._deferDeterministic(work, 'DISCORD_STICKER_FORMAT_UNKNOWN', 21_600);
          continue;
        }

        let media;
        try {
          media = await this.fetchMedia({
            nativeId: work.native_id,
            formatType,
          });
        } catch (error) {
          if (error instanceof StickerMediaError) {
            const code = `DISCORD_${String(error.code || 'STICKER_FETCH_FAILED')}`.slice(0, 96);
            await this._deferDeterministic(work, code, retrySecondsForMediaError(error.code));
            safeLog('MEDIA_DEFERRED', code);
            continue;
          }
          safeLog('MEDIA_OUTCOME_UNKNOWN', errorLabel(error));
          break;
        }

        const contentBase64 = Buffer.from(media.bytes).toString('base64');
        try {
          const result = await this.core.submitVisualContent(work, {
            contentMime: media.mime,
            contentBase64,
          });
          safeLog('RESULT', String(result?.status || 'unknown'));
        } catch (error) {
          const status = Number(error?.status || 0);
          if (status === 409) {
            safeLog('STALE_CLAIM');
            continue;
          }
          if (status === 422) {
            await this._deferDeterministic(work, 'CORE_VISUAL_CONTENT_REJECTED', 21_600);
            continue;
          }
          if (status === 503) {
            // The Core visual service classifies provider failures and defers the
            // live claim before returning 503. A second defer here would race the
            // next claimant and blur the real failure source.
            safeLog('CORE_ALREADY_DEFERRED', errorLabel(error));
            continue;
          }

          // Network/timeout can be ambiguous: the Core may have already committed
          // the analysis before the HTTP response was lost. Never convert that
          // ambiguity into a new state mutation. Lease expiry + idempotent CAS
          // recover safely on the next pass.
          safeLog('SUBMIT_OUTCOME_UNKNOWN', errorLabel(error));
          break;
        }
      }
    } finally {
      this.processing = false;
    }
    return processed;
  }

  async _deferDeterministic(work, errorCode, retryAfterSeconds) {
    try {
      await this.core.deferVisual(work, {
        errorCode,
        retryAfterSeconds,
      });
    } catch (error) {
      if (Number(error?.status || 0) !== 409) {
        safeLog('DEFER_FAILED', errorLabel(error));
      }
    }
  }
}
