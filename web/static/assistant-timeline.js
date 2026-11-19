const stepPace = 460;

export function createAssistantTimeline(list, scroll) {
  const pending = [];
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let timer;
  let previousStep;
  let following = true;

  function showLatest() {
    following = true;
    scroll.scrollTop = scroll.scrollHeight;
  }

  function reveal({ row, step, boundary }) {
    if (boundary) previousStep = undefined;
    if (step) {
      if (previousStep) previousStep.dataset.advanced = '';
      previousStep = row;
    }
    list.append(row);
  }

  function flush() {
    clearTimeout(timer);
    timer = undefined;
    for (const entry of pending.splice(0)) reveal(entry);
  }

  function advance() {
    if (reducedMotion.matches || document.hidden || !scroll.clientHeight) {
      flush();
      return;
    }
    while (pending.length) {
      const next = pending.shift();
      reveal(next);
      if (next.step) {
        timer = setTimeout(() => { timer = undefined; advance(); }, stepPace);
        return;
      }
    }
  }

  scroll.addEventListener('scroll', () => {
    if (scroll.clientHeight) following = scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop < 24;
  }, { passive: true });
  const resize = new ResizeObserver(() => { if (following) showLatest(); });
  resize.observe(list);
  resize.observe(scroll);
  reducedMotion.addEventListener('change', () => { if (reducedMotion.matches) flush(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) flush(); });

  return {
    append(row, { step = false, boundary = false } = {}) {
      pending.push({ row, step, boundary });
      if (timer === undefined) advance();
    },
    showLatest,
    reset() {
      clearTimeout(timer);
      timer = undefined;
      pending.length = 0;
      previousStep = undefined;
      list.replaceChildren();
      showLatest();
    },
  };
}
