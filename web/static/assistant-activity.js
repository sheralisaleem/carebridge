import { activeState, dashboardSnapshot, stateLabel } from './agent-activity.js';
import { agentName, get, iconNode, node, readActivity } from './ui.js';

export function mountAssistantActivity(root, inspector) {
  const banner = get('#current-run', root);
  if (!banner) return null;
  const title = get('#current-run-title', banner);
  const description = get('#current-run-description', banner);
  const inspect = get('#inspect-working-agent', banner);
  const stages = get('#activity', root);
  const empty = get('#activity-empty', root);
  const handoff = get('#assistant-handoff', root);
  const entries = new Map();
  let current = null;
  let last = null;

  function display(state, heading, detail = '', agent = null) {
    banner.dataset.state = state;
    title.textContent = heading;
    description.textContent = detail;
    current = agent;
    inspect.hidden = !agent;
    if (agent) inspect.setAttribute('aria-label', `View ${agentName(agent)} code and details`);
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

  function accept(event) {
    if (event.kind === 'replan') {
      empty.hidden = true;
      const row = node('li', null, 'stage-replan');
      row.append(iconNode('repeat'), node('div', `Replan · ${event.reason || event.goal}`));
      stages.append(row);
      display('working', 'Finding a different option', event.reason, 'scheduler');
      return;
    }
    if (event.kind === 'agent_message') {
      const actions = { DELEGATE: 'Task delegated', INFORM: 'Result returned', CONFIRM: 'Check cleared',
        REFUSE: 'Request stopped' };
      const message = actions[event.performative];
      if (!message) return;
      handoff.hidden = false;
      get('p', handoff).textContent = `${message} · ${agentName(event.from)} → ${agentName(event.to)}`;
      handoff.classList.remove('updated');
      requestAnimationFrame(() => requestAnimationFrame(() => handoff.classList.add('updated')));
      return;
    }
    if (event.kind !== 'agent_status') return;
    empty.hidden = true;
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
    current = null;
    last = null;
    entries.clear();
    stages.replaceChildren();
    empty.hidden = false;
    handoff.hidden = true;
  }

  inspect.addEventListener('click', () => {
    if (current) openAgent(current);
  });

  return {
    accept,
    ready() { display('idle', 'Ready for a request', 'Agent activity will appear as the clinic works.'); },
    start() { reset(); display('working', 'Request received', 'Preparing the next step…'); },
    reset,
    finish() {
      if (last && !activeState(last.state)) {
        display(last.state, last.state === 'completed' ? 'Request finished' : 'Request ended',
          last.detail, last.agent);
      } else display('idle', 'Ready for a request');
    },
    offline(message) { display('failed', 'Clinic connection interrupted', message); },
    error(message) { display('failed', 'Request could not finish', message); },
  };
}
