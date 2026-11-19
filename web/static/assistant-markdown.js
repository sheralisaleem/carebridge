import { node } from './ui.js';

// Reply markup becomes DOM nodes. Raw HTML and image URLs are never rendered.
function appendInline(parent, text) {
  const inline = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|!?\[[^\]\n]+\]\([^\s)]+\))/g;
  let cursor = 0;
  for (const match of text.matchAll(inline)) {
    const value = match[0];
    parent.append(text.slice(cursor, match.index));
    cursor = match.index + value.length;
    if (value.startsWith('`')) parent.append(node('code', value.slice(1, -1)));
    else if (value.startsWith('**')) parent.append(node('strong', value.slice(2, -2)));
    else if (value.startsWith('*')) parent.append(node('em', value.slice(1, -1)));
    else {
      const [, image, label, href] = /^(!?)\[([^\]]+)\]\(([^)]+)\)$/.exec(value);
      let url;
      try { url = new URL(href, location.origin); } catch { /* Invalid URL. */ }
      if (!image && url && (url.protocol === 'https:' || url.protocol === 'http:')) {
        const link = node('a', label);
        link.href = url.href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        parent.append(link);
      } else parent.append(label);
    }
  }
  parent.append(text.slice(cursor));
}

function appendTextBlocks(body, text) {
  for (const block of text.trim().split(/\n[ \t]*\n/)) {
    if (!block) continue;
    const lines = block.split('\n');
    const citation = /^Source: ([\w.-]+\.md) · (.+)$/.exec(lines.at(-1));
    if (citation) {
      const [, file, section] = citation;
      const excerpt = node('p', null, 'message-text');
      appendInline(excerpt, lines.slice(0, -1).join('\n'));
      const chip = node('a', section, 'source-chip');
      chip.href = `/clinic#policy-${encodeURIComponent(file)}`;
      chip.setAttribute('aria-label', `Read clinic policy: ${section}`);
      body.append(excerpt, chip);
      continue;
    }
    const list = /^(?:([-+*])|(\d+)[.)])\s+/;
    if (lines.every(line => list.test(line))) {
      const first = list.exec(lines[0]);
      const group = node(first[2] ? 'ol' : 'ul');
      if (first[2]) group.start = Number(first[2]);
      for (const line of lines) {
        const item = node('li');
        appendInline(item, line.replace(list, ''));
        group.append(item);
      }
      body.append(group);
      continue;
    }
    const heading = /^#{1,6}\s+([^\n]+)$/.exec(block);
    const element = node(heading ? 'h4' : 'p', null, 'message-text');
    appendInline(element, heading ? heading[1] : block);
    body.append(element);
  }
}

export function renderReply(text, copyButton) {
  const body = node('div', null, 'reply-content');
  const fences = /^```([\w+-]{0,24})[ \t]*\r?\n([\s\S]*?)\r?\n?^```[ \t]*(?:\r?\n|$)/gm;
  let cursor = 0;
  for (const match of text.matchAll(fences)) {
    const [block, language, source] = match;
    appendTextBlocks(body, text.slice(cursor, match.index));
    cursor = match.index + block.length;
    const panel = node('div', null, 'reply-code');
    const header = node('div', null, 'reply-code-header');
    header.append(node('span', language || 'Text'), copyButton(source.trimEnd(), 'Copy code'));
    const pre = node('pre');
    pre.tabIndex = 0;
    pre.setAttribute('aria-label', `${language || 'Text'} code`);
    const code = node('code', source.trimEnd());
    pre.append(code);
    panel.append(header, pre);
    body.append(panel);
    if (language) import('./assistant-code.js')
      .then(({ highlightCode }) => highlightCode(code, language))
      .catch(() => { /* Plain code is already displayed. */ });
  }
  appendTextBlocks(body, text.slice(cursor));
  return body;
}
