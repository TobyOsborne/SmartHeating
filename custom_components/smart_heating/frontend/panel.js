// Smart Heating sidebar panel. Plain web component, no build step.
const TABS = [
  ["now", "Now"], ["profiles", "Profiles"], ["zones", "Zones"],
  ["radiators", "Radiators"], ["learning", "Learning"], ["settings", "Settings"],
];
const MODES = [["auto", "Auto"], ["manual", "Manual"], ["off", "Off"], ["frost", "Frost"]];
const SPARE = [
  ["primary_only", "Primary only", "Only the primary zone's radiators open"],
  ["buffer_zones", "Buffer zones", "Rooms marked as buffers open whenever the boiler runs"],
  ["fill_to_minimum", "Fill to minimum", "Every listed room is heated to its target, in order"],
];
const STATE_LABEL = {
  heating: "Heating", satisfied: "Warm enough", waiting: "Waiting", idle: "Idle",
  window: "Window open", frost: "Frost", minimum: "Below minimum", offline: "Sensor offline", boost: "Boost",
};
const SETTINGS = [
  ["Boiler", [
    ["boiler_min_w", "Minimum boiler load", "W", 1], ["margin_w", "Safety margin", "W", 1],
    ["drop", "Flow-to-return drop", "°C", 1], ["min_on_s", "Minimum on time", "min", 60],
    ["min_off_s", "Minimum off time", "min", 60], ["safety_runtime_s", "Safety runtime alert", "h", 3600],
  ]],
  ["Radiators", [
    ["hold_s", "Hold between changes", "min", 60], ["open_temp", "TRV open setpoint", "°C", 1],
    ["closed_temp", "TRV closed setpoint", "°C", 1], ["default_room_temp", "Assumed room temp", "°C", 1],
  ]],
  ["Comfort & safety", [
    ["goal_band", "Goal band (±)", "°C", 1], ["frost_temp", "Frost protection below", "°C", 1],
    ["frost_release", "Frost release above", "°C", 1], ["sensor_stale_s", "Sensor offline after", "min", 60],
    ["all_satisfied_notify_s", "All-warm notice after", "min", 60],
  ]],
];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
const clone = (o) => JSON.parse(JSON.stringify(o));

class SmartHeatingPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = (location.hash || "#now").slice(1);
    if (!TABS.some(([t]) => t === this._tab)) this._tab = "now";
    this._data = null;
    this._draft = null;
    this._dirty = false;
    this._sel = { profile: null };
    this._boost = {};
    this._error = null;
    this._busy = false;
    this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
    this.shadowRoot.addEventListener("input", (e) => this._onInput(e));
    this.shadowRoot.addEventListener("change", (e) => this._onChange(e));
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) { this._load(); this._timer = setInterval(() => this._load(true), 15000); }
    const st = hass.states["sensor.smart_heating_reason"];
    const stamp = st && st.last_updated;
    if (!first && stamp && stamp !== this._stamp) this._load(true);
    this._stamp = stamp;
  }
  set narrow(v) { this._narrow = v; }
  set panel(v) { this._panel = v; }
  disconnectedCallback() { clearInterval(this._timer); }
  connectedCallback() { if (this._hass && !this._timer) this._timer = setInterval(() => this._load(true), 15000); }

  async _ws(msg) { return this._hass.callWS(msg); }

  async _load(background = false) {
    if (this._loading) return;
    this._loading = true;
    try {
      this._data = await this._ws({ type: "smart_heating/state" });
      this._error = null;
      if (!this._dirty) this._draft = clone(this._data.config);
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._loading = false;
    // Don't redraw editors under the user's cursor
    if (!background || !this._editing()) this._render();
  }
  _editing() { return ["profiles", "zones", "radiators", "settings"].includes(this._tab) && this._dirty; }

  async _set(payload) {
    try { this._data = await this._ws({ type: "smart_heating/set", ...payload }); this._error = null; }
    catch (err) { this._error = err.message; }
    this._render();
  }

  async _save() {
    this._busy = true; this._render();
    try {
      this._data = await this._ws({ type: "smart_heating/save_config", config: this._draft });
      this._draft = clone(this._data.config);
      this._dirty = false; this._error = null;
      this._toast("Saved");
    } catch (err) { this._error = err.message; }
    this._busy = false; this._render();
  }
  _discard() { this._draft = clone(this._data.config); this._dirty = false; this._error = null; this._render(); }
  _touch(rerender = true) { this._dirty = true; if (rerender) this._render(); else this._renderBar(); }
  _toast(text) { this._flash = text; setTimeout(() => { this._flash = null; this._renderBar(); }, 2000); }

  // ------------------------------------------------------------------ events
  _onClick(e) {
    const el = e.target.closest("[data-act]");
    if (!el) return;
    const a = el.dataset.act, i = Number(el.dataset.i), j = Number(el.dataset.j);
    const d = this._draft;
    const prof = d && d.profiles.find((p) => p.name === this._sel.profile);
    switch (a) {
      case "tab": this._tab = el.dataset.tab; history.replaceState(null, "", "#" + this._tab); this._render(); break;
      case "mode": this._set({ mode: el.dataset.v }); break;
      case "dry": this._set({ dry_run: !this._data.runtime.dry_run }); break;
      case "save": this._save(); break;
      case "discard": this._discard(); break;
      case "menu": this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })); break;
      case "boost-open": this._boost[el.dataset.zone] = this._boost[el.dataset.zone] ? null : { target: "", minutes: 60 }; this._render(); break;
      case "boost-go": this._doBoost(el.dataset.zone); break;
      case "boost-cancel": this._hass.callService("smart_heating", "cancel_boost", { zone: el.dataset.zone }).then(() => this._load()); break;
      case "pick-profile": this._sel.profile = el.dataset.name; this._render(); break;
      case "prof-add": {
        const name = this._uniq("New profile", d.profiles);
        d.profiles.push({ name, primary_zone: d.zones[0].name, goal: 18, priority: [], spare_heat: "primary_only", runtime_warning_s: 7200 });
        this._sel.profile = name; this._touch(); break;
      }
      case "prof-dup": {
        const copy = clone(prof); copy.name = this._uniq(prof.name + " copy", d.profiles);
        d.profiles.push(copy); this._sel.profile = copy.name; this._touch(); break;
      }
      case "prof-del":
        if (d.profiles.length < 2) { this._error = "At least one profile is needed"; this._render(); break; }
        if (!confirm(`Delete the ${prof.name} profile?`)) break;
        d.profiles = d.profiles.filter((p) => p !== prof); this._sel.profile = d.profiles[0].name; this._touch(); break;
      case "pri-add": {
        const used = new Set([prof.primary_zone, ...prof.priority.map((e) => e.zone)]);
        const zone = (d.zones.find((z) => !used.has(z.name)) || d.zones[0]).name;
        prof.priority.push({ zone, target: 16, buffer: false }); this._touch(); break;
      }
      case "pri-up": if (i > 0) { [prof.priority[i - 1], prof.priority[i]] = [prof.priority[i], prof.priority[i - 1]]; this._touch(); } break;
      case "pri-down": if (i < prof.priority.length - 1) { [prof.priority[i + 1], prof.priority[i]] = [prof.priority[i], prof.priority[i + 1]]; this._touch(); } break;
      case "pri-del": prof.priority.splice(i, 1); this._touch(); break;
      case "zone-add": d.zones.push({ name: this._uniq("New zone", d.zones), thermometer: "", radiators: [], hysteresis: 0.3, window_detection: true, window_drop: 0.8, window_window_s: 300, damp_alert: true, minimum: null }); this._touch(); break;
      case "zone-del": {
        const z = d.zones[i];
        const users = d.profiles.filter((p) => p.primary_zone === z.name).map((p) => p.name);
        if (users.length) { this._error = `${z.name} is the primary zone of ${users.join(", ")}. Change those first.`; this._render(); break; }
        if (!confirm(`Delete the ${z.name} zone? It will be removed from every profile.`)) break;
        d.profiles.forEach((p) => { p.priority = p.priority.filter((e) => e.zone !== z.name); });
        d.zones.splice(i, 1); this._touch(); break;
      }
      case "zr-toggle": {
        const z = d.zones[i], r = el.dataset.rad;
        z.radiators = z.radiators.includes(r) ? z.radiators.filter((x) => x !== r) : [...z.radiators, r];
        this._touch(); break;
      }
      case "rad-add": d.radiators.push({ name: this._uniq("New radiator", d.radiators), trv: "", rated_w: 1000, exponent: 1.3, enabled: true }); this._touch(); break;
      case "rad-del": {
        const r = d.radiators[i];
        if (!confirm(`Delete the ${r.name} radiator?`)) break;
        d.zones.forEach((z) => { z.radiators = z.radiators.filter((x) => x !== r.name); });
        d.radiators.splice(i, 1); this._touch(); break;
      }
      case "set-profile": this._set({ profile: el.dataset.name }); break;
    }
    void j;
  }

  _onInput(e) {
    const el = e.target;
    if (el.dataset.boost) { this._boost[el.dataset.zone][el.dataset.boost] = el.value; return; }
    if (el.tagName === "SELECT" || el.type === "checkbox") return; // handled on change
    if (el.dataset.f) { this._field(el); this._touch(false); }
  }
  _onChange(e) {
    const el = e.target;
    if (el.dataset.act === "profile") { this._set({ profile: el.value }); return; }
    if (el.dataset.f && (el.tagName === "SELECT" || el.type === "checkbox")) { this._field(el); this._touch(); }
    else if (el.dataset.f && el.dataset.f.endsWith(".name")) this._render(); // refresh names elsewhere
  }

  // Write one input into the draft. data-f is "<kind>.<field>", data-i the index.
  _field(el) {
    const d = this._draft, [kind, f] = el.dataset.f.split("."), i = Number(el.dataset.i);
    let v = el.type === "checkbox" ? el.checked : el.value;
    const scale = Number(el.dataset.scale || 1);
    if (el.type === "number") v = v === "" ? null : Number(v) * scale;
    if (kind === "settings") { d.settings[f] = v; return; }
    if (kind === "profile") {
      const p = d.profiles.find((x) => x.name === this._sel.profile);
      if (f === "name") { this._renameProfile(p, v); return; }
      p[f] = v; return;
    }
    if (kind === "pri") { d.profiles.find((x) => x.name === this._sel.profile).priority[i][f] = v; return; }
    if (kind === "zone") {
      const z = d.zones[i];
      if (f === "name") { this._renameZone(z, v); return; }
      z[f] = v; return;
    }
    if (kind === "rad") {
      const r = d.radiators[i];
      if (f === "name") { d.zones.forEach((z) => { z.radiators = z.radiators.map((x) => (x === r.name ? v : x)); }); }
      r[f] = v;
    }
  }
  _renameProfile(p, v) { p.name = v; this._sel.profile = v; }
  _renameZone(z, v) {
    this._draft.profiles.forEach((p) => {
      if (p.primary_zone === z.name) p.primary_zone = v;
      p.priority.forEach((e) => { if (e.zone === z.name) e.zone = v; });
    });
    z.name = v;
  }
  _uniq(base, list) { let n = base, k = 2; while (list.some((x) => x.name === n)) n = `${base} ${k++}`; return n; }

  async _doBoost(zone) {
    const b = this._boost[zone];
    const data = { zone, position: 0 };
    if (b.target !== "") data.target = Number(b.target);
    if (Number(b.minutes) > 0) data.minutes = Number(b.minutes);
    try { await this._hass.callService("smart_heating", "boost", data); this._boost[zone] = null; }
    catch (err) { this._error = err.message; }
    this._load();
  }

  // ------------------------------------------------------------------ render
  _render() {
    const r = this.shadowRoot;
    const active = r.activeElement;
    const focus = active && active.id;
    const pos = active && active.selectionStart;
    r.innerHTML = `<style>${CSS}</style>
      <div class="toolbar">
        ${this._narrow ? `<button class="icon" data-act="menu" aria-label="Menu">☰</button>` : ""}
        <h1>Heating</h1>
        <div class="spacer"></div>
        ${this._data ? `<span class="pill ${this._data.runtime.dry_run ? "warn" : "ok"}">${this._data.runtime.dry_run ? "Dry run" : "Live"}</span>` : ""}
      </div>
      <nav>${TABS.map(([t, l]) => `<button data-act="tab" data-tab="${t}" class="${t === this._tab ? "on" : ""}">${l}</button>`).join("")}</nav>
      <main>${this._body()}</main>
      <div id="bar"></div>`;
    this._renderBar();
    if (focus) {
      const el = r.getElementById(focus);
      if (el) { el.focus(); try { if (pos != null) el.setSelectionRange(pos, pos); } catch (_) { /* number inputs */ } }
    }
  }
  _renderBar() {
    const bar = this.shadowRoot.getElementById("bar");
    if (!bar) return;
    const err = this._error ? `<div class="error">${esc(this._error)}</div>` : "";
    const save = this._dirty ? `<div class="savebar"><span>Unsaved changes</span>
        <button data-act="discard" class="ghost">Discard</button>
        <button data-act="save" class="primary" ${this._busy ? "disabled" : ""}>${this._busy ? "Saving…" : "Save"}</button></div>` : "";
    const flash = this._flash ? `<div class="flash">${esc(this._flash)}</div>` : "";
    bar.innerHTML = err + save + flash;
  }

  _body() {
    if (!this._data) return this._error ? `<div class="card error">${esc(this._error)}</div>` : `<div class="card">Loading…</div>`;
    return this[`_tab_${this._tab}`]();
  }

  _profile() { return this._data.config.profiles.find((p) => p.name === this._data.runtime.profile); }
  _targets() {
    const p = this._profile(), t = {};
    if (!p) return t;
    p.priority.forEach((e) => { t[e.zone] = { target: e.target, buffer: e.buffer }; });
    t[p.primary_zone] = { target: p.goal, primary: true };
    this._data.runtime.overrides.forEach((o) => { if (o.target != null) t[o.zone] = { ...(t[o.zone] || {}), target: o.target, boost: true }; });
    return t;
  }

  // ---------------------------------------------------------------- Now
  _tab_now() {
    const D = this._data, rt = D.runtime, dec = D.decision, p = this._profile();
    const cap = dec.capacity_kw || 0, need = dec.needed_kw || 0;
    const scale = Math.max(cap, need, 0.1) * 1.15;
    const t = this._targets();
    const order = p ? [p.primary_zone, ...p.priority.map((e) => e.zone).filter((z) => z !== p.primary_zone)] : [];
    const rest = Object.keys(D.zones).filter((z) => !order.includes(z));
    const zoneRow = (z) => {
      const info = D.zones[z], tt = t[z] || {}, b = this._boost[z];
      const zr = (D.config.zones.find((x) => x.name === z) || { radiators: [] }).radiators;
      const zmin = (D.config.zones.find((x) => x.name === z) || {}).minimum;
      const openN = zr.filter((r) => D.radiators[r] && D.radiators[r].open).length;
      return `<div class="row zone">
        <div class="grow"><div class="title">${esc(z)} ${tt.primary ? `<span class="tag">primary</span>` : ""}${tt.buffer ? `<span class="tag">buffer</span>` : ""}${info.boost ? `<span class="tag hot">boost</span>` : ""}</div>
          <div class="sub">${zr.length ? `${openN}/${zr.length} radiators open` : "no radiators"}${zmin != null ? ` · min ${zmin}°` : ""}</div></div>
        <div class="temp">${num(info.temperature)}°<span class="sub"> / ${tt.target != null ? num(tt.target) + "°" : "–"}</span></div>
        <span class="chip ${esc(info.state)}">${esc(STATE_LABEL[info.state] || info.state || "–")}</span>
        ${info.boost ? `<button class="small ghost" data-act="boost-cancel" data-zone="${esc(z)}">End boost</button>`
                     : `<button class="small ghost" data-act="boost-open" data-zone="${esc(z)}">Boost</button>`}
      </div>
      ${b ? `<div class="row boost">
        <label>Target °C <input id="bt-${esc(z)}" type="number" step="0.5" data-boost="target" data-zone="${esc(z)}" value="${esc(b.target)}" placeholder="${tt.target ?? ""}"></label>
        <label>For (min, 0 = until profile changes) <input id="bm-${esc(z)}" type="number" min="0" step="15" data-boost="minutes" data-zone="${esc(z)}" value="${esc(b.minutes)}"></label>
        <button class="primary small" data-act="boost-go" data-zone="${esc(z)}">Start boost</button></div>` : ""}`;
    };
    const rads = Object.entries(D.radiators).sort((a, b) => Number(b[1].open) - Number(a[1].open) || a[0].localeCompare(b[0]));
    return `
      <section class="card">
        <div class="controls">
          <label>Profile <select data-act="profile">${D.config.profiles.map((x) => `<option ${x.name === rt.profile ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
          <div class="seg">${MODES.map(([v, l]) => `<button data-act="mode" data-v="${v}" class="${rt.mode === v ? "on" : ""}">${l}</button>`).join("")}</div>
          <button class="toggle ${rt.dry_run ? "on" : ""}" data-act="dry" role="switch" aria-checked="${rt.dry_run}"><span class="knob"></span>Dry run</button>
        </div>
      </section>
      <section class="card status">
        <div class="boiler ${dec.boiler_on ? "on" : ""}"><div class="big">${dec.boiler_on ? "🔥" : "○"}</div>
          <div><div class="title">Boiler ${dec.boiler_on ? "wanted on" : "off"}</div>
          <div class="sub">Relay: ${esc(D.boiler_actual ?? "unknown")}${rt.dry_run ? " (dry run: not switched)" : ""}</div></div></div>
        <p class="reason">${esc(dec.reason || "")}</p>
        <div class="capbar" title="Open radiator output vs what the boiler needs">
          <div class="fill ${cap >= need ? "ok" : "short"}" style="width:${(cap / scale) * 100}%"></div>
          <div class="mark" style="left:${(need / scale) * 100}%"></div>
        </div>
        <div class="sub caprow"><span>Open capacity <b>${num(cap, 2)} kW</b></span><span>Boiler needs <b>${num(need, 2)} kW</b></span></div>
        <div class="facts"><span>Flow ${num(D.flow_temp, 0)}°C</span><span>Outside ${num(D.outdoor)}°C</span>${p ? `<span>Spare heat: ${esc((SPARE.find((s) => s[0] === p.spare_heat) || [])[1] || p.spare_heat)}</span>` : ""}</div>
      </section>
      <section class="card"><h2>Rooms</h2>${order.map(zoneRow).join("")}
        ${rest.length ? `<h3>Not in this profile</h3>${rest.map(zoneRow).join("")}` : ""}</section>
      <section class="card"><h2>Radiators</h2>${rads.map(([n, r]) => `<div class="row">
          <span class="dot ${r.open ? "open" : ""}"></span>
          <div class="grow"><div class="title">${esc(n)} ${r.faulty ? `<span class="tag bad">not responding</span>` : ""}${r.held ? `<span class="tag">held</span>` : ""}${!r.trv_available ? `<span class="tag bad">TRV offline</span>` : ""}</div>
          <div class="sub">${esc(r.reason || (r.open ? "open" : "closed"))}</div></div>
          <div class="num">${r.open ? num(r.kw, 2) + " kW" : ""}<div class="sub">TRV ${r.trv_setpoint ?? "–"}°</div></div></div>`).join("")}</section>
      ${D.notifications.length ? `<section class="card"><h2>Recent notifications</h2>${D.notifications.map((n) => `<div class="row"><div class="grow"><div class="sub">${n.time ? new Date(n.time * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : ""}${n.sent ? "" : " · not sent (dry run)"}</div>${esc(n.message)}</div></div>`).join("")}</section>` : ""}`;
  }

  // ---------------------------------------------------------------- Profiles
  _tab_profiles() {
    const d = this._draft;
    if (!this._sel.profile || !d.profiles.some((p) => p.name === this._sel.profile)) this._sel.profile = this._data.runtime.profile;
    if (!d.profiles.some((p) => p.name === this._sel.profile)) this._sel.profile = d.profiles[0].name;
    const p = d.profiles.find((x) => x.name === this._sel.profile);
    const zoneOpts = (sel) => d.zones.map((z) => `<option ${z.name === sel ? "selected" : ""}>${esc(z.name)}</option>`).join("");
    return `
      <section class="card">
        <div class="chips">${d.profiles.map((x) => `<button data-act="pick-profile" data-name="${esc(x.name)}" class="${x.name === p.name ? "on" : ""}">${esc(x.name)}${x.name === this._data.runtime.profile ? " ●" : ""}</button>`).join("")}
          <button data-act="prof-add" class="ghost">+ New</button></div>
      </section>
      <section class="card">
        <div class="grid">
          <label>Name <input id="p-name" data-f="profile.name" value="${esc(p.name)}"></label>
          <label>Primary zone <select data-f="profile.primary_zone">${zoneOpts(p.primary_zone)}</select></label>
          <label>Goal °C <input id="p-goal" type="number" step="0.5" data-f="profile.goal" value="${p.goal}"></label>
          <label>Spare heat <select data-f="profile.spare_heat">${SPARE.map(([v, l]) => `<option value="${v}" ${p.spare_heat === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
          <label>Long-run warning (h) <input id="p-rw" type="number" step="0.5" data-scale="3600" data-f="profile.runtime_warning_s" value="${p.runtime_warning_s / 3600}"></label>
        </div>
        <p class="hint">The boiler runs until <b>${esc(p.primary_zone)}</b> reaches ${num(p.goal)}°. ${esc((SPARE.find((s) => s[0] === p.spare_heat) || [])[2] || "")}.</p>
        <h3>Priority list</h3>
        <p class="hint">While the boiler runs, these rooms are opened top-down until there's enough radiator output. A room stops taking heat at its target.</p>
        ${p.priority.length ? "" : `<p class="sub">No other rooms. Only ${esc(p.primary_zone)} will heat.</p>`}
        ${p.priority.map((e, i) => `<div class="row pri">
          <span class="idx">${i + 1}</span>
          <select data-f="pri.zone" data-i="${i}">${zoneOpts(e.zone)}</select>
          <label class="inline">Target <input id="pt-${i}" type="number" step="0.5" data-f="pri.target" data-i="${i}" value="${e.target}"></label>
          <label class="inline"><input type="checkbox" data-f="pri.buffer" data-i="${i}" ${e.buffer ? "checked" : ""}> Buffer</label>
          <span class="grow"></span>
          <button class="icon" data-act="pri-up" data-i="${i}" title="Move up">↑</button>
          <button class="icon" data-act="pri-down" data-i="${i}" title="Move down">↓</button>
          <button class="icon" data-act="pri-del" data-i="${i}" title="Remove">✕</button></div>`).join("")}
        <div class="actions">
          <button class="ghost" data-act="pri-add">+ Add room</button><span class="grow"></span>
          ${p.name !== this._data.runtime.profile && !this._dirty ? `<button class="ghost" data-act="set-profile" data-name="${esc(p.name)}">Use now</button>` : ""}
          <button class="ghost" data-act="prof-dup">Duplicate</button>
          <button class="ghost danger" data-act="prof-del">Delete</button></div>
      </section>`;
  }

  // ---------------------------------------------------------------- Zones
  _tab_zones() {
    const d = this._draft, temps = this._data.entities.temperature;
    return `<datalist id="temps">${temps.map((e) => `<option value="${esc(e)}">`).join("")}</datalist>
      ${d.zones.map((z, i) => `<section class="card">
        <div class="grid">
          <label>Name <input id="z-n-${i}" data-f="zone.name" data-i="${i}" value="${esc(z.name)}"></label>
          <label>Thermometer <input id="z-t-${i}" list="temps" data-f="zone.thermometer" data-i="${i}" value="${esc(z.thermometer)}"></label>
          <label>Hysteresis °C <input id="z-h-${i}" type="number" step="0.1" data-f="zone.hysteresis" data-i="${i}" value="${z.hysteresis}"></label>
          <label>Minimum °C (blank = none) <input id="z-m-${i}" type="number" step="0.5" data-f="zone.minimum" data-i="${i}" value="${z.minimum ?? ""}" placeholder="none"></label>
        </div>
        <div class="sub">Reading now: ${num(this._data.zones[z.name]?.temperature)}°C${z.minimum != null ? ` · kept at ${z.minimum}° or above whenever the boiler runs, in every profile` : ""}</div>
        <h3>Radiators</h3>
        <div class="chips">${d.radiators.map((r) => `<button data-act="zr-toggle" data-i="${i}" data-rad="${esc(r.name)}" class="${z.radiators.includes(r.name) ? "on" : ""}">${esc(r.name)}</button>`).join("")}</div>
        <h3>Windows & damp</h3>
        <div class="grid">
          <label class="inline"><input type="checkbox" data-f="zone.window_detection" data-i="${i}" ${z.window_detection ? "checked" : ""}> Detect open windows</label>
          <label>Drop of °C <input id="z-wd-${i}" type="number" step="0.1" data-f="zone.window_drop" data-i="${i}" value="${z.window_drop}"></label>
          <label>Within (min) <input id="z-ww-${i}" type="number" step="1" data-scale="60" data-f="zone.window_window_s" data-i="${i}" value="${z.window_window_s / 60}"></label>
          <label class="inline"><input type="checkbox" data-f="zone.damp_alert" data-i="${i}" ${z.damp_alert ? "checked" : ""}> Damp alerts</label>
        </div>
        <div class="actions"><span class="grow"></span><button class="ghost danger" data-act="zone-del" data-i="${i}">Delete zone</button></div>
      </section>`).join("")}
      <div class="actions"><button class="ghost" data-act="zone-add">+ Add zone</button></div>
      <p class="hint">A room's minimum applies in every profile: whenever the boiler is running, rooms below their minimum open first, coldest first. A minimum never starts the boiler on its own (frost protection does that).</p>
      <p class="hint">Renaming a zone starts its learning afresh.</p>`;
  }

  // ---------------------------------------------------------------- Radiators
  _tab_radiators() {
    const d = this._draft, live = this._data.radiators;
    return `<datalist id="trvs">${this._data.entities.climate.map((e) => `<option value="${esc(e)}">`).join("")}</datalist>
      <section class="card"><p class="hint">Rated output is the radiator's watts at ΔT50. Output now is modelled at ${num(this._data.flow_temp, 0)}°C flow.</p>
      ${d.radiators.map((r, i) => `<div class="row rad">
        <input id="r-n-${i}" class="name" data-f="rad.name" data-i="${i}" value="${esc(r.name)}" aria-label="Name">
        <input id="r-t-${i}" list="trvs" data-f="rad.trv" data-i="${i}" value="${esc(r.trv)}" aria-label="TRV">
        <label class="inline">W <input id="r-w-${i}" type="number" step="10" data-f="rad.rated_w" data-i="${i}" value="${r.rated_w}"></label>
        <label class="inline">n <input id="r-e-${i}" type="number" step="0.05" data-f="rad.exponent" data-i="${i}" value="${r.exponent}"></label>
        <label class="inline"><input type="checkbox" data-f="rad.enabled" data-i="${i}" ${r.enabled ? "checked" : ""}> On</label>
        <span class="sub">${live[r.name] ? num(live[r.name].kw, 2) + " kW" : ""}</span>
        <button class="icon" data-act="rad-del" data-i="${i}" title="Delete">✕</button></div>`).join("")}
      <div class="actions"><button class="ghost" data-act="rad-add">+ Add radiator</button></div></section>`;
  }

  // ---------------------------------------------------------------- Learning
  _tab_learning() {
    const L = this._data.learning, zones = Object.keys(L);
    const bar = (c) => `<div class="conf"><div style="width:${Math.max(0, Math.min(100, c))}%" class="${c >= 70 ? "hi" : c >= 40 ? "mid" : "lo"}"></div></div>`;
    const ago = (t) => {
      if (!t) return "never";
      if (typeof t === "string") return t.replace(/^Pi /, "") + (t.startsWith("Pi ") ? " (from the Pi)" : "");
      const m = (Date.now() / 1000 - t) / 60;
      return m < 90 ? `${Math.round(m)} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`;
    };
    return `<section class="card"><p class="hint">Each room learns two things. <b>Cooling</b>: how fast it loses heat when nothing is heating it (half-life), and how much warmer than outside it settles from people, appliances and sun (free heat). <b>Warm-up</b>: how many degrees an hour it gains per kW of open radiator. Only quiet hours teach cooling and only heated hours teach warm-up, so confidence grows over days.</p></section>
      <div class="cards">${zones.map((z) => {
        const l = L[z];
        return `<section class="card learn">
          <h2>${esc(z)}</h2>
          <div class="lrow"><span>Cooling confidence</span><b>${l.confidence}%</b></div>${bar(l.confidence)}
          <dl>
            <dt>Half-life</dt><dd>${num(l.half_life_h)} h <span class="sub">(×/÷ ${num(l.half_life_factor, 2)})</span></dd>
            <dt>Free heat</dt><dd>+${num(l.free_heat)}° <span class="sub">(± ${num(l.free_heat_sd)})</span></dd>
            <dt>Settles at</dt><dd>${l.settling_temp != null ? num(l.settling_temp) + "°C" : "–"} <span class="sub">at today's outside temp</span></dd>
            <dt>Cooling now</dt><dd>${l.cooling_now != null ? num(l.cooling_now, 2) + " °C/h" : "–"}</dd>
            <dt>Samples</dt><dd>${num(l.samples, 0)} <span class="sub">last ${esc(ago(l.last_sample))}</span></dd>
          </dl>
          <div class="lrow"><span>Warm-up confidence</span><b>${Math.round((l.warmup_confidence || 0) * 100)}%</b></div>${bar((l.warmup_confidence || 0) * 100)}
          <dl><dt>Warm-up</dt><dd>${l.warmup_per_kw != null ? num(l.warmup_per_kw, 2) + " °C/h per kW" : "still learning"}</dd>
            <dt>Samples</dt><dd>${num(l.warmup_samples, 0)}</dd></dl>
        </section>`;
      }).join("")}</div>`;
  }

  // ---------------------------------------------------------------- Settings
  _tab_settings() {
    const s = this._draft.settings;
    return `${SETTINGS.map(([group, rows]) => `<section class="card"><h2>${group}</h2><div class="grid">
        ${rows.map(([k, label, unit, scale]) => `<label>${label} (${unit}) <input id="s-${k}" type="number" step="any" data-scale="${scale}" data-f="settings.${k}" value="${s[k] / scale}"></label>`).join("")}
      </div></section>`).join("")}
      <section class="card"><h2>Sensors</h2><div class="grid">
        <label>Outdoor temperature <input id="s-out" list="temps" data-f="settings.outdoor_sensor" value="${esc(s.outdoor_sensor)}"></label>
      </div><datalist id="temps">${this._data.entities.temperature.map((e) => `<option value="${esc(e)}">`).join("")}</datalist>
      <p class="hint">Boiler relay, flow temperature and notifications are set in Settings → Devices & services → Smart Heating → Configure.</p></section>`;
  }
}

const CSS = `
:host { display:block; min-height:100vh; background:var(--primary-background-color); color:var(--primary-text-color); font-family:var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
* { box-sizing:border-box; }
.toolbar { display:flex; align-items:center; gap:12px; height:56px; padding:0 16px; background:var(--app-header-background-color, var(--primary-color)); color:var(--app-header-text-color, #fff); }
.toolbar h1 { font-size:20px; font-weight:400; margin:0; }
.spacer, .grow { flex:1; }
nav { display:flex; gap:4px; padding:8px 16px 0; overflow-x:auto; border-bottom:1px solid var(--divider-color); background:var(--card-background-color); }
nav button { background:none; border:none; border-bottom:2px solid transparent; padding:10px 12px; color:var(--secondary-text-color); font-size:14px; cursor:pointer; white-space:nowrap; }
nav button.on { color:var(--primary-color); border-bottom-color:var(--primary-color); }
main { max-width:1000px; margin:0 auto; padding:16px 16px 96px; }
.card { background:var(--card-background-color); border-radius:var(--ha-card-border-radius, 12px); box-shadow:var(--ha-card-box-shadow, none); border:1px solid var(--divider-color); padding:16px; margin-bottom:16px; }
.cards { display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:16px; }
.cards .card { margin:0; }
h2 { font-size:16px; font-weight:500; margin:0 0 12px; }
h3 { font-size:13px; font-weight:500; text-transform:uppercase; letter-spacing:.04em; color:var(--secondary-text-color); margin:16px 0 8px; }
.sub, .hint { color:var(--secondary-text-color); font-size:12.5px; }
.hint { margin:8px 0; line-height:1.45; }
.title { font-weight:500; }
.row { display:flex; align-items:center; gap:10px; padding:10px 0; border-top:1px solid var(--divider-color); flex-wrap:wrap; }
h2 + .row, h3 + .row, .hint + .row, .sub + .row { border-top:none; }
.row.boost { background:var(--secondary-background-color); padding:10px; border-radius:8px; border:none; }
.temp { font-size:20px; min-width:90px; text-align:right; }
.num { text-align:right; min-width:70px; }
button { font:inherit; cursor:pointer; border-radius:8px; }
button.primary { background:var(--primary-color); color:var(--text-primary-color, #fff); border:none; padding:8px 16px; }
button.ghost { background:none; border:1px solid var(--divider-color); color:var(--primary-text-color); padding:6px 12px; }
button.danger { color:var(--error-color); }
button.small { padding:4px 10px; font-size:13px; }
button.icon { background:none; border:none; color:inherit; font-size:18px; padding:4px 8px; }
button:disabled { opacity:.5; }
input, select { font:inherit; color:var(--primary-text-color); background:var(--secondary-background-color, transparent); border:1px solid var(--divider-color); border-radius:6px; padding:6px 8px; min-width:0; }
input[type=number] { width:90px; }
input[type=checkbox] { width:auto; accent-color:var(--primary-color); }
label { display:flex; flex-direction:column; gap:4px; font-size:12.5px; color:var(--secondary-text-color); }
label.inline { flex-direction:row; align-items:center; gap:6px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(200px, 1fr)); gap:12px; align-items:end; }
.grid input:not([type=checkbox]), .grid select { width:100%; }
.controls { display:flex; flex-wrap:wrap; gap:16px; align-items:end; }
.seg { display:inline-flex; border:1px solid var(--divider-color); border-radius:8px; overflow:hidden; }
.seg button { border:none; border-radius:0; background:none; color:var(--primary-text-color); padding:8px 14px; }
.seg button.on { background:var(--primary-color); color:var(--text-primary-color, #fff); }
.toggle { display:inline-flex; align-items:center; gap:8px; background:none; border:none; color:var(--primary-text-color); padding:6px 0; }
.toggle .knob { width:34px; height:18px; border-radius:9px; background:var(--disabled-text-color, #bbb); position:relative; transition:background .15s; }
.toggle .knob::after { content:""; position:absolute; top:2px; left:2px; width:14px; height:14px; border-radius:50%; background:#fff; transition:left .15s; }
.toggle.on .knob { background:var(--warning-color, #ff9800); }
.toggle.on .knob::after { left:18px; }
.pill { font-size:12px; padding:3px 10px; border-radius:12px; background:rgba(255,255,255,.2); }
.pill.warn { background:var(--warning-color, #ff9800); color:#000; }
.tag { font-size:11px; padding:1px 6px; border-radius:8px; background:var(--secondary-background-color); color:var(--secondary-text-color); margin-left:4px; font-weight:400; border:1px solid var(--divider-color); }
.tag.hot { background:var(--state-climate-heat-color, #ff8100); color:#fff; border:none; }
.tag.bad { background:var(--error-color); color:#fff; border:none; }
.chip { font-size:12px; padding:3px 10px; border-radius:12px; background:var(--secondary-background-color); white-space:nowrap; }
.chip.heating, .chip.boost, .chip.minimum { background:var(--state-climate-heat-color, #ff8100); color:#fff; }
.chip.satisfied { background:var(--success-color, #43a047); color:#fff; }
.chip.window, .chip.offline, .chip.frost { background:var(--error-color); color:#fff; }
.chips { display:flex; flex-wrap:wrap; gap:8px; }
.chips button { background:none; border:1px solid var(--divider-color); color:var(--primary-text-color); padding:6px 12px; border-radius:16px; }
.chips button.on { background:var(--primary-color); color:var(--text-primary-color, #fff); border-color:var(--primary-color); }
.status .boiler { display:flex; gap:12px; align-items:center; }
.status .big { font-size:32px; width:44px; text-align:center; opacity:.5; }
.status .boiler.on .big { opacity:1; }
.reason { margin:12px 0; line-height:1.4; }
.capbar { position:relative; height:12px; border-radius:6px; background:var(--secondary-background-color); overflow:visible; }
.capbar .fill { height:100%; border-radius:6px; }
.capbar .fill.ok { background:var(--success-color, #43a047); }
.capbar .fill.short { background:var(--warning-color, #ff9800); }
.capbar .mark { position:absolute; top:-4px; bottom:-4px; width:2px; background:var(--primary-text-color); }
.caprow { display:flex; justify-content:space-between; margin-top:6px; }
.facts { display:flex; flex-wrap:wrap; gap:16px; margin-top:12px; font-size:13px; color:var(--secondary-text-color); }
.dot { width:10px; height:10px; border-radius:50%; background:var(--disabled-text-color, #bbb); }
.dot.open { background:var(--state-climate-heat-color, #ff8100); }
.idx { width:22px; color:var(--secondary-text-color); }
.row.rad input.name { width:170px; }
.row.rad input[list] { width:230px; }
.actions { display:flex; gap:8px; margin-top:12px; align-items:center; }
.conf { height:8px; border-radius:4px; background:var(--secondary-background-color); overflow:hidden; margin:4px 0 10px; }
.conf div { height:100%; }
.conf .hi { background:var(--success-color, #43a047); } .conf .mid { background:var(--warning-color, #ff9800); } .conf .lo { background:var(--error-color, #db4437); }
.lrow { display:flex; justify-content:space-between; font-size:13px; }
dl { display:grid; grid-template-columns:auto 1fr; gap:4px 12px; margin:0 0 12px; font-size:13.5px; }
dt { color:var(--secondary-text-color); } dd { margin:0; }
#bar { position:fixed; bottom:16px; left:50%; transform:translateX(-50%); display:flex; flex-direction:column; gap:8px; align-items:center; z-index:5; width:min(600px, calc(100% - 32px)); }
.savebar, .error, .flash { display:flex; gap:12px; align-items:center; padding:10px 16px; border-radius:10px; width:100%; box-shadow:0 2px 8px rgba(0,0,0,.25); }
.savebar { background:var(--card-background-color); border:1px solid var(--divider-color); }
.savebar span { flex:1; }
.error { background:var(--error-color); color:#fff; }
.flash { background:var(--success-color, #43a047); color:#fff; justify-content:center; }
.card.error { position:static; box-shadow:none; }
@media (max-width:600px) { .temp { min-width:auto; font-size:17px; } .row.zone .grow { min-width:45%; } main { padding:12px 8px 96px; } }
`;

customElements.define("smart-heating-panel", SmartHeatingPanel);
