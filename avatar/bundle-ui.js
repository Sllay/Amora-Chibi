// Portable Avatar bundle controls. Browser owns file selection/download only;
// Core owns bundle construction, validation and persistence.
// @ts-check

const exportButton = /** @type {HTMLButtonElement | null} */ (document.querySelector('#exportBundleBtn'));
const importButton = /** @type {HTMLButtonElement | null} */ (document.querySelector('#importProjectBtn'));
const projectInput = /** @type {HTMLInputElement | null} */ (document.querySelector('#projectInput'));
const toast = document.querySelector('#toast');

function notify(text) {
  if (!toast) return;
  toast.textContent = text;
  toast.classList.add('show');
  clearTimeout(toast._bundleTimer);
  toast._bundleTimer = setTimeout(() => toast.classList.remove('show'), 2600);
}

function avatarToken() {
  return sessionStorage.getItem('amora.avatar.token') || '';
}

async function responseError(response) {
  let detail = `${response.status}`;
  try { detail = (await response.json()).detail || detail; } catch (_) {}
  return detail;
}

function looksLikeAvatarProject(value) {
  return Boolean(
    value &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    value.canvas &&
    typeof value.canvas === 'object' &&
    Array.isArray(value.assets) &&
    Array.isArray(value.parts)
  );
}

function patchLegacyProjectCopy() {
  for (const node of document.querySelectorAll('.section small')) {
    if ((node.textContent || '').includes('JSON é só a serialização automática do Studio')) {
      node.textContent = 'Backup portátil usa ZIP com manifest, projeto completo e assets/texturas. JSON antigo continua aceito apenas para restauração legada.';
    }
  }
}

new MutationObserver(patchLegacyProjectCopy).observe(document.body, { childList: true, subtree: true });
patchLegacyProjectCopy();

exportButton?.addEventListener('click', async () => {
  const token = avatarToken();
  if (!token) return notify('Pareie o Avatar Studio antes de exportar');
  exportButton.disabled = true;
  try {
    const response = await fetch('/v1/avatar/bundle', {
      headers: { 'Authorization': `Bearer ${token}` },
      cache: 'no-store',
    });
    if (!response.ok) throw new Error(await responseError(response));
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'amora_avatar.zip';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify('ZIP do Avatar exportado com projeto + texturas');
  } catch (error) {
    notify(`Exportar ZIP: ${error?.message || 'falha ao gerar backup'}`);
  } finally {
    exportButton.disabled = false;
  }
});

importButton?.addEventListener('click', () => projectInput?.click());

projectInput?.addEventListener('change', async () => {
  const file = projectInput.files?.[0];
  projectInput.value = '';
  if (!file) return;
  const token = avatarToken();
  if (!token) return notify('Pareie o Avatar Studio antes de importar');

  importButton.disabled = true;
  try {
    const legacyJson = file.name.toLowerCase().endsWith('.json') || file.type === 'application/json';
    let response;
    if (legacyJson) {
      const imported = JSON.parse(await file.text());
      if (!looksLikeAvatarProject(imported)) {
        throw new Error('arquivo não parece um projeto exportado pelo Avatar Studio');
      }
      response = await fetch('/v1/avatar/project', {
        method: 'PUT',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(imported),
      });
    } else {
      response = await fetch('/v1/avatar/bundle', {
        method: 'PUT',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/zip',
        },
        body: await file.arrayBuffer(),
      });
    }

    if (!response.ok) throw new Error(await responseError(response));
    notify(legacyJson ? 'JSON legado restaurado. Recarregando…' : 'ZIP restaurado com projeto + texturas. Recarregando…');
    setTimeout(() => location.reload(), 300);
  } catch (error) {
    notify(`Importar backup: ${error?.message || 'falha ao ler o arquivo'}`);
  } finally {
    importButton.disabled = false;
  }
});
