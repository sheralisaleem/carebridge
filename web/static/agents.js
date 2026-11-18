import { ACTIVITY_LIMIT, dashboardSnapshot, stateLabel } from './agent-activity.js';
import { activityRow, agentName, all, get, iconNode, node, readActivity } from './ui.js';

export function mountAgentsPage(root, inspector) {
  let overview = JSON.parse(root.dataset.overview);
  let events = readActivity();
  let model;
  let filter = 'all';
  let pending = false;
  const roster = get('#agent-roster', root);
  const cardTemplate = get('#agent-card-template', root);
  const cards = new Map();
  const feed = get('#agent-page-activity', root);
  const isCore = agent => !['doctor', 'patient'].includes(agent.role);
  const matches = agent => filter === 'all' || (filter === 'core' ? isCore(agent) : agent.role === filter);
  const nameOf = id => model.rows.get(id)?.name || agentName(id);

  function card(agent) {
    if (cards.has(agent.id)) return cards.get(agent.id);
    const item = cardTemplate.content.firstElementChild.cloneNode(true);
    item.dataset.agent = agent.id;
    item.dataset.role = agent.role;
    const info = inspector.catalog[agent.role];
    get('.agent-card-icon', item).append(iconNode(info.icon));
    get('.agent-card-name strong', item).textContent = agent.name;
    get('[data-agent-role-label]', item).textContent = agent.detail || (isCore(agent) ? 'Core team' : info.title);
    get('[data-agent-summary]', item).textContent = info.summary;
    get('.agent-card-open', item).addEventListener('click', () => inspector.showAgent(model.rows.get(agent.id)));
    for (const button of all('[data-agent-metric]', item)) {
      button.addEventListener('click', () => inspector.showAgent(model.rows.get(agent.id), button.dataset.agentMetric));
    }
    cards.set(agent.id, item);
    roster.append(item);
    return item;
  }

  function repaintCounts() {
    model = dashboardSnapshot(overview, events);
    for (const counter of all('[data-total]', root)) {
      const number = String(model.totals[counter.dataset.total]);
      if (counter.textContent !== number) counter.textContent = number;
    }
    for (const agent of model.agents) {
      const item = card(agent);
      item.hidden = !matches(agent);
      item.dataset.state = agent.state;
      const status = get('[data-agent-status]', item);
      status.textContent = stateLabel(agent.state, agent.role);
      status.title = agent.last;
      for (const button of all('[data-agent-metric]', item)) {
        const metric = button.dataset.agentMetric;
        get('strong', button).textContent = agent[metric];
        button.setAttribute('aria-label', `${agent[metric]} ${metric} for ${agent.name}`);
      }
    }
    for (const [id, item] of cards) {
      if (!model.rows.has(id)) {
        item.remove();
        cards.delete(id);
      }
    }
    get('#roster-empty', root).hidden = model.agents.some(matches);
  }

  function paintHandoff(event, animate = false) {
    get('#flow-from', root).textContent = nameOf(event.from);
    get('#flow-to', root).textContent = nameOf(event.to);
    get('#flow-label', root).textContent = event.performative.toLowerCase().replace(/^./, c => c.toUpperCase());
    if (animate) {
      const line = get('.flow-travel', root);
      line.classList.remove('active');
      requestAnimationFrame(() => requestAnimationFrame(() => line.classList.add('active')));
    }
  }

  function paintActivity(event, animate = false) {
    if (event.kind === 'agent_message') paintHandoff(event, animate);
    if (event.kind === 'replan' || (event.kind === 'agent_status' && event.state !== 'working')) {
      const row = activityRow(event);
      feed.prepend(row);
      if (feed.children.length > 5) feed.lastChild.remove();
    }
    if (['agent_status', 'agent_message', 'replan'].includes(event.kind)) {
      get('#agent-feed-empty', root).hidden = true;
    }
  }

  function paintPublications() {
    const list = get('#shared-agent-log', root);
    get('#shared-agent-activity', root).hidden = !overview.publications.length;
    list.replaceChildren(...overview.publications.slice().reverse().map(event => {
      const item = node('li');
      item.append(node('strong', nameOf(event.agent)), node('span',
        (event.event === 'availability_updated' ? 'Published availability' : 'Reported doctor leave')
        + (event.date ? ` · ${event.date}` : '')));
      return item;
    }));
  }

  const metrics = {
    agents: ['Agents', 'flow', 'Every agent in the clinic model. Select an agent to inspect its role and code.'],
    actions: ['Actions recorded', 'activity', 'Requests and task outcomes recorded by each agent. This includes refusals and failures.'],
    messages: ['Messages sent', 'exchange', 'Each message is counted once in the clinic total. Per-agent counts include messages sent or received.'],
    replans: ['Replans', 'repeat', 'A replan occurs when the workflow changes in response to a conflict or another scheduling event.'],
  };

  const getMetric = key => {
    const [title, icon, description] = metrics[key];
    const value = model.totals[key];
    if (key === 'replans') {
      const items = events.filter(event => event.kind === 'replan').reverse()
        .map(event => ({ label: event.reason || event.goal, value: 'Assistant' }));
      if (overview.replans) items.push({ label: 'Shared simulation', value: overview.replans });
      return { key, title, icon, description, value, items };
    }
    const items = model.agents.slice().sort((a, b) => key === 'agents' ? 0 : b[key] - a[key]).map(agent => ({
      label: agent.name, value: key === 'agents' ? inspector.catalog[agent.role].title : agent[key], agent,
    }));
    return { key, title, icon, description, value, items };
  };

  for (const button of all('[data-metric-open]', root)) {
    button.addEventListener('click', () => inspector.showMetric(getMetric(button.dataset.metricOpen)));
  }
  for (const button of all('[data-role-filter]', root)) {
    button.addEventListener('click', () => {
      filter = button.dataset.roleFilter;
      for (const member of all('[data-role-filter]', root)) {
        member.setAttribute('aria-pressed', String(member === button));
      }
      repaintCounts();
    });
  }

  repaintCounts();
  events.forEach(event => paintActivity(event));
  paintPublications();

  if ('BroadcastChannel' in window) {
    const channel = new BroadcastChannel(`carebridge:${document.body.dataset.account}`);
    channel.onmessage = ({ data }) => {
      if (data.kind === 'activity_reset') {
        events = [];
        feed.replaceChildren();
        get('#agent-feed-empty', root).hidden = false;
        get('#flow-from', root).textContent = 'Sender';
        get('#flow-to', root).textContent = 'Recipient';
        get('#flow-label', root).textContent = 'Waiting for a request';
      } else {
        events = [...events, data].slice(-ACTIVITY_LIMIT);
      }
      repaintCounts();
      paintActivity(data, true);
    };
  }

  async function refreshShared() {
    if (document.hidden || pending) return;
    pending = true;
    try {
      const response = await fetch('/api/agents/overview');
      if (!response.ok) return;
      overview = await response.json();
      repaintCounts();
      paintPublications();
    } catch { /* the current view stays readable while reconnecting */ }
    finally { pending = false; }
  }
  window.setInterval(refreshShared, 4000);
  document.addEventListener('visibilitychange', refreshShared);
}
