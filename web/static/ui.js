import { ACTIVITY_LIMIT } from './agent-activity.js';

const agentNames = {
  coordinator: 'Coordinator', safety: 'Safety Officer', scheduler: 'Scheduler',
  receptionist: 'Front Desk', policy: 'Policy Librarian', memory: 'Memory Keeper',
};

export const agentName = (id) => agentNames[id] || (id?.startsWith('SIM-') ? 'Simulated patient' : 'Patient or doctor');
export const get = (selector, root = document) => root.querySelector(selector);
export const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));
export const csrf = () => get('[name="csrf-token"]')?.content;

export function node(tag, value, className) {
  const item = document.createElement(tag);
  if (value != null) item.textContent = value;
  if (className) item.className = className;
  return item;
}

export function iconNode(name) {
  const ns = 'http://www.w3.org/2000/svg';
  const icon = document.createElementNS(ns, 'svg');
  const use = document.createElementNS(ns, 'use');
  icon.setAttribute('class', 'icon');
  icon.setAttribute('aria-hidden', 'true');
  use.setAttribute('href', '/static/icons.svg#' + name);
  icon.append(use);
  return icon;
}

export function notice(element, message, failed = false) {
  if (!element) return;
  element.textContent = message || '';
  element.hidden = !message;
  element.classList.toggle('error', failed);
}

export function prettyDate(date) {
  return new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
    .format(new Date(`${date}T12:00:00`));
}

const storageKey = () => `carebridge:activity:${document.body.dataset.account}`;

export function readActivity() {
  try { return JSON.parse(sessionStorage.getItem(storageKey())) || []; }
  catch { return []; }
}

export function recordActivity(event) {
  try {
    const events = [...readActivity(), event].slice(-ACTIVITY_LIMIT);
    sessionStorage.setItem(storageKey(), JSON.stringify(events));
  } catch { /* storage may be unavailable */ }
}

export function clearActivity() {
  try { sessionStorage.removeItem(storageKey()); }
  catch { /* storage may be unavailable */ }
}

export function activityRow(event) {
  const row = node('li');
  row.append(node('i', null, `status-dot ${event.state || ''}`));
  const detail = node('div');
  detail.append(node('strong', event.kind === 'replan' ? 'Replan' : agentName(event.agent)),
    node('span', ` · ${event.detail || event.reason || ''}`));
  row.append(detail);
  return row;
}
