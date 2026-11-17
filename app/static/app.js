let options = {campaigns: [], offers: [], domains: [], groups: [], sources: []};
let currentCampaign = null;

const $ = selector => document.querySelector(selector);
function notify(message, error = false) {
  const box = $('#notice'); box.textContent = message; box.className = `notice${error ? ' error' : ''}`; box.hidden = false;
}
async function request(url, method = 'GET', body) {
  const res = await fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  let data; try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed. Check your inputs.');
  return data;
}
function option(select, list, placeholder) {
  select.replaceChildren(new Option(placeholder, ''));
  for (const item of list) select.add(new Option(`${item.name || item.title || item.id} (#${item.id})`, item.id));
}
function list(value) { return Array.isArray(value) ? value : (value?.items || value?.data || []); }
async function loadOptions() {
  try {
    options = await request('/api/options');
    for (const key of Object.keys(options)) options[key] = list(options[key]);
    const form = $('#create-form');
    option(form.elements.offer_id, options.offers, 'Choose an offer');
    option(form.elements.domain_id, options.domains, 'Choose a domain');
    option(form.elements.group_id, options.groups.filter(g => !g.type || ['campaigns','campaign'].includes(g.type)), 'Choose a campaign group');
    option(form.elements.traffic_source_id, options.sources, 'Choose a traffic source');
    option($('#campaign'), options.campaigns, 'Choose a campaign');
    if (currentCampaign) $('#campaign').value = String(currentCampaign);
    notify('Tracker data loaded.');
  } catch (error) { notify(error.message, true); }
}
function element(tag, text, cls) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; }
function button(text, fn, cls = 'secondary') { const b = element('button', text, cls); b.type = 'button'; b.onclick = async () => { b.disabled = true; try { await fn(); } catch (e) { notify(e.message, true); } finally { b.disabled = false; } }; return b; }
async function refreshDrafts() { render(await request(`/api/campaigns/${currentCampaign}/drafts`)); }
async function action(url, body) { await request(url, 'POST', body); await refreshDrafts(); }
function render(streams) {
  const container = $('#streams'); container.replaceChildren();
  if (!streams.length) { container.append(element('div', 'No streams found.', 'empty')); return; }
  for (const stream of streams) {
    const card = element('article', undefined, `stream${stream.dirty ? ' dirty' : ''}`);
    const head = element('div', undefined, 'stream-head'); head.append(element('h3', `${stream.name} (#${stream.stream_id})`), element('span', stream.dirty ? 'UNPUBLISHED' : 'SYNCED', 'status')); card.append(head);
    if (!stream.has_offers) { card.append(element('div', 'This stream has no offers to edit.', 'offer')); container.append(card); continue; }
    for (const item of stream.offers) {
      const row = element('div', undefined, `offer${item.removed ? ' removed' : ''}`);
      row.append(element('span', `${item.name} (#${item.offer_id})`, 'name'));
      const share = element('input'); share.type = 'number'; share.min = 0; share.max = 100; share.value = item.share; share.disabled = item.removed;
      const pin = element('input'); pin.type = 'checkbox'; pin.checked = item.pinned; pin.disabled = item.removed;
      const pinLabel = element('label'); pinLabel.append(pin, document.createTextNode(' Pin')); row.append(share, element('span','%'), pinLabel);
      const pinUrl = `/api/streams/${stream.stream_id}/offers/${item.offer_id}/pin`;
      pin.onchange = async () => { try { await action(pinUrl, {pinned: pin.checked, share: Number(share.value)}); } catch(e) { notify(e.message,true); await refreshDrafts(); } };
      share.onchange = async () => { try { await action(pinUrl, {pinned: true, share: Number(share.value)}); } catch(e) { notify(e.message,true); await refreshDrafts(); } };
      const which = item.removed ? 'restore' : 'remove';
      row.append(button(item.removed ? 'Bring back' : 'Remove', () => action(`/api/streams/${stream.stream_id}/offers/${item.offer_id}/${which}`), 'link-button'));
      card.append(row);
    }
    const controls = element('div', undefined, 'stream-actions');
    const select = element('select'); option(select, options.offers, 'Select offer to add'); controls.append(select);
    controls.append(button('Add offer', async () => { const chosen = options.offers.find(x => String(x.id) === select.value); if (!chosen) throw Error('Choose an offer first.'); await action(`/api/streams/${stream.stream_id}/offers`, {offer_id: Number(chosen.id), name: chosen.name}); }));
    controls.append(element('span', '', 'spacer'));
    if (stream.dirty) {
      controls.append(button('Cancel draft', () => action(`/api/streams/${stream.stream_id}/cancel`)));
      controls.append(button('Publish to Keitaro', async () => { await action(`/api/streams/${stream.stream_id}/publish`); notify('Stream published successfully.'); }, 'primary'));
    }
    card.append(controls); container.append(card);
  }
}
$('#refresh').onclick = loadOptions;
$('#fetch').onclick = async event => {
  const fetchButton = event.currentTarget;
  fetchButton.disabled = true;
  try {
    const id = Number($('#campaign').value);
    if (!id) throw new Error('Select a campaign first.');
    const streams = await request(`/api/campaigns/${id}/fetch`, 'POST');
    currentCampaign = id;
    render(streams);
    notify(streams.some(stream => stream.dirty) ? 'Saved draft reopened.' : 'Streams fetched.');
  } catch (error) {
    notify(error.message, true);
  } finally {
    fetchButton.disabled = false;
  }
};
$('#create-form').onsubmit = async event => { event.preventDefault(); const form = event.currentTarget; const fields = Object.fromEntries(new FormData(form)); for (const key of ['offer_id','domain_id','group_id','traffic_source_id']) fields[key] = Number(fields[key]); try { const result = await request('/api/campaigns', 'POST', fields); currentCampaign = result.id; await loadOptions(); notify(`Campaign ${result.name} created (#${result.id}).`); render(await request(`/api/campaigns/${result.id}/fetch`, 'POST')); } catch(e) { notify(e.message, true); } };
loadOptions();
