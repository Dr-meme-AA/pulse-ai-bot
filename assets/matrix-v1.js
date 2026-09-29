/* Decorative effects only. No wallet, chain, biometric or analytics requests. */
(() => {
  'use strict';
  const canvas = document.getElementById('matrix-rain');
  const ctx = canvas?.getContext('2d', { alpha: true });
  if (!ctx) return;
  const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = matchMedia('(hover: hover) and (pointer: fine)');
  const toggle = document.getElementById('motion-toggle');
  const toggleText = toggle.querySelector('.motion-text');
  const hero = document.querySelector('.hero');
  const subject = document.querySelector('.identity-subject');
  const scrambleNodes = [...document.querySelectorAll('[data-scramble]')];
  let width = 0, height = 0, streams = [], raf = 0, last = 0, clock = 0;
  let userPaused = false, running = false, glyphTick = -1, lastScrambleTick = -1;
  let pointerX = 0, pointerY = 0, easedX = 0, easedY = 0;
  let resizeTimer;
  try { userPaused = sessionStorage.getItem('404-effects-paused') === '1'; } catch {}
  const random = (min, max) => min + Math.random() * (max - min);
  const canMove = () => !motionQuery.matches && !userPaused && !document.hidden;
  function fit() {
    width = document.documentElement.clientWidth;
    height = window.innerHeight;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const spacing = width < 700 ? 21 : 25;
    streams = Array.from({ length: Math.ceil(width / spacing) }, (_, index) => {
      const near = index % 3 !== 0;
      return {
        x: index * spacing + random(0, 6), y: random(30, height + 180),
        font: near ? 16 : 12, step: near ? 22 : 19,
        speed: random(65, 130) * (near ? 1 : .65),
        length: Math.floor(random(18, near ? 34 : 23)),
        alpha: near ? random(.7, 1) : random(.3, .52),
        rgb: index % 3 === 0 ? '178,111,255' : index % 5 === 0 ? '117,206,210' : '20,241,149',
        digits: Array.from({ length: 40 }, () => Math.random() > .5 ? '1' : '0')
      };
    });
    draw(0, 0);
  }
  function draw(dt, time) {
    ctx.clearRect(0, 0, width, height);
    const tick = Math.floor(time / 180);
    const mutate = tick !== glyphTick;
    glyphTick = tick;
    for (const stream of streams) {
      stream.y += stream.speed * dt;
      if (stream.y - stream.length * stream.step > height) stream.y = random(-100, -10);
      if (mutate) {
        const index = Math.floor(Math.random() * stream.digits.length);
        stream.digits[index] = stream.digits[index] === '1' ? '0' : '1';
      }
      ctx.font = stream.font + 'px ui-monospace, SFMono-Regular, Menlo, monospace';
      for (let trail = 0; trail < stream.length; trail++) {
        const y = stream.y - trail * stream.step;
        if (y < -20 || y > height + 20) continue;
        const fade = Math.pow(1 - trail / stream.length, 1.15);
        ctx.fillStyle = trail === 0
          ? 'rgba(203,255,233,' + stream.alpha + ')'
          : 'rgba(' + stream.rgb + ',' + (fade * stream.alpha * .88) + ')';
        ctx.fillText(stream.digits[trail], stream.x, y);
      }
    }
  }
  function restoreLabels() {
    scrambleNodes.forEach(node => { node.textContent = node.dataset.scramble; });
  }
  function scramble(time) {
    const tick = Math.floor(time / 85);
    if (tick === lastScrambleTick) return;
    lastScrambleTick = tick;
    const phase = time % 8500;
    scrambleNodes.forEach((node, index) => {
      const label = node.dataset.scramble;
      const local = phase - index * 130;
      if (local < 0 || local > 920) {
        if (node.textContent !== label) node.textContent = label;
        return;
      }
      const revealed = Math.floor((local / 920) * label.length);
      node.textContent = [...label].map((char, n) => n < revealed || char === ' ' ? char : Math.random() > .5 ? '1' : '0').join('');
    });
  }
  function frame(timestamp) {
    if (!canMove()) { running = false; raf = 0; return; }
    raf = requestAnimationFrame(frame);
    if (last && timestamp - last < 1000 / 30) return;
    const dt = last ? Math.min((timestamp - last) / 1000, .09) : 0;
    last = timestamp;
    clock += dt * 1000;
    draw(dt, clock);
    scramble(clock);
    if (finePointer.matches && (Math.abs(pointerX - easedX) > .03 || Math.abs(pointerY - easedY) > .03)) {
      easedX += (pointerX - easedX) * .09;
      easedY += (pointerY - easedY) * .09;
      subject.style.setProperty('--subject-x', easedX.toFixed(2) + 'px');
      subject.style.setProperty('--subject-y', easedY.toFixed(2) + 'px');
    }
  }
  function syncMotion() {
    const paused = userPaused || motionQuery.matches;
    document.body.classList.toggle('effects-paused', paused);
    document.body.classList.toggle('reduced-effects', motionQuery.matches);
    toggle.disabled = motionQuery.matches;
    toggleText.textContent = motionQuery.matches ? 'REDUCED FX' : userPaused ? 'PLAY FX' : 'PAUSE FX';
    toggle.setAttribute('aria-label', motionQuery.matches ? 'Visual motion reduced by device settings' : userPaused ? 'Play visual effects' : 'Pause visual effects');
    toggle.setAttribute('aria-pressed', String(paused));
    if (canMove() && !running) {
      running = true; last = 0; raf = requestAnimationFrame(frame);
    } else if (!canMove()) {
      cancelAnimationFrame(raf); raf = 0; running = false; last = 0;
      restoreLabels();
    }
  }
  toggle.addEventListener('click', () => {
    userPaused = !userPaused;
    try { sessionStorage.setItem('404-effects-paused', userPaused ? '1' : '0'); } catch {}
    syncMotion();
  });
  motionQuery.addEventListener('change', syncMotion);
  document.addEventListener('visibilitychange', syncMotion);
  window.addEventListener('pagehide', () => { cancelAnimationFrame(raf); running = false; }, { passive:true });
  window.addEventListener('pageshow', syncMotion, { passive:true });
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(fit, 120);
  }, { passive:true });
  hero.addEventListener('pointermove', event => {
    if (!canMove() || !finePointer.matches) return;
    const rect = hero.getBoundingClientRect();
    pointerX = ((event.clientX - rect.left) / rect.width - .5) * 12;
    pointerY = ((event.clientY - rect.top) / rect.height - .5) * 8;
  }, { passive:true });
  hero.addEventListener('pointerleave', () => { pointerX = 0; pointerY = 0; }, { passive:true });
  const revealNodes = document.querySelectorAll('.lore-grid > div, .signal-heading, .signal-stats, .contract');
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => { if (entry.isIntersecting) { entry.target.classList.add('is-visible'); observer.unobserve(entry.target); } });
  }, { threshold: .08 });
  revealNodes.forEach(node => { node.classList.add('reveal'); observer.observe(node); });
  document.body.classList.add('fx-ready');
  function readingProgress() {
    const distance = document.documentElement.scrollHeight - innerHeight;
    document.documentElement.style.setProperty('--reading-progress', distance > 0 ? Math.min(1, Math.max(0, scrollY / distance)) : 0);
  }
  window.addEventListener('scroll', readingProgress, { passive:true });
  fit(); readingProgress(); syncMotion();
})();
