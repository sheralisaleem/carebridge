const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  const activitySource = fs.readFileSync('web/static/agent-activity.js', 'utf8');
  const activityUrl = `data:text/javascript;base64,${Buffer.from(activitySource).toString('base64')}`;
  const source = fs.readFileSync('web/static/ui.js', 'utf8').replace("'./agent-activity.js'", JSON.stringify(activityUrl));
  const { agentName, prettyDate } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  assert.equal(agentName('receptionist'), 'Front Desk');
  assert.equal(agentName('SIM-01'), 'Simulated patient');
  assert.equal(prettyDate('2026-11-12').replaceAll(',', ''), 'Thu 12 Nov');
  const { dashboardSnapshot } = await import(activityUrl);
  const shared = { messages: 3, replans: 2, agents: [
    { id: 'scheduler', role: 'scheduler', actions: 4, messages: 2, state: 'idle' },
    { id: 'coordinator', role: 'coordinator', actions: 1, messages: 1, state: 'idle' },
  ] };
  const base = { session: 's1', turn_id: 't1', task_id: 'task1', agent: 'scheduler' };
  const message = { kind: 'agent_message', id: 'm1', from: 'coordinator', to: 'scheduler' };
  const done = { ...base, kind: 'agent_status', state: 'completed', detail: 'Found three times' };
  const end = { ...base, kind: 'activity_end' };
  const working = { ...base, kind: 'agent_status', state: 'working' };
  const model = dashboardSnapshot(shared, [working, message, message, done, done, { kind: 'replan' }]);
  assert.deepEqual(model.totals, { agents: 2, actions: 6, messages: 4, replans: 3 });
  assert.equal(model.rows.get('scheduler').messages, 3);
  assert.equal(model.rows.get('scheduler').state, 'completed');
  assert.equal(dashboardSnapshot(shared, [working, end]).rows.get('scheduler').state, 'idle');
  console.log('UI logic tests passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
