import { activeState, dashboardSnapshot, stateLabel } from './agent-activity.js';
import { agentName, get, iconNode, node, readActivity } from './ui.js';
import { createAssistantTrace } from './assistant-trace.js';
import { createAssistantTimeline } from './assistant-timeline.js';

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
  const timeline = createAssistantTimeline(stages, scroll);
  const entries = new Map();
  const trace = createAssistantTrace(timeline.append);
  let current = null;
  let last = null;
  let requests = 0;
  let failed = false;

  function display(state, heading, detail = '', agent = null) {
    banner.dataset.state = state;
    title.textContent = heading;
    description.textContent = detail;
    if (current !== agent) {
      runIcon.replaceChildren(iconNode(inspector.catalog[agent]?.icon || 'flow'));
      if (agent) runIcon.dataset.agentRole = agent;
      else delete runIcon.dataset.agentRole;
    }
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
    row.dataset.agentRole = event.agent;
    const reveal = node('div', null, 'stage-reveal');
    const step = node('div', null, 'stage-track');
    const marker = node('span', null, 'stage-marker');
    marker.append(iconNode(inspector.catalog[event.agent]?.icon || 'activity'));
    const title = node('div', null, 'stage-title');
    const button = node('button', agentName(event.agent), 'stage-agent');
    button.type = 'button';
    button.setAttribute('aria-label', `Inspect ${agentName(event.agent)}`);
    button.addEventListener('click', () => openAgent(event.agent));
    const badge = node('span', null, 'stage-state');
    title.append(button, badge);
    const line = node('p', null, 'stage-detail');
    const body = node('div', null, 'stage-body');
    body.append(title, line);
    step.append(marker, body);
    reveal.append(step);
    row.append(reveal);
    timeline.append(row, { step: true });
    return { row, line, badge };
  }

  function showHandoff(event, action) {
    const sender = event.performative === 'QUERY' ? 'Patient' : agentName(event.from);
    const recipient = agentName(event.to);
    const row = node('li', null, 'stage-message');
    const from = node('span', sender, 'handoff-agent');
    from.dataset.agentRole = event.from;
    const to = node('span', recipient, 'handoff-agent');
    to.dataset.agentRole = event.to;
    const label = node('span');
    label.append(from, ' → ', to, ` · ${action}`);
    row.append(iconNode('arrow'), label);
    timeline.append(row);
  }

  function renderEvent(event) {
    if (event.kind === 'replan') {
      const row = node('li', null, 'stage-replan');
      row.append(iconNode('repeat'), node('div', `Replan · ${event.reason || event.goal}`));
      timeline.append(row);
      display('working', 'Finding a different option', event.reason);
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
    trace.end();
    last = null;
    requests = 0;
    failed = false;
    entries.clear();
    timeline.reset();
    empty.hidden = false;
    display('idle', 'Ready for a request');
  }

  inspect.addEventListener('click', () => {
    if (current) openAgent(current);
  });

  return {
    accept(event) {
      renderEvent(event);
      trace.accept(event);
    },
    ready() { display('idle', 'Ready for a request'); },
    showLatest: timeline.showLatest,
    start() {
      trace.end();
      failed = false;
      last = null;
      entries.clear();
      empty.hidden = true;
      timeline.append(node('li', `Request ${++requests}`, 'timeline-request'), { boundary: true });
      display('working', 'Request received', 'Preparing the next step…');
    },
    reset,
    finish() {
      trace.end();
      if (failed) return;
      if (last && !activeState(last.state)) {
        display(last.state, last.state === 'completed' ? 'Request finished' : 'Request ended',
          last.detail, last.agent);
      } else display('idle', 'Ready for a request');
    },
    offline(message) {
      trace.end();
      display('failed', 'Clinic connection interrupted', message);
    },
    error(message) {
      trace.end();
      failed = true;
      display('failed', 'Request could not finish', message);
    },
  };
}
