const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
class HouseholdShoppingCard extends HTMLElement {
  setConfig(config) { this.config=config; if(!this.shadowRoot) this.attachShadow({mode:'open'}); }
  getCardSize() { return 8; }
  connectedCallback() { this.clock=setInterval(()=>this.countdown(),1000); }
  disconnectedCallback() { clearInterval(this.clock); }
  set hass(hass) {
    this._hass=hass;
    const state=hass.states[this.config.entity || 'sensor.household_shopping'];
    if(!this.ready) this.build();
    this.shadowRoot.querySelector('#status').textContent=state?.state || 'Waiting for household shopping…';
    const people=Object.values(hass.states).filter(s=>s.entity_id.startsWith('person.')).map(s=>s.attributes.friendly_name);
    const options=['',...people,'Household'];
    for(const select of this.shadowRoot.querySelectorAll('select.person')) {
      const signature=JSON.stringify(options);
      if(select.dataset.options!==signature) { const previous=select.value; select.innerHTML=options.map(p=>`<option value="${esc(p)}">${esc(p || 'Me')}</option>`).join('') + '<option value="__guest__">Custom guest…</option>'; select.value=previous; select.dataset.options=signature; }
    }
    const stores=state?.attributes.stores || ['Albert Heijn','Vomar','Action','DekaMarkt','Hema','Kruidvat'];
    const storeOptions = JSON.stringify(stores);
    for (const select of this.shadowRoot.querySelectorAll('select.store')) {
      if (select.dataset.options === storeOptions) continue;
      const previous = select.value;
      const choices = stores.map(name => `<option>${esc(name)}</option>`).join('');
      const editor = select.id === 'edit-store';
      select.innerHTML = (editor ? '<option value="">Add a store…</option>' : '') + choices;
      if (stores.includes(previous)) select.value = previous;
      select.dataset.options = storeOptions;
    }
    this.storeCards = state?.attributes.store_cards || {};
    this.storeTags = state?.attributes.store_tags || {};
    this.shadowRoot.querySelector('#labels').hidden=!state?.attributes.label_available;
    const trips=state?.attributes.trips || [];
    const signature=JSON.stringify(trips);
    if(this.signature!==signature) { this.signature=signature; this.renderTrips(trips); }
    this.countdown();
  }
  build() {
    this.shadowRoot.innerHTML=`<style>
      :host{display:block;color:var(--primary-text-color)}ha-card{padding:20px}h2{margin:0 0 8px;font-size:22px}h3{margin:20px 0 10px;font-size:18px}p{line-height:1.5}.muted,small{color:var(--secondary-text-color)}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:end}label{display:flex;flex-direction:column;gap:6px;flex:1;min-width:100px;font-size:13px}input,select,button{box-sizing:border-box;font:inherit;color:inherit;border:1px solid var(--divider-color);border-radius:10px;background:var(--card-background-color);padding:12px;min-height:44px;max-width:100%}button{cursor:pointer;font-weight:600}button.primary{background:var(--primary-color);color:var(--text-primary-color,#fff);border-color:transparent}button:disabled{opacity:.5;cursor:wait}input[type=checkbox]{min-height:22px;width:22px}.check{flex-direction:row;align-items:center;margin:12px 0}.trip{border-top:1px solid var(--divider-color);padding:16px 0}.trip strong{font-size:17px}.actions{margin:12px 0}.error{color:var(--error-color);white-space:pre-wrap}details{margin:12px 0}summary{cursor:pointer;padding:8px 0}.pay{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:8px 0}.pay input[type=text]{width:95px}.pay label{flex:1}.item{margin:5px 0;font-size:14px}#feedback{min-height:20px;margin:12px 0;white-space:pre-wrap}form{margin:12px 0}.date{width:100%}@media(max-width:400px){ha-card{padding:14px}.row>button{width:100%}}
    </style><ha-card><h2>Household shopping</h2><small id="status"></small>
    <h3>Add what you need</h3><form id="add"><div class="row"><label>Shop<select name="store" class="store"></select></label><label>For<select name="requester" class="person"></select></label></div><div id="guest-name" hidden><label>Guest name<input name="guest" maxlength="40" disabled></label></div><div class="row"><label style="flex:3">Item<input name="item" required maxlength="180" placeholder="Milk, pasta, bin bags…"></label><label style="flex:0;min-width:75px">Qty<input name="quantity" type="number" min="1" max="100" value="1" required></label><button class="primary">Add</button></div></form>
    <h3>Heading out?</h3><form id="start"><div class="row"><label>First or next shop<select class="store" name="store"></select></label><label>Guest shopper (optional)<input name="guest_shopper" maxlength="40" placeholder="Leave blank if it's you"></label><button class="primary">I'm going</button></div><p class="muted">Add another shop during the five-minute countdown. Each shop gets its own receipt; the deadline stays the same.</p></form>
    <details><summary>Manage stores & loyalty cards</summary>
      <label>Edit an existing store<select id="edit-store" class="store"></select></label>
      <form id="store-form">
        <label>Store name<input name="store" required maxlength="40"></label>
        <label>Loyalty card<input name="loyalty" maxlength="512" placeholder="Card number, qr:CODE, or image:cards/store.png"></label>
        <p class="muted">Image paths are relative to Home Assistant's /config/www folder. Clear the value to remove a card.</p>
        <label>NFC tag<input name="tag_id" maxlength="100" placeholder="Tag ID from Settings → Tags"></label>
        <p class="muted">Scanning this tag starts a trip to the store. Leave it empty for no tag.</p>
        <div class="row"><button class="primary">Save store</button><button id="remove-store" type="button" disabled>Remove store</button></div>
      </form>
    </details>
    <p><button id="pair">Connect my Telegram</button></p><div id="feedback" role="status" aria-live="polite"></div><div id="trips"></div>
    <section id="labels"><h3>Opened on · Brother QL</h3><p class="muted">Opened a jar, bottle or packet? Print the date and stick it on.</p><button id="today" class="primary">Print today's opening date</button><details><summary>Choose another opening date</summary><form id="label"><label>Opened on<input name="date" type="date" class="date" required></label><button>Print this date</button></form></details></section></ha-card>`;
    const storeEditor = this.shadowRoot.querySelector('#edit-store');
    const storeForm = this.shadowRoot.querySelector('#store-form');
    const removeStore = this.shadowRoot.querySelector('#remove-store');
    storeEditor.onchange = () => {
      const name = storeEditor.value;
      storeForm.elements.store.value = name;
      storeForm.elements.store.readOnly = Boolean(name);
      storeForm.elements.loyalty.value = this.storeCards[name] || '';
      storeForm.elements.tag_id.value = this.storeTags[name] || '';
      removeStore.disabled = !name;
    };
    storeForm.onsubmit = async event => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(storeForm));
      if (await this.call('save_store', data)) {
        storeEditor.value = data.store.trim();
        storeEditor.onchange();
      }
    };
    removeStore.onclick = async () => {
      const store = storeEditor.value;
      if (!store || !confirm(`Remove ${store} and its loyalty card? Saved receipts and shopping items will be kept.`)) return;
      if (await this.call('remove_store', {store})) {
        storeEditor.value = '';
        storeEditor.onchange();
      }
    };
    const addForm = this.shadowRoot.querySelector('#add');
    addForm.elements.requester.onchange = () => {
      const custom = addForm.elements.requester.value === '__guest__';
      this.shadowRoot.querySelector('#guest-name').hidden = !custom;
      addForm.elements.guest.disabled = !custom;
      addForm.elements.guest.required = custom;
    };
    for(const [id,operation] of [['add','add_item'],['start','start'],['label','food_label']]) this.shadowRoot.querySelector('#'+id).onsubmit=async e=>{
      e.preventDefault();const form=e.currentTarget;const data=Object.fromEntries(new FormData(form));
      if (operation === 'add_item') {
        if (data.requester === '__guest__') {
          data.requester = data.guest.trim();
          if (!data.requester) {
            form.elements.guest.setCustomValidity('Enter a guest name.');
            form.elements.guest.reportValidity();
            form.elements.guest.setCustomValidity('');
            return;
          }
        }
        delete data.guest;
      }
      if(data.quantity)data.quantity=Number(data.quantity);
      if(await this.call(operation,data)) {if(operation==='add_item'){form.elements.item.value='';form.elements.quantity.value=1;form.elements.item.focus();}}
    };
    // Build today's date in the browser's local zone; backend uses HA's local date when empty.
    this.shadowRoot.querySelector('#label input[name=date]').value='';
    this.shadowRoot.querySelector('#today').onclick=()=>this.call('food_label');
    this.shadowRoot.querySelector('#pair').onclick=async()=>{
      const f=this.shadowRoot.querySelector('#feedback');
      try { const result=await this._hass.callWS({type:'call_service',domain:'household_shopping',service:'request',service_data:{operation:'pair_telegram'},return_response:true});
        f.className='';f.textContent=`Send !link ${result.response.code} to the household Telegram bot within 10 minutes. This connects Telegram to ${result.response.person}.`;
      } catch(e){f.className='error';f.textContent=e.message || String(e);}
    };
    this.ready=true;
  }
  async call(operation,data={}) {
    const feedback=this.shadowRoot.querySelector('#feedback');feedback.className='';feedback.textContent='Working…';
    const buttons=[...this.shadowRoot.querySelectorAll('button')].map(b=>[b,b.disabled]);buttons.forEach(([b])=>b.disabled=true);
    try {await this._hass.callService('household_shopping','request',{operation,...data});feedback.textContent=operation==='add_item'?'Added to the shopping list.':operation==='food_label'?'Opening-date label sent.':'Done.';return true;}
    catch(e){feedback.className='error';feedback.textContent=e.message || String(e);return false;}
    finally{buttons.forEach(([b,disabled])=>b.disabled=disabled);}
  }
  countdown(){for(const el of this.shadowRoot?.querySelectorAll('[data-deadline]') || []){const sec=Math.max(0,Math.ceil((new Date(el.dataset.deadline)-Date.now())/1000));el.textContent=sec?`${Math.floor(sec/60)}:${String(sec%60).padStart(2,'0')} to add items`:'Finalizing…';}}
  renderTrips(trips) {
    const container=this.shadowRoot.querySelector('#trips');
    container.innerHTML='<h3>Trips</h3>' + (trips.length?'':'<p class="muted">No trips yet. Scan a shop tag or tap “I’m going”.</p>') + [...trips].reverse().map(t=>{
      const live=['collecting','sent','attention'].includes(t.status);
      const buttons=t.status==='collecting'?'<button data-op="print_now">Print now</button>':live?'<button data-op="reprint">Reprint all</button><button data-op="finish">Shopping done</button>':'';
      const undo=live?'<button data-op="rollback">Undo whole trip</button>':'';
      const details=(t.jobs || []).map(j=>`<details><summary>${esc(j.store)} · ${esc(j.status)}</summary>${j.items.map(i=>`<div class="item">${esc(i.item)} × ${esc(i.quantity)} · ${esc(i.requester)}</div>`).join('')}${live && !['empty','cancelled'].includes(j.status)?`<div class="row actions"><button data-op="reprint" data-store="${esc(j.store)}">Reprint this shop</button><button data-op="rollback" data-store="${esc(j.store)}">Undo this shop</button></div>`:''}</details>`).join('');
      return `<div class="trip" data-trip="${esc(t.id)}"><strong>${esc(t.shopper)} · ${esc(t.stores.join(' + ') || 'Cancelled')}</strong><p class="muted">${esc(t.status)} · ${esc(new Date(t.created).toLocaleString())}${t.status==='collecting'?`<br><b data-deadline="${esc(t.deadline)}"></b>`:''}</p>${t.error?`<p class="error">${esc(t.error)}</p>`:''}<div class="row actions">${buttons}${undo}</div>${details}</div>`;
    }).join('');
    for(const b of container.querySelectorAll('[data-op]')) b.onclick=()=>{
      const op=b.dataset.op; if(['rollback','reprint','finish'].includes(op)&&!confirm(op==='rollback'?'Restore this trip’s items? This cannot unprint paper.':op==='reprint'?'Print the saved receipt again? Check for an existing copy first.':'Shopping is done? This closes rollback.'))return;
      this.call(op,{trip_id:b.closest('[data-trip]').dataset.trip,store:b.dataset.store || ''});
    };

  }
}
// The integration serves this file automatically; an older manual /local copy
// may load too, so register the element and card-picker entry only once.
if(!customElements.get('household-shopping-card')){
  customElements.define('household-shopping-card',HouseholdShoppingCard);
  window.customCards=window.customCards || [];
  window.customCards.push({type:'household-shopping-card',name:'Household Shopping',description:'Trips, receipts and opening-date labels.'});
}
