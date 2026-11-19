export const ACTIVITY_LIMIT = 100;
const terminal = new Set(['completed', 'refused', 'failed']);
export const activeState = state => state === 'working' || state === 'waiting';

export function dashboardSnapshot(overview, events) {
  const rows = new Map(overview.agents.map(agent => [agent.id, { ...agent }]));
  const outcomes = new Set();
  const messages = new Set();
  const latest = new Map();
  const ended = new Set();
  let replans = overview.replans || 0;
  for (const event of events) {
    if (event.kind === 'agent_status' && rows.has(event.agent)) {
      latest.set(event.agent, event);
      if (terminal.has(event.state)) {
        const key = [event.session, event.turn_id, event.task_id, event.agent].join(':');
        if (!outcomes.has(key)) {
          outcomes.add(key);
          rows.get(event.agent).actions += 1;
        }
      }
    } else if (event.kind === 'agent_message' && !messages.has(event.id)) {
      messages.add(event.id);
      for (const id of new Set([event.from, event.to])) {
        if (rows.has(id)) rows.get(id).messages += 1;
      }
    } else if (event.kind === 'replan') {
      replans += 1;
    } else if (event.kind === 'activity_end') {
      ended.add(`${event.session}:${event.turn_id}`);
    }
  }
  for (const [id, event] of latest) {
    const row = rows.get(id);
    row.state = activeState(event.state) && ended.has(`${event.session}:${event.turn_id}`) ? 'idle' : event.state;
    row.last = event.detail;
  }
  const agents = Array.from(rows.values());
  return {
    agents, rows,
    totals: {
      agents: agents.length,
      actions: agents.reduce((sum, agent) => sum + agent.actions, 0),
      messages: overview.messages + messages.size,
      replans,
    },
  };
}

export function stateLabel(state, role) {
  switch (state) {
    case 'working': return 'Working';
    case 'waiting': return role === 'receptionist' ? 'Waiting for approval' : 'Waiting';
    case 'completed': return 'Done';
    case 'refused': return 'Stopped';
    case 'failed': return 'Needs attention';
    case 'unavailable': return 'On leave';
    case 'booked': return 'Booked';
    case 'planning': return 'Plan recorded';
    case 'replanning': return 'Replan recorded';
    case 'requesting': return 'Request sent';
    case 'selecting': return 'Selection made';
    default: return 'Idle';
  }
}
