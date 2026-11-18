import { node, get, iconNode } from './ui.js';
import { stateLabel } from './agent-activity.js';

const pythonTokens = /("[^"]*"|'[^']*'|#[^\n]*|\b(?:async|await|def|return|if|else|for|in|try|except|raise|True|False|None|not|or|and)\b)/g;

function renderCodeLine(line, number) {
  const item = node('span', null, 'source-line');
  item.dataset.line = number;
  for (const [index, part] of line.split(pythonTokens).entries()) {
    if (index % 2 === 0) item.append(part);
    else item.append(node('span', part, part.startsWith('#') ? 'code-comment'
      : ['"', "'"].includes(part[0]) ? 'code-string' : 'code-keyword'));
  }
  if (!line) item.append(' ');
  return item;
}

export function mountAgentInspector() {
  const dialog = get('#agent-dialog');
  if (!dialog) return null;
  const catalog = JSON.parse(get('#agent-catalog').dataset.catalog);
  const title = get('#agent-dialog-title');
  const category = get('#inspector-category');
  const icon = get('#inspector-icon');
  const description = get('#inspector-description');
  const breakdown = get('#metric-breakdown');
  const agentSection = get('#inspector-agent');
  const metricSection = get('#inspector-breakdown');
  const backButton = get('#back-to-metric');
  const close = get('#close-agent-dialog');
  let fileReference = '';
  let previousMetric = null;
  let returnFocus = null;

  function open() {
    if (!dialog.open) {
      returnFocus = document.activeElement;
      dialog.showModal();
    }
    close.focus();
  }

  function stat(label, value, key, selected) {
    const entry = node('div', null, key === selected ? 'stat-highlight' : '');
    entry.append(node('dt', label), node('dd', value));
    return entry;
  }

  function showAgent(agent, selected = '') {
    const role = catalog[agent.role];
    if (!role) return;
    if (!dialog.open) previousMetric = null;
    icon.replaceChildren(iconNode(role.icon));
    category.textContent = role.title === agent.name ? 'Agent details' : role.title;
    title.textContent = agent.name;
    description.textContent = role.explanation;
    const stats = get('#inspector-stats');
    stats.replaceChildren(
      stat('Status', stateLabel(agent.state, agent.role), 'state', selected),
      stat('Actions', agent.actions ?? 0, 'actions', selected),
      stat('Messages', agent.messages ?? 0, 'messages', selected),
    );
    const latest = get('#inspector-latest');
    latest.hidden = !agent.last;
    get('span', latest).textContent = agent.last || '';
    fileReference = `${role.path}:${role.line}`;
    get('#inspector-path').textContent = fileReference;
    get('#inspector-symbol').textContent = role.symbol;
    const code = get('#inspector-code');
    code.replaceChildren();
    role.code.split('\n').forEach((line, index) => code.append(renderCodeLine(line, role.line + index)));
    get('#copy-agent-status').textContent = '';
    metricSection.hidden = true;
    agentSection.hidden = false;
    backButton.hidden = !previousMetric;
    open();
  }

  function showMetric(metric) {
    previousMetric = metric;
    icon.replaceChildren(iconNode(metric.icon));
    category.textContent = 'Clinic activity';
    title.textContent = `${metric.title} · ${metric.value}`;
    description.textContent = metric.description;
    breakdown.replaceChildren();
    for (const item of metric.items) {
      const row = node('li');
      if (item.agent) {
        const button = node('button', null, 'breakdown-button');
        button.type = 'button';
        button.append(node('span', item.label), node('strong', item.value));
        button.addEventListener('click', () => showAgent(item.agent, metric.key));
        row.append(button);
      } else {
        row.append(node('span', item.label), node('strong', item.value));
      }
      breakdown.append(row);
    }
    if (!metric.items.length) breakdown.append(node('li', 'No activity recorded yet.', 'secondary'));
    agentSection.hidden = true;
    metricSection.hidden = false;
    backButton.hidden = true;
    open();
  }

  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener('close', () => {
    if (returnFocus?.isConnected) returnFocus.focus();
    previousMetric = null;
  });
  backButton.addEventListener('click', () => showMetric(previousMetric));
  get('#copy-agent-path').addEventListener('click', async () => {
    const feedback = get('#copy-agent-status');
    try {
      await navigator.clipboard.writeText(fileReference);
      feedback.textContent = 'Path copied.';
    } catch {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(get('#inspector-path'));
      selection.removeAllRanges();
      selection.addRange(range);
      feedback.textContent = 'Path selected. Press Ctrl+C to copy it.';
    }
  });

  return { catalog, showAgent, showMetric, close: () => { if (dialog.open) dialog.close(); } };
}
