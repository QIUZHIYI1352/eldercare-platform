/* 智慧养老 · 辅助监管与培训系统 前端逻辑 */
const App = {
  state: { token: null, user: null, view: "dashboard", data: {} },
  roleNames: {
    admin: "管理员", nursing_student: "护理大学生",
    long_term_caregiver: "长期护理员", elderly_caregiver: "养老护理员",
    elderly_service_teacher: "养老服务师",
  },

  // ---------- API ----------
  async api(path, method = "GET", body = null) {
    const opts = { method, headers: {} };
    if (this.state.token) opts.headers["Authorization"] = "Bearer " + this.state.token;
    if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const res = await fetch(path, opts);
    if (res.status === 401) { this.forceLogout(); throw new Error("未登录或会话过期"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "请求失败");
    return data;
  },

  // ---------- 登录 ----------
  async login() {
    const username = document.getElementById("login-username").value.trim();
    const password = document.getElementById("login-password").value;
    const msg = document.getElementById("login-msg");
    msg.textContent = "";
    try {
      const data = await this.api("/api/auth/login", "POST", { username, password });
      this.state.token = data.token;
      this.state.user = data.user;
      this.showApp();
    } catch (e) { msg.textContent = e.message; }
  },
  showRegister() {
    this.openModal(`
      <h3>注册账号（自助开户）</h3>
      <div class="form-row"><label>姓名</label><input id="r-name"></div>
      <div class="form-row"><label>账号（至少 3 位）</label><input id="r-username"></div>
      <div class="form-row"><label>密码（至少 6 位）</label><input id="r-password" type="password"></div>
      <div class="form-row"><label>角色</label><select id="r-role">
        ${Object.entries(this.roleNames).filter(([k]) => k !== "admin").map(([k, v]) => `<option value="${k}" ${k === "elderly_caregiver" ? "selected" : ""}>${v}</option>`).join("")}
      </select></div>
      <div class="form-row"><label>机构（可选）</label><input id="r-org"></div>
      <div class="form-row"><label>手机号（可选）</label><input id="r-phone"></div>
      <p id="r-msg" class="msg"></p>
      <div class="modal-actions">
        <button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn btn-primary" onclick="App.register()">注册并登录</button>
      </div>`);
  },
  async register() {
    const msg = document.getElementById("r-msg");
    msg.textContent = "";
    const payload = {
      name: document.getElementById("r-name").value.trim(),
      username: document.getElementById("r-username").value.trim(),
      password: document.getElementById("r-password").value,
      role: document.getElementById("r-role").value,
      organization: document.getElementById("r-org").value.trim(),
      phone: document.getElementById("r-phone").value.trim(),
    };
    try {
      const data = await this.api("/api/auth/register", "POST", payload);
      this.state.token = data.token;
      this.state.user = data.user;
      this.closeModal();
      this.showApp();
    } catch (e) { msg.textContent = e.message; }
  },
  async logout() {
    try { await this.api("/api/auth/logout", "POST"); } catch (e) {}
    this.forceLogout();
  },
  forceLogout() {
    this.state.token = null; this.state.user = null;
    document.getElementById("login-view").classList.remove("hidden");
    document.getElementById("app-view").classList.add("hidden");
  },
  showApp() {
    document.getElementById("login-view").classList.add("hidden");
    document.getElementById("app-view").classList.remove("hidden");
    this.renderNav();
    this.go("dashboard");
    this.checkVision();
  },
  async checkVision() {
    try {
      const h = await this.api("/api/health");
      const el = document.getElementById("topbar-status");
      if (el) {
        if (h.vision_available) {
          el.className = "status-dot online";
          el.textContent = "视觉引擎已就绪";
        } else {
          el.className = "status-dot offline";
          el.textContent = "视觉引擎不可用（未装 cv2/mediapipe）";
        }
      }
    } catch (e) {}
  },

  // ---------- 导航 ----------
  navItems() {
    const isAdmin = this.state.user && this.state.user.role === "admin";
    const base = [
      { key: "dashboard", ico: "📊", label: "仪表盘" },
      { key: "processes", ico: "📋", label: "护理流程" },
      { key: "actions",   ico: "🎯", label: "动作模板" },
      { key: "devices",   ico: "📹", label: "监控设备" },
      { key: "monitoring",ico: "👁️", label: "实时监管" },
      { key: "training",  ico: "🎓", label: "培训记录" },
    ];
    if (isAdmin) {
      base.push({ key: "users", ico: "👥", label: "用户管理" });
      base.push({ key: "privacy", ico: "🔐", label: "隐私安全" });
    }
    return base;
  },
  renderNav() {
    const nav = document.getElementById("nav");
    nav.innerHTML = this.navItems().map(i =>
      `<div class="nav-item" data-view="${i.key}" onclick="App.go('${i.key}')">
        <span class="ico">${i.ico}</span><span>${i.label}</span></div>`).join("");
    const ub = document.getElementById("user-badge");
    ub.innerHTML = `<div class="uname">${this.state.user.name}</div>
      <div class="urole">${this.roleNames[this.state.user.role] || this.state.user.role}</div>`;
  },
  go(view) {
    this.state.view = view;
    if (view !== "monitoring" && this.state.monSession) this.stopMonitor(true);
    document.querySelectorAll(".nav-item").forEach(el =>
      el.classList.toggle("active", el.dataset.view === view));
    const titles = { dashboard: "仪表盘", processes: "护理流程管理", actions: "动作模板管理",
      devices: "监控设备管理", monitoring: "实时监管", training: "培训记录",
      users: "用户管理", privacy: "隐私安全" };
    document.getElementById("page-title").textContent = titles[view] || view;
    const views = {
      dashboard: () => this.renderDashboard(), processes: () => this.renderProcesses(),
      actions: () => this.renderActions(), devices: () => this.renderDevices(),
      monitoring: () => this.renderMonitoring(), training: () => this.renderTraining(),
      users: () => this.renderUsers(), privacy: () => this.renderPrivacy(),
    };
    (views[view] || (() => {}))();
  },

  // ---------- 通用 ----------
  esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); },
  openModal(html) {
    document.getElementById("modal-body").innerHTML = html;
    document.getElementById("modal-mask").classList.remove("hidden");
  },
  closeModal() { document.getElementById("modal-mask").classList.add("hidden"); },
  emptyHtml(txt) { return `<div class="empty">${txt || "暂无数据"}</div>`; },

  // ---------- 仪表盘 ----------
  async renderDashboard() {
    const c = document.getElementById("content");
    c.innerHTML = `<div class="banner banner-info">
      🎯 系统核心能力：① 通过 cv2 + mediapipe 实时识别护理操作动作，比对标准流程，<b>提醒「哪一步漏掉了」</b>；
      ② 数据本地处理、敏感信息脱敏、分级权限、审计日志，<b>优化隐私安全监管能力</b>。</div>
      <div class="grid grid-4" id="stats"></div>
      <div class="grid grid-2" style="margin-top:16px">
        <div class="card"><div class="card-head"><h3>最近培训记录</h3></div><div id="recent-training"></div></div>
        <div class="card"><div class="card-head"><h3>快速开始</h3></div><div id="quick-start"></div></div>
      </div>`;
    const [procs, acts, devs, trains] = await Promise.all([
      this.api("/api/processes"), this.api("/api/actions"),
      this.api("/api/devices"), this.api("/api/training/records"),
    ]);
    const stats = [
      { n: procs.items.length, l: "护理流程", i: "📋" },
      { n: acts.items.length, l: "识别动作", i: "🎯" },
      { n: devs.items.length, l: "监控设备", i: "📹" },
      { n: trains.items.length, l: "培训记录", i: "🎓" },
    ];
    document.getElementById("stats").innerHTML = stats.map(s =>
      `<div class="stat"><span class="ico">${s.i}</span><div class="num">${s.n}</div><div class="lbl">${s.l}</div></div>`).join("");
    const rt = document.getElementById("recent-training");
    rt.innerHTML = trains.items.length
      ? trains.items.slice(0, 6).map(t => `<table><tr>
          <td>${this.esc(t.process_name || "-")}</td>
          <td>${this.esc(t.user_name || "-")}</td>
          <td><span class="tag ${t.score >= 80 ? "tag-green" : t.score >= 60 ? "tag-amber" : "tag-red"}">${t.score}分</span></td>
        </tr></table>`).join("")
      : this.emptyHtml("暂无培训记录");
    document.getElementById("quick-start").innerHTML = `
      <ol style="padding-left:18px;line-height:2.1">
        <li>在「动作模板」中<b>自主添加</b>要识别的动作（关节角度规则）</li>
        <li>在「护理流程」中编排标准步骤并关联动作</li>
        <li>在「监控设备」中<b>添加监控设备</b></li>
        <li>运行 <code>python monitor.py</code> 启动摄像头实时识别</li>
        <li>系统实时比对流程，<b>漏步即时提醒</b></li>
      </ol>`;
  },

  // ---------- 护理流程 ----------
  async renderProcesses() {
    const c = document.getElementById("content");
    const data = await this.api("/api/processes");
    c.innerHTML = `<div class="card">
      <div class="card-head"><h3>标准护理流程（培训标准）</h3>
        <button class="btn btn-primary" onclick="App.editProcess()">+ 新建流程</button></div>
      <div id="process-list"></div></div>`;
    const list = document.getElementById("process-list");
    if (!data.items.length) { list.innerHTML = this.emptyHtml("暂无流程，点击右上角新建"); return; }
    list.innerHTML = data.items.map(p => `
      <div class="card" style="box-shadow:none;border:1px solid var(--border)">
        <div class="card-head">
          <h3>${this.esc(p.name)} <span class="tag tag-blue">${this.esc(p.category)}</span>
            <span class="tag tag-gray">${p.steps_count} 步</span></h3>
          <div>
            <button class="btn btn-sm" onclick="App.viewProcess('${p.id}')">查看步骤</button>
            <button class="btn btn-sm btn-primary" onclick="App.editProcess('${p.id}')">编辑</button>
            <button class="btn btn-sm btn-danger" onclick="App.delProcess('${p.id}')">删除</button>
          </div></div>
        <p style="color:var(--muted);font-size:13px">${this.esc(p.description || "")}</p>
      </div>`).join("");
  },
  async viewProcess(pid) {
    const p = await this.api("/api/processes/" + pid);
    const steps = p.steps.map(s => `
      <div class="step-item"><span class="order">${s.order}</span>
        <span>${this.esc(s.name)}</span></div>`).join("") || this.emptyHtml("暂无步骤");
    this.openModal(`<h3>${this.esc(p.name)} · 操作步骤</h3>
      <p style="color:var(--muted);margin-bottom:12px">${this.esc(p.description || "")}</p>
      <div class="step-list">${steps}</div>
      <div class="modal-actions"><button class="btn" onclick="App.closeModal()">关闭</button></div>`);
  },
  async editProcess(pid) {
    let p = { name: "", category: "基础护理", description: "", steps: [] };
    let actions = [];
    if (pid) p = await this.api("/api/processes/" + pid);
    const ares = await this.api("/api/actions");
    actions = ares.items;
    const actionOpts = actions.map(a => `<option value="${a.id}">${this.esc(a.name)}</option>`).join("");
    const stepRows = (p.steps || []).map(s => `
      <div class="step-item">
        <span class="order">${s.order}</span>
        <input style="flex:1;padding:8px;border:1px solid var(--border);border-radius:6px" value="${this.esc(s.name)}" data-role="step-name">
        <select style="padding:8px;border:1px solid var(--border);border-radius:6px" data-role="step-action">
          ${actionOpts.replace(`value="${s.action_id}"`, `value="${s.action_id}" selected`)}
        </select>
        <button class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">×</button>
      </div>`).join("");
    this.openModal(`
      <h3>${pid ? "编辑" : "新建"}护理流程</h3>
      <div class="form-row"><label>流程名称</label><input id="p-name" value="${this.esc(p.name)}"></div>
      <div class="form-row"><label>分类</label><input id="p-cat" value="${this.esc(p.category)}"></div>
      <div class="form-row"><label>描述</label><textarea id="p-desc">${this.esc(p.description)}</textarea></div>
      <div class="form-row"><label>操作步骤（顺序执行，每步关联一个可识别动作）</label>
        <div id="p-steps" class="step-list">${stepRows}</div>
        <button class="btn btn-sm" style="margin-top:8px" onclick="App.addStepRow()">+ 添加步骤</button>
      </div>
      <div class="modal-actions">
        <button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn btn-primary" onclick="App.saveProcess('${pid || ""}', '${this.esc(p.category)}')">保存</button>
      </div>`);
    window._actionOpts = actionOpts;
  },
  addStepRow() {
    const box = document.getElementById("p-steps");
    const order = box.querySelectorAll(".step-item").length + 1;
    const div = document.createElement("div");
    div.className = "step-item";
    div.innerHTML = `<span class="order">${order}</span>
      <input style="flex:1;padding:8px;border:1px solid var(--border);border-radius:6px" data-role="step-name" placeholder="步骤名称">
      <select style="padding:8px;border:1px solid var(--border);border-radius:6px" data-role="step-action">${window._actionOpts}</select>
      <button class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">×</button>`;
    box.appendChild(div);
  },
  async saveProcess(pid) {
    const steps = [...document.querySelectorAll("#p-steps .step-item")].map((el, i) => ({
      order: i + 1,
      name: el.querySelector('[data-role="step-name"]').value.trim() || ("步骤" + (i + 1)),
      action_id: el.querySelector('[data-role="step-action"]').value,
    }));
    if (!steps.length) { alert("请至少添加一个步骤"); return; }
    const payload = {
      name: document.getElementById("p-name").value.trim(),
      category: document.getElementById("p-cat").value.trim() || "基础护理",
      description: document.getElementById("p-desc").value.trim(), steps,
    };
    if (!payload.name) { alert("请填写流程名称"); return; }
    try {
      if (pid) await this.api("/api/processes/" + pid, "PUT", payload);
      else await this.api("/api/processes", "POST", payload);
      this.closeModal(); this.go("processes");
    } catch (e) { alert(e.message); }
  },
  async delProcess(pid) {
    if (!confirm("确认删除该流程？")) return;
    await this.api("/api/processes/" + pid, "DELETE");
    this.go("processes");
  },

  // ---------- 动作模板 ----------
  async renderActions() {
    const c = document.getElementById("content");
    const [data, fields] = await Promise.all([
      this.api("/api/actions"), this.api("/api/actions/fields")]);
    this.state._fields = fields.items;
    c.innerHTML = `<div class="banner banner-info">🎯 自主添加识别动作，支持两类模板：
      <b>① 规则判定</b>（关节特征 + 阈值 + 持续时间）与 <b>② 骨骼序列模板</b>（DTW 精细匹配过程性动作）。
      序列模板可用 <code>python record_template.py --name "动作名"</code> 录制。特征字段见下表。</div>
      <div class="card"><div class="card-head"><h3>可识别动作模板</h3>
        <button class="btn btn-primary" onclick="App.editAction()">+ 添加识别动作</button></div>
      <table><thead><tr><th>动作名称</th><th>类型</th><th>分类</th><th>判定规则 / 模板</th><th>持续(秒)</th><th>操作</th></tr></thead>
        <tbody id="action-tbody"></tbody></table></div>
      <div class="card"><div class="card-head"><h3>可引用关节特征字段</h3></div>
      <table><thead><tr><th>字段 key</th><th>含义</th><th>说明</th></tr></thead><tbody>
        ${fields.items.map(f => `<tr><td><code>${f.key}</code></td><td>${this.esc(f.label)}</td><td>${this.esc(f.note)}</td></tr>`).join("")}
      </tbody></table></div>`;
    const tb = document.getElementById("action-tbody");
    tb.innerHTML = data.items.length ? data.items.map(a => {
      const isSeq = a.template_type === "sequence";
      const rule = isSeq ? `骨骼序列 ${a.template_frames} 帧 · 阈值 ${a.template_threshold}`
        : (a.conditions || []).map(cd => `${cd.joint} ${cd.op} ${cd.value}`).join("<br>");
      return `<tr><td><b>${this.esc(a.name)}</b></td>
        <td><span class="tag ${isSeq ? "tag-amber" : "tag-blue"}">${isSeq ? "序列(DTW)" : "规则"}</span></td>
        <td>${this.esc(a.category)}</td><td>${rule}</td><td>${a.duration}</td>
        <td><button class="btn btn-sm btn-primary" onclick="App.editAction('${a.id}')">编辑</button>
            <button class="btn btn-sm btn-danger" onclick="App.delAction('${a.id}')">删除</button></td></tr>`;
    }).join("")
      : `<tr><td colspan="6">${this.emptyHtml("暂无动作，点击右上角添加")}</td></tr>`;
  },
  editAction(aid) {
    if (aid) return this._loadAction(aid);
    this._actionForm({ name: "", category: "通用", description: "", duration: 1.0,
      conditions: [], template_type: "rule", template_data: {} });
  },
  async _loadAction(aid) {
    const a = await this.api("/api/actions/" + aid);
    this._actionForm(a);
  },
  _actionForm(a) {
    const fields = this.state._fields || [];
    const fieldOpts = fields.map(f => `<option value="${f.key}">${f.key} (${f.label})</option>`).join("");
    const ttype = a.template_type || "rule";
    const isSeq = ttype === "sequence";
    const td = a.template_data || {};
    const frames = (td.vectors || []).length || (a.template_frames || 0);
    const threshold = td.threshold != null ? td.threshold : (a.template_threshold || 8.0);
    window._actionTemplateData = td;
    const condRows = (a.conditions || []).map(cd => `
      <div class="cond-row">
        <select data-role="joint">${fieldOpts.replace(`value="${cd.joint}"`, `value="${cd.joint}" selected`)}</select>
        <select data-role="op" style="flex:0 0 80px">
          ${[">=", "<=", ">", "<", "==", "!="].map(o => `<option ${o === cd.op ? "selected" : ""}>${o}</option>`).join("")}
        </select>
        <input type="number" step="0.1" data-role="value" value="${cd.value}">
        <button class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">×</button>
      </div>`).join("") || this._condRowHtml(fieldOpts);
    this.openModal(`
      <h3>${a.id ? "编辑" : "添加"}识别动作</h3>
      <div class="form-row"><label>动作名称</label><input id="a-name" value="${this.esc(a.name)}"></div>
      <div class="form-row"><label>分类</label><input id="a-cat" value="${this.esc(a.category)}"></div>
      <div class="form-row"><label>描述</label><input id="a-desc" value="${this.esc(a.description)}"></div>
      <div class="form-row"><label>模板类型</label>
        <select id="a-type" onchange="App.toggleActionType()">
          <option value="rule" ${!isSeq ? "selected" : ""}>规则判定（关节特征 + 阈值）</option>
          <option value="sequence" ${isSeq ? "selected" : ""}>骨骼序列模板（DTW 精细匹配）</option>
        </select></div>
      <div id="a-rule-area" class="${isSeq ? "hidden" : ""}">
        <div class="form-row"><label>判定条件（全部满足才判定为该动作）</label>
          <div id="a-conds">${condRows}</div>
          <button class="btn btn-sm" style="margin-top:8px" onclick="App.addCondRow()">+ 添加条件</button></div>
        <div class="form-row"><label>需持续时长（秒，避免抖动误判）</label>
          <input id="a-dur" type="number" step="0.1" value="${a.duration}"></div>
      </div>
      <div id="a-seq-area" class="${isSeq ? "" : "hidden"}">
        <div class="banner banner-warn">序列模板需通过命令行录制骨骼序列：<br>
          <code>python record_template.py --name "${this.esc(a.name || "动作名")}" --source 0 --duration 5</code><br>
          录制后自动写入数据库，此处可调整匹配阈值。</div>
        <div class="form-row"><label>已录制帧数</label><input value="${frames}" disabled></div>
        <div class="form-row"><label>DTW 匹配阈值（越小越严格，默认 8.0）</label>
          <input id="a-seq-threshold" type="number" step="0.5" value="${threshold}"></div>
      </div>
      <div class="modal-actions">
        <button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn btn-primary" onclick="App.saveAction('${a.id || ""}')">保存</button>
      </div>`);
    window._fieldOpts = fieldOpts;
  },
  toggleActionType() {
    const isSeq = document.getElementById("a-type").value === "sequence";
    document.getElementById("a-rule-area").classList.toggle("hidden", isSeq);
    document.getElementById("a-seq-area").classList.toggle("hidden", !isSeq);
  },
  _condRowHtml(fieldOpts) {
    return `<div class="cond-row">
      <select data-role="joint">${fieldOpts}</select>
      <select data-role="op" style="flex:0 0 80px"><option>&gt;=</option><option>&lt;=</option><option>&gt;</option><option>&lt;</option><option>==</option></select>
      <input type="number" step="0.1" data-role="value" value="30">
      <button class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">×</button></div>`;
  },
  addCondRow() {
    const box = document.getElementById("a-conds");
    const div = document.createElement("div");
    div.innerHTML = this._condRowHtml(window._fieldOpts);
    box.appendChild(div);
  },
  async saveAction(aid) {
    const ttype = document.getElementById("a-type").value;
    const payload = {
      name: document.getElementById("a-name").value.trim(),
      category: document.getElementById("a-cat").value.trim() || "通用",
      description: document.getElementById("a-desc").value.trim(),
      template_type: ttype,
    };
    if (!payload.name) { alert("请填写动作名称"); return; }
    if (ttype === "sequence") {
      const td = window._actionTemplateData || {};
      const vectors = td.vectors || [];
      if (!(vectors.length >= 2)) {
        alert("该动作尚未录制骨骼序列。请先用命令行录制：\npython record_template.py --name \"" + payload.name + "\"");
        return;
      }
      const threshold = parseFloat(document.getElementById("a-seq-threshold").value) || 8.0;
      payload.template_data = { vectors, threshold, frames: vectors.length };
      payload.conditions = [];
      payload.duration = 1.0;
    } else {
      const conditions = [...document.querySelectorAll("#a-conds .cond-row")].map(el => ({
        joint: el.querySelector('[data-role="joint"]').value,
        op: el.querySelector('[data-role="op"]').value,
        value: parseFloat(el.querySelector('[data-role="value"]').value) || 0,
      }));
      if (!conditions.length) { alert("请至少添加一条判定条件"); return; }
      payload.conditions = conditions;
      payload.duration = parseFloat(document.getElementById("a-dur").value) || 1.0;
      payload.template_data = {};
    }
    try {
      if (aid) await this.api("/api/actions/" + aid, "PUT", payload);
      else await this.api("/api/actions", "POST", payload);
      this.closeModal(); this.go("actions");
    } catch (e) { alert(e.message); }
  },
  async delAction(aid) {
    if (!confirm("确认删除该动作？关联流程步骤将失去识别目标")) return;
    await this.api("/api/actions/" + aid, "DELETE");
    this.go("actions");
  },

  // ---------- 设备 ----------
  async renderDevices() {
    const c = document.getElementById("content");
    const data = await this.api("/api/devices");
    c.innerHTML = `<div class="card"><div class="card-head"><h3>监控设备</h3>
      <button class="btn btn-primary" onclick="App.editDevice()">+ 添加监控设备</button></div>
      <table><thead><tr><th>设备名称</th><th>类型</th><th>接入地址</th><th>位置</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${data.items.map(d => `<tr>
        <td><b>${this.esc(d.name)}</b></td><td>${this.esc(d.type)}</td>
        <td><code>${this.esc(d.url === "0" ? "本机摄像头" : d.url)}</code></td>
        <td>${this.esc(d.location)}</td>
        <td><span class="tag ${d.status === "online" ? "tag-green" : "tag-gray"}">${d.status === "online" ? "在线" : "离线"}</span></td>
        <td><button class="btn btn-sm btn-primary" onclick="App.editDevice('${d.id}')">编辑</button>
            <button class="btn btn-sm btn-danger" onclick="App.delDevice('${d.id}')">删除</button></td>
      </tr>`).join("") || `<tr><td colspan="6">${this.emptyHtml("暂无设备")}</td></tr>`}</tbody></table></div>`;
  },
  async editDevice(did) {
    let d = { name: "", type: "USB摄像头", url: "0", location: "", privacy_mask: true };
    if (did) { const data = await this.api("/api/devices"); d = data.items.find(x => x.id === did); }
    this.openModal(`
      <h3>${did ? "编辑" : "添加"}监控设备</h3>
      <div class="form-row"><label>设备名称</label><input id="d-name" value="${this.esc(d.name)}"></div>
      <div class="form-row"><label>设备类型</label>
        <select id="d-type"><option ${d.type === "USB摄像头" ? "selected" : ""}>USB摄像头</option>
        <option ${d.type === "RTSP网络摄像头" ? "selected" : ""}>RTSP网络摄像头</option>
        <option ${d.type === "HTTP视频流" ? "selected" : ""}>HTTP视频流</option></select></div>
      <div class="form-row"><label>接入地址（本机摄像头填 0，或 rtsp/http 地址）</label><input id="d-url" value="${this.esc(d.url)}"></div>
      <div class="form-row"><label>安装位置</label><input id="d-loc" value="${this.esc(d.location)}"></div>
      <div class="modal-actions">
        <button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn btn-primary" onclick="App.saveDevice('${did || ""}')">保存</button>
      </div>`);
  },
  async saveDevice(did) {
    const payload = {
      name: document.getElementById("d-name").value.trim(),
      type: document.getElementById("d-type").value,
      url: document.getElementById("d-url").value.trim() || "0",
      location: document.getElementById("d-loc").value.trim(),
      privacy_mask: true,
    };
    if (!payload.name) { alert("请填写设备名称"); return; }
    try {
      if (did) await this.api("/api/devices/" + did, "PUT", payload);
      else await this.api("/api/devices", "POST", payload);
      this.closeModal(); this.go("devices");
    } catch (e) { alert(e.message); }
  },
  async delDevice(did) {
    if (!confirm("确认删除该设备？")) return;
    await this.api("/api/devices/" + did, "DELETE");
    this.go("devices");
  },

  // ---------- 实时监管 ----------
  async renderMonitoring() {
    const c = document.getElementById("content");
    const [devices, procs, data] = await Promise.all([
      this.api("/api/devices"), this.api("/api/processes"),
      this.api("/api/monitoring/records")]);
    const pname = {};
    procs.items.forEach(p => pname[p.id] = p.name);
    const devOpts = devices.items.length
      ? devices.items.map(d => `<option value="${this.esc(d.url)}">${this.esc(d.name)}${d.url === "0" ? "（本机摄像头）" : ""}</option>`).join("")
      : `<option value="0">本机摄像头</option>`;
    const procOpts = procs.items.map(p => `<option value="${p.id}">${this.esc(p.name)}</option>`).join("");
    c.innerHTML = `
      <div class="card">
        <div class="card-head"><h3>实时监控（浏览器内直接查看画面）</h3>
          <span id="mon-status" class="tag tag-gray">未开始</span></div>
        <div class="grid" style="grid-template-columns:200px 1fr 1fr auto;gap:12px;align-items:end">
          <div class="form-row" style="margin:0"><label>监控设备</label>
            <select id="mon-device">${devOpts}</select></div>
          <div class="form-row" style="margin:0"><label>护理流程</label>
            <select id="mon-process">${procOpts}</select></div>
          <div class="form-row" style="margin:0"><label>自定义视频源（可选，覆盖设备）</label>
            <input id="mon-source" placeholder="rtsp://... 或 0"></div>
          <div style="display:flex;gap:8px">
            <button class="btn btn-primary" id="mon-start" onclick="App.startMonitor()">▶ 开始监控</button>
            <button class="btn btn-danger" id="mon-stop" onclick="App.stopMonitor()" disabled>停止</button>
          </div>
        </div>
        <div class="grid" style="grid-template-columns:1.5fr 1fr;margin-top:16px">
          <div style="background:#0f1115;border-radius:10px;min-height:340px;display:flex;align-items:center;justify-content:center;overflow:hidden">
            <img id="mon-stream" style="max-width:100%;max-height:520px;display:none">
            <span id="mon-placeholder" style="color:#4b5563">选择设备与流程后点「开始监控」，此处显示实时画面</span>
          </div>
          <div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
              <b>操作步骤状态</b><span id="mon-score" class="tag tag-gray">完成度 --</span>
            </div>
            <div id="mon-steps" class="step-list">
              <div class="empty">开始监控后实时显示步骤完成情况</div>
            </div>
            <div style="margin-top:12px;font-size:12px;color:var(--muted)">
              已识别动作：<span id="mon-detected">-</span>
            </div>
          </div>
        </div>
      </div>
      <div class="card"><div class="card-head"><h3>监控记录</h3></div>
      <table><thead><tr><th>流程</th><th>完成步骤</th><th>漏步</th><th>状态</th><th>时间</th></tr></thead>
      <tbody>${data.items.length ? data.items.map(r => `<tr>
        <td>${this.esc(pname[r.process_id] || r.process_id || "-")}</td>
        <td>${(r.completed_steps || []).length} 步</td>
        <td>${(r.missed_steps || []).length ? `<span class="tag tag-red">${r.missed_steps.length} 步漏掉</span>` : `<span class="tag tag-green">无</span>`}</td>
        <td><span class="tag ${r.status === "finished" ? "tag-green" : "tag-blue"}">${r.status === "finished" ? "已完成" : "进行中"}</span></td>
        <td>${new Date((r.started_at || 0) * 1000).toLocaleString()}</td>
      </tr>`).join("") : `<tr><td colspan="5">${this.emptyHtml("暂无监控记录")}</td></tr>`}</tbody></table></div>`;
    this.stopMonitor(true);
  },
  async startMonitor() {
    const devSel = document.getElementById("mon-device");
    const source = (document.getElementById("mon-source").value.trim()
      || (devSel ? devSel.value : "0") || "0");
    const processId = document.getElementById("mon-process").value;
    if (!processId) { alert("请先选择护理流程"); return; }
    try {
      const data = await this.api("/api/monitoring/sessions", "POST", { source, process_id: processId });
      this.state.monSession = data.session_id;
      const img = document.getElementById("mon-stream");
      img.style.display = "block";
      document.getElementById("mon-placeholder").style.display = "none";
      document.getElementById("mon-start").disabled = true;
      document.getElementById("mon-stop").disabled = false;
      document.getElementById("mon-status").className = "tag tag-green";
      document.getElementById("mon-status").textContent = "监控中";
      // 单帧 JPEG 轮询刷新画面（兼容 Chrome/Edge，避免 MJPEG 兼容问题）
      this.refreshFrame();
      if (this._frameTimer) clearInterval(this._frameTimer);
      this._frameTimer = setInterval(() => this.refreshFrame(), 120);
      this.pollMonitor();
    } catch (e) { alert(e.message); }
  },
  refreshFrame() {
    const sid = this.state.monSession;
    if (!sid) return;
    const img = document.getElementById("mon-stream");
    if (img) img.src = "/api/monitoring/sessions/" + sid + "/frame?t=" + Date.now();
  },
  async stopMonitor(silent) {
    if (this.state.monSession) {
      try { await this.api("/api/monitoring/sessions/" + this.state.monSession, "DELETE"); } catch (e) {}
      this.state.monSession = null;
    }
    if (this._monTimer) { clearInterval(this._monTimer); this._monTimer = null; }
    if (this._frameTimer) { clearInterval(this._frameTimer); this._frameTimer = null; }
    const img = document.getElementById("mon-stream");
    if (img) { img.style.display = "none"; img.src = ""; }
    const ph = document.getElementById("mon-placeholder");
    if (ph) ph.style.display = "";
    const st = document.getElementById("mon-start");
    if (st) st.disabled = false;
    const sp = document.getElementById("mon-stop");
    if (sp) sp.disabled = true;
    const ms = document.getElementById("mon-status");
    if (ms) { ms.className = "tag tag-gray"; ms.textContent = "未开始"; }
    if (!silent) this.go("monitoring");
  },
  pollMonitor() {
    if (this._monTimer) clearInterval(this._monTimer);
    this._monTimer = setInterval(async () => {
      const sid = this.state.monSession;
      if (!sid) { clearInterval(this._monTimer); this._monTimer = null; return; }
      try {
        const s = await this.api("/api/monitoring/sessions/" + sid + "/state");
        this.renderMonitorState(s);
        if (s.running === false) this.stopMonitor(true);
      } catch (e) { /* 会话可能已结束 */ }
    }, 600);
  },
  renderMonitorState(s) {
    const scoreEl = document.getElementById("mon-score");
    if (scoreEl) {
      scoreEl.textContent = "完成度 " + (s.score != null ? s.score + "%" : "--");
      scoreEl.className = "tag " + (s.score >= 80 ? "tag-green" : s.score >= 60 ? "tag-amber" : "tag-red");
    }
    const stepsEl = document.getElementById("mon-steps");
    if (stepsEl && s.steps) {
      stepsEl.innerHTML = s.steps.map(st => {
        const cls = st.status === "done" ? "done" : st.status === "miss" ? "miss"
          : st.status === "current" ? "current" : "";
        const label = st.status === "done" ? "✓已完成" : st.status === "miss" ? "⚠漏步"
          : st.status === "current" ? "▶当前" : "待执行";
        return `<div class="step-item ${cls}"><span class="order">${st.order}</span>
          <span>${this.esc(st.name)}</span><span style="margin-left:auto;font-size:12px;color:var(--muted)">${label}</span></div>`;
      }).join("");
    }
    const detEl = document.getElementById("mon-detected");
    if (detEl) detEl.textContent = (s.detected_actions || []).join(" → ") || "-";
  },

  // ---------- 培训记录 ----------
  async renderTraining() {
    const c = document.getElementById("content");
    const data = await this.api("/api/training/records");
    c.innerHTML = `<div class="card"><div class="card-head"><h3>培训记录（操作过程防漏训练）</h3></div>
      <table><thead><tr><th>学员</th><th>护理流程</th><th>得分</th><th>完成/漏步</th><th>用时(秒)</th><th>时间</th></tr></thead>
      <tbody>${data.items.length ? data.items.map(t => `<tr>
        <td>${this.esc(t.user_name || "-")}</td><td>${this.esc(t.process_name || "-")}</td>
        <td><span class="tag ${t.score >= 80 ? "tag-green" : t.score >= 60 ? "tag-amber" : "tag-red"}">${t.score}分</span></td>
        <td>${(t.completed_steps || []).length} 完成 / ${(t.missed_steps || []).length} 漏步</td>
        <td>${t.duration}</td><td>${new Date((t.created_at || 0) * 1000).toLocaleString()}</td>
      </tr>`).join("") : `<tr><td colspan="6">${this.emptyHtml("暂无培训记录")}</td></tr>`}</tbody></table></div>`;
  },

  // ---------- 用户管理 ----------
  async renderUsers() {
    const c = document.getElementById("content");
    const data = await this.api("/api/users");
    c.innerHTML = `<div class="card"><div class="card-head"><h3>用户管理</h3>
      <button class="btn btn-primary" onclick="App.editUser()">+ 新建用户</button></div>
      <table><thead><tr><th>姓名</th><th>账号</th><th>角色</th><th>机构</th><th>手机号</th><th>操作</th></tr></thead>
      <tbody>${data.items.map(u => `<tr>
        <td><b>${this.esc(u.name)}</b></td><td>${this.esc(u.username)}</td>
        <td><span class="tag tag-blue">${this.roleNames[u.role] || u.role}</span></td>
        <td>${this.esc(u.organization)}</td><td>${this.esc(u.phone_masked || "")}</td>
        <td><button class="btn btn-sm btn-danger" onclick="App.delUser('${u.id}')">删除</button></td>
      </tr>`).join("") || `<tr><td colspan="6">${this.emptyHtml()}</td></tr>`}</tbody></table></div>`;
  },
  editUser() {
    this.openModal(`
      <h3>新建用户</h3>
      <div class="form-row"><label>姓名</label><input id="u-name"></div>
      <div class="form-row"><label>账号</label><input id="u-username"></div>
      <div class="form-row"><label>密码</label><input id="u-password" type="password"></div>
      <div class="form-row"><label>角色</label><select id="u-role">
        ${Object.entries(this.roleNames).map(([k, v]) => `<option value="${k}" ${k === "nursing_student" ? "selected" : ""}>${v}</option>`).join("")}
      </select></div>
      <div class="form-row"><label>机构</label><input id="u-org"></div>
      <div class="form-row"><label>手机号</label><input id="u-phone"></div>
      <div class="modal-actions">
        <button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn btn-primary" onclick="App.saveUser()">保存</button>
      </div>`);
  },
  async saveUser() {
    const payload = {
      name: document.getElementById("u-name").value.trim(),
      username: document.getElementById("u-username").value.trim(),
      password: document.getElementById("u-password").value,
      role: document.getElementById("u-role").value,
      organization: document.getElementById("u-org").value.trim(),
      phone: document.getElementById("u-phone").value.trim(),
    };
    if (!payload.name || !payload.username || !payload.password) { alert("姓名/账号/密码必填"); return; }
    try {
      await this.api("/api/users", "POST", payload);
      this.closeModal(); this.go("users");
    } catch (e) { alert(e.message); }
  },
  async delUser(uid) {
    if (!confirm("确认删除该用户？")) return;
    await this.api("/api/users/" + uid, "DELETE");
    this.go("users");
  },

  // ---------- 隐私安全 ----------
  async renderPrivacy() {
    const c = document.getElementById("content");
    const [s, logs] = await Promise.all([
      this.api("/api/privacy/settings"), this.api("/api/privacy/audit-logs")]);
    const sMap = s.settings || {};
    const labels = {
      face_blur: "人脸自动打码（本项目默认关闭，专注动作判定）",
      store_raw_video: "保存原始监控视频",
      store_skeleton_only: "仅保存骨骼关键点数据（脱敏）",
      local_process_only: "数据仅本地处理，不出外网",
      mask_personal_info: "界面敏感信息脱敏显示",
      audit_log: "开启操作审计日志",
    };
    c.innerHTML = `<div class="grid grid-2">
      <div class="card"><div class="card-head"><h3>隐私安全策略</h3></div>
        <div id="privacy-toggles">
        ${Object.keys(labels).map(k => `<div class="checkbox-row">
          <input type="checkbox" id="priv-${k}" ${sMap[k] ? "checked" : ""}>
          <label for="priv-${k}">${labels[k]}</label></div>`).join("")}
        </div>
        <button class="btn btn-primary" onclick="App.savePrivacy()">保存策略</button></div>
      <div class="card"><div class="card-head"><h3>操作审计日志（最近 200 条）</h3></div>
        <div style="max-height:420px;overflow-y:auto">
        <table><thead><tr><th>时间</th><th>用户</th><th>操作</th><th>详情</th></tr></thead><tbody>
        ${logs.items.map(l => `<tr><td>${new Date((l.created_at || 0) * 1000).toLocaleString()}</td>
          <td>${this.esc(l.username)}</td><td>${this.esc(l.action)}</td><td>${this.esc(l.detail)}</td></tr>`).join("")
          || `<tr><td colspan="4">${this.emptyHtml("暂无日志")}</td></tr>`}</tbody></table></div></div>
    </div>`;
  },
  async savePrivacy() {
    const keys = ["face_blur", "store_raw_video", "store_skeleton_only", "local_process_only", "mask_personal_info", "audit_log"];
    const settings = {};
    keys.forEach(k => settings[k] = document.getElementById("priv-" + k).checked);
    try {
      await this.api("/api/privacy/settings", "PUT", { settings });
      alert("隐私安全策略已保存");
      this.go("privacy");
    } catch (e) { alert(e.message); }
  },
};

// 点击遮罩关闭弹窗
document.getElementById("modal-mask").addEventListener("click", e => {
  if (e.target.id === "modal-mask") App.closeModal();
});
// 登录回车
document.addEventListener("keydown", e => {
  if (e.key === "Enter" && !document.getElementById("login-view").classList.contains("hidden")) App.login();
});
