import { stateLabel } from './agent-activity.js';
import { agentName, iconNode, node } from './ui.js';

export function createAssistantTrace(appendRow) {
  const pendingTools = new Map();
  let plan = [];

  function showPlan(event) {
    if (!Array.isArray(event.steps) || !event.steps.length) return;
    const row = node('li', null, 'plan-card');
    const toggle = node('button', event.kind === 'replan' ? 'Updated plan' : 'Request plan', 'plan-toggle');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'false');
    const indicator = node('span', null, 'plan-chevron');
    indicator.setAttribute('aria-hidden', 'true');
    toggle.append(indicator);
    const details = node('div', null, 'plan-disclosure');
    details.inert = true;
    const content = node('div', null, 'plan-content');
    const steps = node('ol', null, 'plan-steps');
    plan = event.steps.map(step => {
      const item = node('li', null, 'plan-step');
      const owner = node('span', agentName(step.tool), 'plan-owner');
      owner.dataset.agentRole = step.tool;
      item.append(node('strong', step.step), owner);
      item.dataset.state = 'planned';
      const status = node('span', 'Planned', 'plan-status');
      item.append(status);
      steps.append(item);
      return { item, status, owner: step.tool, task: null };
    });
    content.append(node('p', event.goal, 'plan-goal'), steps);
    details.append(content);
    row.append(toggle, details);
    toggle.addEventListener('click', () => {
      const open = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', String(open));
      details.inert = !open;
    });
    appendRow(row);
  }

  function updateStep(step, state) {
    step.item.dataset.state = state;
    step.status.textContent = state === 'interrupted' ? 'Interrupted' : stateLabel(state, step.owner);
  }

  function applyStepEvent(event) {
    if (event.kind === 'agent_message' && event.performative === 'DELEGATE') {
      const step = plan.find(candidate => candidate.owner === event.to && candidate.task === null);
      if (step) step.task = event.id;
    }
    if (event.kind === 'agent_status' && event.task_id) {
      const step = plan.find(candidate => candidate.task === event.task_id);
      if (step) updateStep(step, event.state);
    }
  }

  function toolCall(event) {
    const row = node('li', null, 'tool-card');
    row.dataset.state = 'running';
    const disclosure = node('details');
    const summary = node('summary');
    const mark = node('span', null, 'tool-mark');
    mark.setAttribute('aria-hidden', 'true');
    const status = node('span', 'Running', 'tool-status');
    summary.append(mark, node('code', event.name), status);
    const payload = node('div', null, 'tool-payload');
    const output = node('pre', 'Waiting for a result.');
    const link = node('a', 'View tool reference', 'text-link');
    link.href = `/mcp#${encodeURIComponent(event.name)}`;
    payload.append(node('h4', 'Input'), node('pre', JSON.stringify(event.args, null, 2)),
      node('h4', 'Output'), output, link);
    disclosure.append(summary, payload);
    row.append(disclosure);
    appendRow(row);
    return { row, mark, status, output };
  }

  function updateTool(tool, state, label, output) {
    tool.row.dataset.state = state;
    tool.mark.replaceChildren(iconNode(state === 'success' ? 'shield' : 'close'));
    tool.status.textContent = label;
    tool.output.textContent = output;
  }

  return {
    accept(event) {
      if (event.kind === 'plan' || event.kind === 'replan') showPlan(event);
      applyStepEvent(event);
      if (event.kind !== 'tool_call' && event.kind !== 'tool_result') return;
      const key = [event.session, event.turn_id, event.task_id, event.name].join(':');
      if (event.kind === 'tool_call' && event.via === 'MCP') pendingTools.set(key, toolCall(event));
      if (event.kind === 'tool_result' && pendingTools.has(key)) {
        const tool = pendingTools.get(key);
        pendingTools.delete(key);
        const ok = event.result?.ok === true;
        updateTool(tool, ok ? 'success' : 'error', ok ? 'Succeeded' : 'Failed', JSON.stringify(event.result, null, 2));
      }
    },
    end() {
      for (const tool of pendingTools.values()) updateTool(tool, 'error', 'Interrupted', 'No result was received.');
      pendingTools.clear();
      for (const step of plan) {
        if (['working', 'waiting'].includes(step.item.dataset.state)) updateStep(step, 'interrupted');
      }
      plan = [];
    },
  };
}
