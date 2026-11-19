import { activeState, dashboardSnapshot, stateLabel } from './agent-activity.js';
import { agentName, get, iconNode, node, readActivity } from './ui.js';

const handoffActions = {
  QUERY: 'Check requested', DELEGATE: 'Task delegated', INFORM: 'Result returned',
  CONFIRM: 'Check cleared', REFUSE: 'Request stopped',
};

export function mountAssistantActivity(root, inspector) {
  const banner = get('#current-run', root);
  if (!banner) return null;
  const title = get('#current-run-title', banner);
  const description = get('#current-run-description', banner);
  const inspect = get('#inspect-working-agent', banner);
  const runIcon = get('#current-run-icon', banner);
  const stages = get('#activity', root);
  const scroll = get('#activity-scroll', root);
  const empty = get('#activity-empty', root);
  const handoff = get('#assistant-handoff', root);
  const handoffAction = get('#handoff-action', handoff);
  const route = get('#handoff-route', handoff);
  const entries = new Map();
  let current = null;
  let last = null;
  let requests = 0;
  let failed = false;

  function display(state, heading, detail = '', agent = null) {
    banner.dataset.state = state;
    title.textContent = heading;
    description.textContent = detail;
    if (current !== agent) runIcon.replaceChildren(iconNode(inspector.catalog[agent]?.icon || 'flow'));
    current = agent;
    inspect.hidden = !agent;
    if (agent) inspect.setAttribute('aria-label', `View ${agentName(agent)} code and details`);
  }

  function atLatest() {
    return scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop < 24;
  }

  function openAgent(id) {
    const overview = {
      agents: [{ id, role: id, name: agentName(id), state: 'idle', actions: 0, messages: 0 }],
      messages: 0, replans: 0,
    };
    inspector.showAgent(dashboardSnapshot(overview, readActivity()).agents[0]);
  }

  function createStep(event) {
    const row = node('li', null, 'request-stage');
    row.append(node('i', null, 'stage-marker'));
    const title = node('div', null, 'stage-title');
    const icon = iconNode(inspector.catalog[event.agent]?.icon || 'activity');
    const button = node('button', null, 'stage-agent');
    button.type = 'button';
    button.setAttribute('aria-label', `Inspect ${agentName(event.agent)}`);
    button.append(icon, node('span', agentName(event.agent)));
    button.addEventListener('click', () => openAgent(event.agent));
    const badge = node('span', null, 'stage-state');
    title.append(button, badge);
    const line = node('p', null, 'stage-detail');
    const body = node('div', null, 'stage-body');
    body.append(title, line);
    row.append(body);
    stages.append(row);
    return { row, line, badge };
  }

  function showHandoff(event, action) {
    const sender = event.performative === 'QUERY' ? 'Patient' : agentName(event.from);
    const recipient = agentName(event.to);
    const from = node('span', null, 'handoff-endpoint');
    from.append(iconNode(inspector.catalog[event.from]?.icon || 'person'), node('span', sender));
    const to = node('span', null, 'handoff-endpoint');
    to.append(iconNode(inspector.catalog[event.to]?.icon || 'flow'), node('span', recipient));
    const signal = node('span', null, 'handoff-signal');
    signal.setAttribute('role', 'img');
    signal.setAttribute('aria-label', 'to');
    handoff.hidden = false;
    handoffAction.textContent = action;
    route.replaceChildren(from, signal, to);

    const row = node('li', null, 'stage-message');
    row.append(iconNode('arrow'), node('span', `${sender} → ${recipient} · ${action}`));
    stages.append(row);
  }

  function renderEvent(event) {
    if (event.kind === 'replan') {
      const row = node('li', null, 'stage-replan');
      row.append(iconNode('repeat'), node('div', `Replan · ${event.reason || event.goal}`));
      stages.append(row);
      display('working', 'Finding a different option', event.reason);
      return;
    }
    if (event.kind === 'agent_message') {
      const action = handoffActions[event.performative];
      if (action) showHandoff(event, action);
      return;
    }
    if (event.kind !== 'agent_status') return;
    const key = `${event.turn_id}:${event.task_id || ''}:${event.agent}`;
    if (!entries.has(key)) entries.set(key, createStep(event));
    const { row, line, badge } = entries.get(key);
    row.dataset.state = event.state;
    badge.textContent = stateLabel(event.state, event.agent);
    line.textContent = event.detail;
    if (event.state === 'waiting') {
      display('waiting', 'Waiting for your approval', 'Front Desk has prepared the appointment change.', event.agent);
    } else if (event.state === 'working') {
      display('working', `${agentName(event.agent)} is working`, event.detail, event.agent);
    } else {
      last = event;
      display(event.state, agentName(event.agent), event.detail, event.agent);
    }
  }

  function reset() {
    last = null;
    requests = 0;
    failed = false;
    entries.clear();
    stages.replaceChildren();
    empty.hidden = false;
    handoff.hidden = true;
    display('idle', 'Ready for a request');
  }

  inspect.addEventListener('click', () => {
    if (current) openAgent(current);
  });

  return {
    accept(event) {
      const follow = atLatest();
      renderEvent(event);
      if (follow) scroll.scrollTop = scroll.scrollHeight;
    },
    ready() { display('idle', 'Ready for a request'); },
    showLatest() { scroll.scrollTop = scroll.scrollHeight; },
    start() {
      const follow = atLatest();
      failed = false;
      last = null;
      entries.clear();
      handoff.hidden = true;
      empty.hidden = true;
      stages.append(node('li', `Request ${++requests}`, 'timeline-request'));
      display('working', 'Request received', 'Preparing the next step…');
      if (follow) scroll.scrollTop = scroll.scrollHeight;
    },
    reset,
    finish() {
      if (failed) return;
      const follow = atLatest();
      if (last && !activeState(last.state)) {
        display(last.state, last.state === 'completed' ? 'Request finished' : 'Request ended',
          last.detail, last.agent);
      } else display('idle', 'Ready for a request');
      if (follow) scroll.scrollTop = scroll.scrollHeight;
    },
    offline(message) { display('failed', 'Clinic connection interrupted', message); },
    error(message) {
      failed = true;
      display('failed', 'Request could not finish', message);
    },
  };
}
