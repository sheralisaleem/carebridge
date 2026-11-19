import { get, node } from './ui.js';
import { renderReply } from './assistant-markdown.js';

const latestThreshold = 48;
const thinkingDelay = 300;

export function mountAssistantChat(root) {
  const log = get('#chat-log', root);
  if (!log) return null;
  const scroll = get('#conversation-scroll', root);
  const intro = get('#chat-intro', log);
  const jump = get('#jump-latest', root);
  const thinking = get('#assistant-thinking', root);
  const feedback = get('#chat-feedback', root);
  const retry = get('#retry-message', root);
  const input = get('#message', root);
  const field = input.closest('.composer-field');
  let thinkingTimer;
  let retryText;

  function isNearLatest() {
    return scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop <= latestThreshold;
  }

  function updateJump() {
    if (scroll.clientHeight) jump.hidden = isNearLatest();
  }

  function showLatest() {
    scroll.scrollTop = scroll.scrollHeight;
    updateJump();
  }

  function update(render) {
    const follow = isNearLatest();
    render();
    if (follow) showLatest();
    else updateJump();
  }

  function setDraft(value, focus = false) {
    update(() => {
      input.value = value;
      field.dataset.draft = value;
    });
    if (focus) {
      input.focus({ preventScroll: true });
      input.setSelectionRange(value.length, value.length);
    }
  }

  function copyButton(text, label) {
    const button = node('button', 'Copy', 'chat-action');
    button.type = 'button';
    button.setAttribute('aria-label', label);
    let reset;
    button.addEventListener('click', async () => {
      clearTimeout(reset);
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = 'Copied';
        button.dataset.copied = '';
        feedback.textContent = 'Copied to clipboard.';
      } catch {
        button.textContent = 'Copy unavailable';
        feedback.textContent = 'Clipboard unavailable. Select the text to copy it manually.';
      }
      reset = setTimeout(() => {
        button.textContent = 'Copy';
        delete button.dataset.copied;
      }, 1600);
    });
    return button;
  }

  function append(author, message) {
    intro.hidden = true;
    const patient = author === 'patient';
    const entry = node('article', null, `chat-message ${author}`);
    const header = node('div', null, 'chat-message-header');
    const actions = node('div', null, 'chat-message-actions');
    actions.append(copyButton(message, `Copy ${patient ? 'your message' : 'assistant reply'}`));
    if (patient) {
      const edit = node('button', 'Edit', 'chat-action');
      edit.type = 'button';
      edit.setAttribute('aria-label', 'Edit message in composer');
      edit.addEventListener('click', () => setDraft(message, true));
      actions.append(edit);
    }
    header.append(node('span', patient ? 'You' : 'CareBridge', 'chat-author'), actions);
    entry.append(header, patient ? node('p', message, 'message-text') : renderReply(message, copyButton));
    log.append(entry);
  }

  function setThinking(active) {
    clearTimeout(thinkingTimer);
    if (active) {
      if (thinking.hidden) thinkingTimer = setTimeout(() => update(() => { thinking.hidden = false; }), thinkingDelay);
    } else if (!thinking.hidden) {
      update(() => { thinking.hidden = true; });
    }
  }

  jump.addEventListener('click', () => {
    scroll.focus({ preventScroll: true });
    showLatest();
  });
  retry.addEventListener('click', () => {
    if (input.value.trim()) {
      feedback.textContent = 'Clear the current draft before restoring the failed request.';
      input.focus({ preventScroll: true });
      return;
    }
    const text = retryText;
    showRetry('');
    setDraft(text, true);
  });

  function showRetry(text) {
    retryText = text;
    update(() => { retry.hidden = !text; });
  }
  scroll.addEventListener('scroll', updateJump, { passive: true });
  const resize = new ResizeObserver(updateJump);
  resize.observe(scroll);
  input.addEventListener('input', () => update(() => { field.dataset.draft = input.value; }));

  return {
    append, setDraft, setThinking, update, showLatest, showRetry,
    reset() {
      setThinking(false);
      showRetry('');
      log.replaceChildren(intro);
      intro.hidden = false;
      feedback.textContent = '';
      setDraft('', true);
      showLatest();
    },
  };
}
