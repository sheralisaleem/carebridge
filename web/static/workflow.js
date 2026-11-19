import { all, clearActivity, get, node, notice, prettyDate, recordActivity } from './ui.js';
import { mountAssistantActivity } from './assistant-activity.js';

export function mountWorkflow(root, inspector) {
  const page = root.dataset.workflow;
  const status = get('[data-workflow-status]', root);
  const choices = get('[data-choices]', root);
  const chat = get('#chat-log', root);
  const chatScroll = get('#conversation-scroll', root);
  const activity = mountAssistantActivity(root, inspector);
  const dialog = get('#confirm-dialog');
  const channel = 'BroadcastChannel' in window
    ? new BroadcastChannel(`carebridge:${document.body.dataset.account}`) : null;
  let traceSession = '', traceTurn = '';
  let socket, connected = false, busy = false, pending = null, connectAttempts = 0, focusBeforeConfirm;

  function available() {
    all('button[type="submit"], [data-action], [data-suggest], #new-session', root)
      .forEach(button => { button.disabled = !connected || busy; });
  }

  function send(message, turn = true) {
    if (!connected || socket.readyState !== WebSocket.OPEN || (turn && busy)) return false;
    socket.send(JSON.stringify(message));
    if (turn) {
      busy = true;
      available();
    }
    return true;
  }

  function addChat(author, message) {
    if (!chat) return;
    const intro = get('#chat-intro');
    if (intro) intro.hidden = true;
    const entry = node('div', null, `chat-message ${author}`);
    entry.append(node('span', author === 'patient' ? 'You' : 'CareBridge', 'chat-author'), node('p', message));
    chat.append(entry);
  }

  function showLatestChat() {
    if (chatScroll) chatScroll.scrollTop = chatScroll.scrollHeight;
  }

  function enterStep(index) {
    all('.steps li', root).forEach((step, position) => {
      step.classList.toggle('active', index === position);
      if (position === index) step.setAttribute('aria-current', 'step');
      else step.removeAttribute('aria-current');
    });
  }

  function renderSlots(slots, appointmentId) {
    choices.replaceChildren();
    choices.hidden = !slots.length;
    if (!slots.length) return;
    enterStep(1);
    const list = node('ol', null, 'choices');
    for (const slot of slots) {
      const row = node('li', null, 'choice');
      const detail = node('div', null, 'choice-detail');
      detail.append(node('strong', `${prettyDate(slot.date)} · ${slot.start_time}`),
        node('span', `${slot.doctor_name} · ${slot.department}`));
      const button = node('button', 'Select', 'button secondary-button');
      button.type = 'button';
      button.dataset.action = appointmentId ? 'reschedule' : 'book';
      button.dataset.slot = slot.slot_id;
      if (appointmentId) button.dataset.appointment = appointmentId;
      row.append(detail, button);
      list.append(row);
    }
    choices.append(list);
    available();
  }

  function renderAppointmentChoices(visits, action) {
    if (!visits.length) return;
    choices.hidden = false;
    const list = node('ol', null, 'choices');
    for (const visit of visits) {
      const row = node('li', null, 'choice');
      const detail = node('div');
      detail.append(node('strong', visit.department),
        node('span', `${visit.doctor_name} · ${prettyDate(visit.date)} · ${visit.start_time}`));
      const button = node('button', 'Select', 'button secondary-button');
      button.type = 'button';
      button.dataset.action = action;
      button.dataset.appointment = visit.appointment_id;
      row.append(detail, button);
      list.append(row);
    }
    choices.replaceChildren(list);
    available();
  }

  function renderAppts(visits) {
    if (page !== 'appointments') return;
    const today = root.dataset.today;
    const now = root.dataset.now;
    for (const name of ['upcoming', 'history']) {
      const list = get(`[data-tab-panel="${name}"]`, root);
      list.replaceChildren();
      for (const visit of visits) {
        const upcoming = visit.status === 'confirmed' && `${visit.date}T${visit.start_time}` >= now;
        if ((name === 'upcoming') !== upcoming) continue;
        const row = node('article', null, 'visit-row');
        const info = node('div', null, 'visit-main');
        info.append(node('strong', visit.department), node('span', visit.doctor_name),
          node('span', `${prettyDate(visit.date)} · ${visit.start_time} · ${visit.appointment_id}`, 'secondary'));
        row.append(info, node('span', visit.status.replaceAll('_', ' '),
          visit.status === 'confirmed' ? 'pill success' : 'pill'));
        if (upcoming || (visit.status === 'cancelled_by_clinic' && visit.date >= today)) {
          const actions = node('div', null, 'row-actions');
          for (const action of upcoming ? ['reschedule', 'cancel'] : ['reschedule']) {
            const button = node('button', action === 'reschedule' ? 'Reschedule' : 'Cancel',
              `link-button${action === 'cancel' ? ' danger' : ''}`);
            button.type = 'button';
            button.dataset.action = action;
            button.dataset.appointment = visit.appointment_id;
            actions.append(button);
          }
          row.append(actions);
        }
        list.append(row);
      }
      if (!list.children.length) list.append(node('p',
        name === 'upcoming' ? 'You have no upcoming appointments.' : 'You have no appointment history.', 'empty-state'));
    }
    available();
  }

  function publish(event) {
    recordActivity(event);
    channel?.postMessage(event);
  }

  function closeTraceTurn() {
    if (!traceTurn) return;
    publish({ kind: 'activity_end', turn_id: traceTurn, session: traceSession });
    traceTurn = '';
  }

  function trace(event) {
    if (event.turn_id) traceTurn = event.turn_id;
    if (['agent_status', 'agent_message', 'replan'].includes(event.kind)) {
      publish(event);
      activity?.accept(event);
    }
    const technical = get('#technical-trace');
    if (technical && ['plan', 'replan', 'agent_message', 'tool_call', 'tool_result', 'rag', 'memory', 'guardrail', 'confirm'].includes(event.kind)) {
      const detail = node('details');
      detail.append(node('summary',
        event.kind === 'agent_message' ? event.performative : event.kind.replaceAll('_', ' ')),
        node('pre', JSON.stringify(event, null, 2)));
      technical.prepend(detail);
      if (technical.children.length > 30) technical.lastChild.remove();
    }
  }

  function closeDialog() {
    if (dialog.open) dialog.close();
    pending = null;
    if (focusBeforeConfirm?.isConnected) focusBeforeConfirm.focus();
  }

  function answer(approved) {
    if (pending) send({ type: 'confirm', id: pending, approved }, false);
    closeDialog();
  }

  function openConfirm(event) {
    inspector?.close();
    enterStep(2);
    pending = event.id;
    focusBeforeConfirm = document.activeElement;
    get('#confirm-detail').textContent = event.summary;
    if (!dialog.open) dialog.showModal();
    get('#decline').focus();
  }

  function receive(event) {
    switch (event.type) {
      case 'session':
        traceSession = event.session_id;
        activity?.ready();
        connected = true; busy = false; connectAttempts = 0;
        notice(status, null); available();
        break;
      case 'assistant': {
        const text = event.slots?.length ? event.text.split('\n\n').map(section =>
          section.startsWith('Choose an available time:\n') ? 'Choose an available time below.' : section
        ).join('\n\n') : event.text;
        if (chat) {
          addChat('assistant', text);
          notice(status, null);
        } else notice(status, text);
        renderSlots(event.slots || [], event.reschedule_id);
        if (event.appointment_action) renderAppointmentChoices(event.appointments || [], event.appointment_action);
        showLatestChat();
        break;
      }
      case 'state':
        renderAppts(event.appointments || []);
        break;
      case 'trace':
        trace(event.event);
        break;
      case 'confirm_request':
        openConfirm(event);
        break;
      case 'confirm_timeout':
        closeDialog(); notice(status, 'Approval expired. No change was made.', true);
        showLatestChat();
        break;
      case 'turn_start':
        busy = true;
        activity?.start();
        if (activity) notice(status, null);
        else notice(status, 'Working on your request…');
        available();
        break;
      case 'turn_end':
        closeTraceTurn();
        activity?.finish();
        busy = false; available();
        break;
      case 'error':
        activity?.error(event.message);
        busy = false; available(); notice(status, event.message, true);
        showLatestChat();
        break;
    }
  }

  function connect() {
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
    socket.onmessage = message => {
      try { receive(JSON.parse(message.data)); }
      catch (error) { console.error('CareBridge could not render a server event', error); }
    };
    socket.onclose = () => {
      connected = false;
      busy = false;
      closeDialog();
      available();
      connectAttempts += 1;
      closeTraceTurn();
      const message = connectAttempts >= 3 ? 'Reload or sign in again.' : 'Reconnecting to the clinic…';
      if (activity) activity.offline(message);
      else notice(status, message, true);
      if (connectAttempts < 3) setTimeout(connect, 900 * connectAttempts);
    };
  }

  get('#approve').addEventListener('click', () => answer(true));
  get('#decline').addEventListener('click', () => answer(false));
  dialog.addEventListener('cancel', event => { event.preventDefault(); answer(false); });

  root.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.action) {
      const task = { action: button.dataset.action };
      if (button.dataset.appointment) task.appointment_id = button.dataset.appointment;
      if (button.dataset.slot) task.slot_id = button.dataset.slot;
      if (send({ type: 'action', task })) {
        notice(status, 'Checking current availability…');
        showLatestChat();
      }
    }
    if (button.dataset.suggest && send({ type: 'user', text: button.dataset.suggest })) {
      addChat('patient', button.dataset.suggest);
      showLatestChat();
    }
  });

  const views = all('[data-assistant-view]', root);
  for (const view of views) {
    view.addEventListener('click', () => {
      if (root.dataset.view === view.dataset.assistantView) return;
      root.dataset.view = view.dataset.assistantView;
      for (const button of views) button.setAttribute('aria-pressed', String(button === view));
      if (root.dataset.view === 'conversation') showLatestChat();
      else activity.showLatest();
    });
  }

  const chatForm = get('#chat-form', root);
  if (chatForm) {
    const input = get('#message', chatForm);
    chatForm.addEventListener('submit', event => {
      event.preventDefault();
      const text = input.value.trim();
      if (text && send({ type: 'user', text })) {
        addChat('patient', text);
        showLatestChat();
        input.value = '';
      }
    });
    input.addEventListener('keydown', event => {
      if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
      event.preventDefault();
      if (!event.repeat && connected && !busy) chatForm.requestSubmit();
    });
  }

  get('#new-session')?.addEventListener('click', () => {
    if (send({ type: 'new_session' })) {
      all('.chat-message').forEach(message => message.remove());
      get('#chat-intro').hidden = false;
      activity?.reset();
      get('#technical-trace').replaceChildren();
      renderSlots([], null);
      clearActivity();
      channel?.postMessage({ kind: 'activity_reset' });
    }
  });

  const department = get('#booking-department');
  const doctor = get('#booking-doctor');
  const filterDoctors = () => {
    if (!doctor) return;
    for (const option of doctor.options) {
      option.hidden = Boolean(option.value && department.value && option.dataset.department !== department.value);
      option.disabled = option.hidden;
      if (option.hidden && option.selected) doctor.value = '';
    }
  };
  department?.addEventListener('change', filterDoctors);
  filterDoctors();

  get('#booking-form')?.addEventListener('submit', event => {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const task = { action: 'find_slots', department: fields.get('department') };
    if (fields.get('doctor_id')) task.doctor_id = fields.get('doctor_id');
    if (fields.get('date')) {
      task.date_from = fields.get('date');
      task.date_to = task.date_from;
      task.one_per_day = false;
    }
    send({ type: 'action', task });
  });

  for (const tab of all('[data-panel]', root)) {
    tab.addEventListener('click', () => {
      for (const other of all('[data-panel]', root)) {
        other.classList.toggle('active', other === tab);
        other.setAttribute('aria-pressed', String(other === tab));
      }
      for (const panel of all('[data-tab-panel]', root)) panel.hidden = panel.dataset.tabPanel !== tab.dataset.panel;
    });
  }
  window.addEventListener('pagehide', closeTraceTurn);
  available();
  connect();
}
