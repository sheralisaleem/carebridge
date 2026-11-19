function showLinkedTool() {
  const tool = document.getElementById(location.hash.slice(1));
  if (!(tool instanceof HTMLDetailsElement)) return;
  tool.open = true;
  tool.scrollIntoView({ block: 'start' });
}

showLinkedTool();
window.addEventListener('hashchange', showLinkedTool);
