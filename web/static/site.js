import { all, clearActivity, csrf, get, node, notice } from './ui.js';
import { mountWorkflow } from './workflow.js';

import { mountAgentInspector } from './agent-inspector.js';
import { mountAgentsPage } from './agents.js';

const inspector = mountAgentInspector();
const agentsPage = get('#agents-page');
if (agentsPage) mountAgentsPage(agentsPage, inspector);
const workflow = get('[data-workflow]');
if (workflow) mountWorkflow(workflow, inspector);
get('#logout-form')?.addEventListener('submit', clearActivity);

if (document.body.dataset.page === '/clinic') {
  const openPolicy = () => {
    const target = document.getElementById(location.hash.slice(1));
    if (target?.matches('#policies details')) {
      target.open = true;
      target.scrollIntoView({ block: 'start' });
    }
  };
  openPolicy();
  window.addEventListener('hashchange', openPolicy);
}

const directory = get('[data-directory]');
if (directory) {
  const options = get('#department-filter');
  const input = get('#doctor-filter');
  const rows = all('.directory-row');
  const filter = () => {
    for (const row of rows) {
      row.hidden = (options.value && row.dataset.department !== options.value)
        || !row.dataset.name.includes(input.value.toLowerCase().trim());
    }
    get('#no-doctors').hidden = rows.some(row => !row.hidden);
  };
  options.addEventListener('change', filter);
  input.addEventListener('input', filter);
}

const hoursList = get('#hours-list');
if (hoursList) {
  get('#add-window').addEventListener('click', () => {
    const row = get('#hour-template').content.cloneNode(true);
    const field = get('select', row);
    hoursList.append(row);
    field.focus();
  });
  hoursList.addEventListener('click', event => {
    const button = event.target.closest('[data-remove-window]');
    if (button) {
      button.closest('.hour-row').remove();
      get('#add-window').focus();
    }
  });
}

const knowledgeForm = get('#knowledge-search');
if (knowledgeForm) {
  knowledgeForm.addEventListener('submit', async event => {
    event.preventDefault();
    const results = get('#policy-results');
    const query = new FormData(knowledgeForm).get('query');
    results.replaceChildren(node('p', 'Searching…', 'secondary'));
    try {
      const response = await fetch(`/api/kb/search?q=${encodeURIComponent(query)}`);
      if (!response.ok) throw Error('Search could not be completed.');
      const result = await response.json();
      results.replaceChildren();
      if (!result.results.some(item => item.above)) {
        results.append(node('p', 'No result met the evidence threshold.', 'notice'));
      }
      for (const hit of result.results) {
        const article = node('article');
        article.append(node('h3', hit.section || hit.title), node('p', hit.source, 'secondary'),
          node('p', `Score ${hit.score.toFixed(2)} · ${hit.above ? 'Evidence' : 'Below threshold'}`),
          node('p', hit.text, 'document'));
        results.append(article);
      }
    } catch (error) {
      results.replaceChildren(node('p', error.message, 'notice error'));
    }
  });
}

const simulation = get('#simulation-form');
if (simulation) {
  const status = get('#simulation-status');
  const log = get('#simulation-log');
  const controls = [...all('button', simulation), ...all('[data-simulation]')];
  async function run(body) {
    controls.forEach(button => { button.disabled = true; });
    notice(status, 'Applying change to the shared clinic…');
    try {
      const response = await fetch('/api/simulation', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
        body: JSON.stringify(body),
      });
      const data = await response.json();
      notice(status, data.message, !response.ok);
      if ('log' in data) {
        log.replaceChildren();
        for (const event of data.log) {
          const row = node('li');
          row.append(node('strong', `Tick ${event.tick}`),
            node('span', `${event.event} · ${event.patient} · ${event.detail}`));
          log.append(row);
        }
      }
    } catch {
      notice(status, 'Could not reach the simulation service.', true);
    } finally {
      controls.forEach(button => { button.disabled = false; });
    }
  }
  simulation.addEventListener('submit', event => {
    event.preventDefault();
    run({ event: 'run', ...Object.fromEntries(new FormData(simulation)) });
  });
  for (const button of all('[data-simulation]')) {
    button.addEventListener('click', () => {
      if (button.dataset.simulation !== 'doctor_leave'
          || confirm('Simulate doctor leave and cancel affected synthetic appointments?')) {
        run({ event: button.dataset.simulation });
      }
    });
  }
}
