import { node } from './ui.js';

const keywords = {
  python: /^(?:async|await|def|class|from|import|return|if|elif|else|for|in|while|with|as|try|except|raise|True|False|None|yield|pass|and|or|not)$/,
  javascript: /^(?:async|await|function|let|const|var|return|if|else|for|of|while|try|catch|throw|new|import|export|true|false|null|class|this)$/,
  json: /^(?:true|false|null)$/,
};
const aliases = { py: 'python', js: 'javascript', ts: 'javascript', typescript: 'javascript' };

export function highlightCode(code, label) {
  const language = label.toLowerCase();
  const words = keywords[aliases[language] || language];
  if (!(words instanceof RegExp)) return;
  const source = code.textContent;
  const tokens = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(#.*|\/\/.*)|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_]\w*\b)/g;
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  for (const match of source.matchAll(tokens)) {
    const [text, string, comment, number, word] = match;
    fragment.append(source.slice(cursor, match.index));
    const kind = string ? 'string' : comment ? 'comment' : number ? 'number' : words.test(word) ? 'keyword' : '';
    fragment.append(kind ? node('span', text, `code-${kind}`) : text);
    cursor = match.index + text.length;
  }
  fragment.append(source.slice(cursor));
  if (code.isConnected) code.replaceChildren(fragment);
}
