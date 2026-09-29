/* Fictional story interactions. Commands never execute code or send requests. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const hero = document.querySelector('.hero');
  const aliasNode = $('alias-value');
  const sessionNode = $('session-value');
  const output = $('terminal-output');
  const input = $('terminal-input');
  const unmask = $('unmask-subject');
  const attempt = $('attempt-state');
  const toast = $('status-toast');
  const fileTabs = [...document.querySelectorAll('[data-file]')];
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let alias = aliasNode.textContent;
  let session = sessionNode.textContent;
  let currentFile = 'identity';
  let maskTimer, toastTimer, unmaskTimer;
  let traceTimers = [];
  const hex = length => Array.from({ length }, () => Math.floor(Math.random() * 16).toString(16)).join('').toUpperCase();
  const aliases = ['ghost', 'null', 'unknown', 'void', 'redacted'];
  const files = {
    identity: () => JSON.stringify({ subject: '404', alias, name: '[REDACTED]', face: null, origin: '[REDACTED]', status: 'IDENTITY_NOT_FOUND', network: 'SOLANA' }, null, 2),
    fingerprint: () => '404:// FICTIONAL TRACE LOG\n\n[00] Fragment detected: ' + session + '\n[01] Searching for a name ...\n[02] Name field overwritten.\n[03] Searching for a face ...\n[04] Face field: NULL.\n[05] Matching identity ... 0 results.\n\nRESULT: THE SIGNAL HAS NO SIGNATURE.',
    memory: () => 'MEMORY FRAGMENT / 0x404\n\n“I remember the questions.\n I no longer remember the name.”\n\nA hood. A number. An empty field.\nSomeone was here.\nNo one can say who.\n\nEND OF RECOVERABLE FRAGMENT_'
  };
  const paths = { identity: 'identity.json', fingerprint: 'fingerprint.log', memory: 'memory.dump' };
  function renderFile() {
    $('file-path').textContent = '/unknown/' + paths[currentFile];
    $('file-content').textContent = files[currentFile]();
  }
  function announce(message) {
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.add('visible');
    toastTimer = setTimeout(() => { toast.classList.remove('visible'); }, 2800);
  }
  function flashMask() {
    if (reducedMotion.matches || document.body.classList.contains('effects-paused')) return;
    clearTimeout(maskTimer);
    hero.classList.add('masking');
    maskTimer = setTimeout(() => hero.classList.remove('masking'), 900);
  }
  function rotate(manual = false) {
    const previous = alias;
    do { alias = aliases[Math.floor(Math.random() * aliases.length)] + '_0x' + hex(3).toLowerCase(); } while (alias === previous);
    session = hex(4) + ':' + hex(4);
    aliasNode.textContent = alias;
    sessionNode.textContent = session;
    renderFile();
    if (manual) { flashMask(); announce('ALIAS REPLACED // IDENTITY STILL NOT FOUND'); }
  }
  $('rotate-identity').addEventListener('click', () => rotate(true));
  const rotateTimer = setInterval(() => {
    if (!document.hidden && !reducedMotion.matches && !document.body.classList.contains('effects-paused')) rotate();
  }, 11000);
  unmask.addEventListener('click', () => {
    unmask.disabled = true;
    unmask.textContent = 'RESOLVING SUBJECT...';
    attempt.textContent = 'SEARCHING THE FICTIONAL DOSSIER...';
    flashMask();
    unmaskTimer = setTimeout(() => {
      attempt.textContent = 'ERROR 404 // IDENTITY NOT FOUND.';
      unmask.textContent = 'TRY AGAIN ↗';
      unmask.disabled = false;
    }, reducedMotion.matches ? 0 : 1100);
  });
  function write(text, kind = '') {
    const line = document.createElement('div');
    line.className = 'terminal-line' + (kind ? ' ' + kind : '');
    line.textContent = text;
    output.append(line);
    while (output.children.length > 40) output.firstElementChild.remove();
    output.scrollTop = output.scrollHeight;
  }
  function cancelTrace() { traceTimers.forEach(clearTimeout); traceTimers = []; }
  function run(raw) {
    const command = raw.trim().toLowerCase();
    if (!command) return;
    cancelTrace();
    if (command === 'clear') { output.replaceChildren(); write('404:// cleared. The absence remains.', 'muted'); return; }
    write('ghost@404:~$ ' + raw.trim(), 'command');
    switch (command) {
      case 'help':
        write('whoami   Read the current identity fragment.\nmask     Replace the fictional alias.\ntrace    Follow the signal through the story.\nlore     Read the manifesto.\nclear    Clear this terminal.', 'success');
        break;
      case 'whoami':
        write('ALIAS    ' + alias + '\nSESSION  ' + session + '\nIDENTITY NOT FOUND', 'success');
        break;
      case 'mask':
        rotate(true);
        write('MASK REGENERATED // ' + alias + '\nThe name changed. The absence did not.', 'success');
        break;
      case 'trace': {
        const lines = ['[01] Following a fictional signal → VOID', '[02] VOID → SHADOW → [REDACTED]', '[404] Origin: UNKNOWN. No identity recovered.'];
        if (reducedMotion.matches) lines.forEach((line, i) => write(line, i === 2 ? 'warn' : 'muted'));
        else lines.forEach((line, i) => traceTimers.push(setTimeout(() => write(line, i === 2 ? 'warn' : 'muted'), i * 500 + 150)));
        break;
      }
      case 'lore':
        write('Opening /404/manifesto...', 'success');
        $('manifesto').scrollIntoView({ behavior: reducedMotion.matches ? 'instant' : 'smooth' });
        break;
      default:
        write('Command not found. Type “help” for story commands.', 'warn');
    }
  }
  $('terminal-form').addEventListener('submit', event => { event.preventDefault(); const value = input.value; input.value = ''; run(value); });
  document.querySelectorAll('[data-command]').forEach(button => button.addEventListener('click', () => run(button.dataset.command)));
  function selectFile(tab, focus = false) {
    currentFile = tab.dataset.file;
    fileTabs.forEach(item => { const selected = item === tab; item.setAttribute('aria-selected', String(selected)); item.tabIndex = selected ? 0 : -1; });
    $('file-panel').setAttribute('aria-labelledby', tab.id);
    renderFile();
    if (focus) tab.focus();
  }
  fileTabs.forEach((tab, index) => {
    tab.addEventListener('click', () => selectFile(tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % fileTabs.length;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + fileTabs.length - 1) % fileTabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = fileTabs.length - 1;
      if (next !== undefined) { event.preventDefault(); selectFile(fileTabs[next], true); }
    });
  });
  window.addEventListener('pagehide', event => {
    cancelTrace();
    clearTimeout(maskTimer); clearTimeout(toastTimer); clearTimeout(unmaskTimer);
    hero.classList.remove('masking'); toast.classList.remove('visible');
    unmask.disabled = false; unmask.textContent = 'UNMASK SUBJECT ↗';
    if (!event.persisted) clearInterval(rotateTimer);
  });
})();
