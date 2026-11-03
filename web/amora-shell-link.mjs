export function installAmoraShellLink() {
  const consoleView = document.getElementById('consoleView');
  if (!consoleView || document.getElementById('openAmoraShell')) return;
  const actions = consoleView.querySelector('.admin-toolbar .toolbar-actions');
  if (!actions) return;

  const link = document.createElement('a');
  link.id = 'openAmoraShell';
  link.className = 'ghost';
  link.href = './amora-shell.html';
  link.textContent = 'Amora Shell';
  link.setAttribute('aria-label', 'Abrir Amora Shell administrativo');
  actions.prepend(link);
}
