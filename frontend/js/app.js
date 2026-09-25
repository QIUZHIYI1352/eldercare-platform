/* 智慧实训规范平台 前端逻辑 */
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
    const role = this.state.user && this.state.user.role;
    const isTeacher = role === "admin" || role === "elderly_service_teacher";
    const wantTv = new URLSearchParams(location.search).get("tv") === "1";
    this.go(wantTv && isTeacher ? "tv" : isTeacher ? "live" : "mytasks");
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
    const role = this.state.user && this.state.user.role;
    const isAdmin = role === "admin";
    const isTeacher = isAdmin || role === "elderly_service_teacher";
    const isStudent = role === "nursing_student" || role === "long_term_caregiver" || role === "elderly_caregiver";
    if (isTeacher) {
      const groups = [
        { title: "教学", items: [
          { key: "live", label: "教学看板" },
          { key: "archive", label: "学员档案" },
          { key: "messages", label: "消息" }] },
        { title: "组织", items: [
          { key: "classes", label: "班级管理" },
          { key: "tasks", label: "任务发布" }] },
        { title: "内容", items: [
          { key: "library", label: "步骤库" },
          { key: "devices", label: "设备管理" }] },
        { title: "练习", items: [
          { key: "monitoring", label: "实时监控" },
          { key: "assessment", label: "动作对比" },
          { key: "training", label: "培训记录" }] },
      ];
      if (isAdmin) {
        groups.push({ title: "管理", items: [
          { key: "users", label: "用户管理" },
          { key: "privacy", label: "隐私安全" }] });
      }
      return groups;
    }
    if (isStudent) {
      return [{ title: "学习", items: [
        { key: "mytasks", label: "我的任务" },
        { key: "assessment", label: "动作对比" },
        { key: "monitoring", label: "实时监控" },
        { key: "training", label: "个人记录" },
        { key: "messages", label: "消息" }] }];
    }
    return [];
  },
  renderNav() {
    const nav = document.getElementById("nav");
    nav.innerHTML = this.navItems().map(g =>
      `<div class="nav-group-title">${g.title}</div>` +
      g.items.map(i =>
        `<div class="nav-item" data-view="${i.key}" onclick="App.go('${i.key}')">
          <span>${i.label}</span></div>`).join("")).join("");
    const ub = document.getElementById("user-badge");
    ub.innerHTML = `<div class="uname">${this.state.user.name}</div>
      <div class="urole">${this.roleNames[this.state.user.role] || this.state.user.role}</div>`;
    this.refreshUnread();
  },
  go(view) {
    this.state.view = view;
    document.body.classList.toggle("tv-mode", view === "tv");
    if (view !== "tv" && this._tvTimer) { clearInterval(this._tvTimer); this._tvTimer = null; }
    if (view !== "live" && this._liveTimer) {
      clearInterval(this._liveTimer);
      this._liveTimer = null;
    }
    if ((view !== "monitoring" && view !== "assessment") && this.state.monSession) this.stopMonitor(true);
    document.querySelectorAll(".nav-item").forEach(el =>
      el.classList.toggle("active", el.dataset.view === view));
    const titles = { dashboard: "仪表盘", processes: "护理流程管理", actions: "动作模板管理",
      devices: "监控设备管理", monitoring: "实时监管", assessment: "动作对比", training: "培训记录",
      classes: "班级管理", tasks: "任务发布", mytasks: "我的任务",
      live: "教学看板", archive: "学员档案", library: "步骤库",
      messages: "消息", tv: "大屏模式",
      users: "用户管理", privacy: "隐私安全" };
    document.getElementById("page-title").textContent = titles[view] || view;
    const views = {
      dashboard: () => this.renderDashboard(), processes: () => this.renderProcesses(),
      actions: () => this.renderActions(), devices: () => this.renderDevices(),
      monitoring: () => this.renderMonitoring(), assessment: () => this.renderAssessment(),
      training: () => this.renderTraining(),
      classes: () => this.renderClasses(), tasks: () => this.renderTasks(),
      mytasks: () => this.renderMyTasks(),
      live: () => this.renderLive(), archive: () => this.renderArchive(),
      library: () => this.renderLibrary(), messages: () => this.renderMessages(),
      tv: () => this.renderTv(),
      users: () => this.renderUsers(), privacy: () => this.renderPrivacy(),
    };
    (views[view] || (() => {}))();
  },

  // ---------- 通用 ----------
  esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); },
  // 内联事件处理器里的 JS 字符串字面量参数专用转义。
  // esc() 产出的 &#39; 会被浏览器解码回单引号，仍能闭合 '…' 造成 XSS，
  // 因此这里按 JS 字面量转义，并把 HTML 敏感字符写成 \xNN 形式。
  jss(s) { return String(s == null ? "" : s)
    .replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/"/g, "\\x22")
    .replace(/&/g, "\\x26").replace(/</g, "\\x3c").replace(/>/g, "\\x3e")
    .replace(/[\r\n\u2028\u2029]/g, " "); },
  // 画面帧地址：<img> 无法带 Authorization 头，改用 token 查询参数鉴权
  frameUrl(sid) {
    return "/api/monitoring/sessions/" + sid + "/frame?token="
      + encodeURIComponent(this.state.token || "") + "&t=" + Date.now();
  },
  openModal(html) {
    document.getElementById("modal-body").innerHTML = html;
    document.getElementById("modal-mask").classList.remove("hidden");
  },
  closeModal() {
    document.getElementById("modal-mask").classList.add("hidden");
    if (this._frameModalTimer) { clearInterval(this._frameModalTimer); this._frameModalTimer = null; }
  },
  emptyHtml(txt) { return `<div class="empty">${txt || "暂无数据"}</div>`; },

  // ---------- 仪表盘 ----------
  async renderDashboard() {
    const c = document.getElementById("content");
    c.innerHTML = `<div class="banner banner-info">
      系统核心能力：① 通过 cv2 + mediapipe 实时识别护理操作动作，比对标准流程，<b>提醒「哪一步漏掉了」</b>；
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
      { n: procs.items.length, l: "护理流程", i: "" },
      { n: acts.items.length, l: "识别动作", i: "" },
      { n: devs.items.length, l: "监控设备", i: "" },
      { n: trains.items.length, l: "培训记录", i: "" },
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
        <button class="btn btn-primary" onclick="App.saveProcess('${pid || ""}')">保存</button>
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
      this.closeModal(); this.state.libTab = "processes"; this.go("library");
    } catch (e) { alert(e.message); }
  },
  async delProcess(pid) {
    if (!confirm("确认删除该流程？")) return;
    await this.api("/api/processes/" + pid, "DELETE");
    this.state.libTab = "processes"; this.go("library");
  },

  // ---------- 动作模板 ----------
  async renderActions() {
    const c = document.getElementById("content");
    const [data, fields] = await Promise.all([
      this.api("/api/actions"), this.api("/api/actions/fields")]);
    this.state._fields = fields.items;
    c.innerHTML = `<div class="banner banner-info">自主添加识别动作，支持两类模板：
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
      this.closeModal(); this.state.libTab = "actions"; this.go("library");
    } catch (e) { alert(e.message); }
  },
  async delAction(aid) {
    if (!confirm("确认删除该动作？关联流程步骤将失去识别目标")) return;
    await this.api("/api/actions/" + aid, "DELETE");
    this.state.libTab = "actions"; this.go("library");
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
            <button class="btn" id="mon-phone" onclick="App.openPhoneCam()">用手机当摄像头</button>
            <button class="btn btn-primary" id="mon-start" onclick="App.startMonitor()">开始监控</button>
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

  // ---------- 用手机当摄像头 ----------
  // 手机不装任何 App：平台在服务端起一个 HTTPS 小服务（浏览器只在安全上下文
  // 里放行摄像头），这里把二维码显示出来，手机扫一下就进去了。
  // 连上后视频源地址自动填好，使用者只需要点「开始监控」。
  async openPhoneCam() {
    let d;
    try {
      d = await this.api("/api/phone-cam/start", "POST", {});
    } catch (e) { alert(e.message); return; }

    // 源地址自动填好——这一步是「方便」的关键：使用者不必知道
    // http://127.0.0.1:8444/video 这种地址的存在。
    const src = document.getElementById("mon-source");
    if (src) src.value = d.feed_url;

    const qr = d.qr_svg
      ? `<div style="background:#fff;padding:10px;border-radius:12px;border:1px solid var(--border);line-height:0">${d.qr_svg}</div>`
      : `<div style="padding:14px;border:1px dashed var(--border);border-radius:12px;font-size:13px;color:var(--muted);max-width:280px">
           本机没装 OpenCV，生成不了二维码，请手动在手机浏览器里输入右边的地址。</div>`;

    this.openModal(`
      <h3>用手机当摄像头</h3>
      <p style="color:var(--muted);font-size:13px;margin-bottom:14px">
        手机与你连同一个 WiFi，用相机扫码打开。页面会提示「连接不私密」——
        自签证书的正常现象，点「高级 → 继续前往」，再点「允许」访问摄像头即可。
        画面只在局域网内传输，不出公网。</p>
      <div style="display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap">
        ${qr}
        <div style="flex:1;min-width:230px">
          <div class="form-row" style="margin-top:0"><label>手机打开这个地址</label>
            <input readonly value="${this.esc(d.https_url)}" onclick="this.select()"></div>
          <div id="pc-state" class="tag tag-amber">等待手机扫码…</div>
          <div style="margin-top:12px;font-size:12px;color:var(--muted)">
            视频源已自动填好，手机连上后直接点「开始监控」。<br>
            这台设备也会登记进「设备管理」，以后直接在设备下拉里选。</div>
        </div>
      </div>
      <div class="modal-actions">
        <button class="btn btn-danger" onclick="App.stopPhoneCam()">停止手机摄像头</button>
        <button class="btn" onclick="App.closeModal()">关闭</button>
      </div>`);
    this.pollPhoneCam();
  },
  pollPhoneCam() {
    if (this._pcTimer) clearInterval(this._pcTimer);
    this._pcTimer = setInterval(async () => {
      const el = document.getElementById("pc-state");
      // 弹窗关了就直接停轮询（比在 closeModal 里各处挂钩子更不容易漏）
      if (!el) { clearInterval(this._pcTimer); this._pcTimer = null; return; }
      try {
        const s = await this.api("/api/phone-cam/status");
        if (s.connected) {
          el.className = "tag tag-green";
          el.textContent = "手机已连接" + (s.fps ? " · " + s.fps + " fps" : "")
            + " · 已收 " + s.frames + " 帧";
        } else if (s.running) {
          el.className = "tag tag-amber";
          el.textContent = "等待手机连接…";
        } else {
          el.className = "tag tag-red";
          el.textContent = "服务已停止";
        }
      } catch (e) { /* 会话过期等，下一轮再说 */ }
    }, 1000);
  },
  async stopPhoneCam() {
    try { await this.api("/api/phone-cam/stop", "POST"); } catch (e) {}
    if (this._pcTimer) { clearInterval(this._pcTimer); this._pcTimer = null; }
    this.closeModal();
    this.go("monitoring");
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
    const img = document.getElementById("mon-stream") || document.getElementById("as-stream");
    if (img) img.src = this.frameUrl(sid);
  },
  async stopMonitor(silent) {
    if (this.state.monSession) {
      try { await this.api("/api/monitoring/sessions/" + this.state.monSession, "DELETE"); } catch (e) {}
      this.state.monSession = null;
    }
    if (this._monTimer) { clearInterval(this._monTimer); this._monTimer = null; }
    if (this._frameTimer) { clearInterval(this._frameTimer); this._frameTimer = null; }
    if (this._asTimer) { clearInterval(this._asTimer); this._asTimer = null; }
    if (this._asFrameTimer) { clearInterval(this._asFrameTimer); this._asFrameTimer = null; }
    this.state.assessMode = false;
    this.state.taskId = null;
    this.state.taskTitle = "";
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
    if (s.error) {
      const statusEl = document.getElementById("mon-status");
      if (statusEl) { statusEl.className = "tag tag-red"; statusEl.textContent = "识别出错"; }
      const ph = document.getElementById("mon-placeholder");
      if (ph) { ph.textContent = "识别出错：" + s.error; ph.style.display = ""; }
      const img = document.getElementById("mon-stream");
      if (img) { img.style.display = "none"; img.src = ""; }
      return;
    }
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
        const label = st.status === "done" ? "已完成" : st.status === "miss" ? "漏步"
          : st.status === "current" ? "当前" : "待执行";
        return `<div class="step-item ${cls}"><span class="order">${st.order}</span>
          <span>${this.esc(st.name)}</span><span style="margin-left:auto;font-size:12px;color:var(--muted)">${label}</span></div>`;
      }).join("");
    }
    const detEl = document.getElementById("mon-detected");
    if (detEl) detEl.textContent = (s.detected_actions || []).join(" → ") || "-";
  },

  // ---------- 动作对比 ----------
  async renderAssessment() {
    const c = document.getElementById("content");
    const [procs, data] = await Promise.all([
      this.api("/api/processes"), this.api("/api/assessment/records")]);
    const pname = {};
    procs.items.forEach(p => pname[p.id] = p.name);
    const procOpts = procs.items.map(p => `<option value="${p.id}">${this.esc(p.name)}</option>`).join("");
    const taskMode = !!this.state.taskId;
    window._reportFrom = taskMode ? "mytasks" : "assessment";
    const headerTitle = taskMode
      ? `任务练习：${this.esc(this.state.taskTitle || "")}`
      : "动作对比（摄像头采集 → 自动评分）";
    c.innerHTML = `
      <div class="card">
        <div class="card-head"><h3>${headerTitle}</h3>
          <span id="as-status" class="tag tag-gray">未开始</span></div>
        <div class="grid" style="grid-template-columns:1fr 1fr auto;gap:12px;align-items:end">
          <div class="form-row" style="margin:0;${taskMode ? "display:none" : ""}"><label>护理流程</label>
            <select id="as-process">${procOpts}</select></div>
          <div class="form-row" style="margin:0"><label>自定义视频源（留空用本机摄像头）</label>
            <input id="as-source" placeholder="rtsp://... 或留空"></div>
          <div style="display:flex;gap:8px">
            <button class="btn btn-primary" id="as-start" onclick="App.startAssessment()">开始</button>
            <button class="btn btn-danger" id="as-stop" onclick="App.stopAssessment()" disabled>结束</button>
          </div>
        </div>
        <div class="grid" style="grid-template-columns:1.5fr 1fr;margin-top:16px">
          <div style="background:#0f1115;border-radius:10px;min-height:340px;display:flex;align-items:center;justify-content:center;overflow:hidden">
            <img id="as-stream" style="max-width:100%;max-height:520px;display:none">
            <span id="as-placeholder" style="color:#4b5563">选流程后点「开始」，完整做一遍标准流程</span>
          </div>
          <div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
              <b>步骤状态</b><span id="as-score" class="tag tag-gray">完成度 --</span>
            </div>
            <div id="as-steps" class="step-list"><div class="empty">开始后实时显示</div></div>
          </div>
        </div>
      </div>
      <div class="card"><div class="card-head"><h3>对比记录</h3></div>
      <table><thead><tr><th>流程</th><th>得分</th><th>遗漏</th><th>顺序错误</th><th>时间</th><th>操作</th></tr></thead>
      <tbody>${data.items.length ? data.items.map(r => `<tr>
        <td>${this.esc(pname[r.process_id] || "-")}</td>
        <td>${r.score == null ? '<span class="tag tag-gray">无标准</span>' : `<span class="tag ${r.score >= 80 ? "tag-green" : r.score >= 60 ? "tag-amber" : "tag-red"}">${r.score}分</span>`}</td>
        <td>${r.missed ? `<span class="tag tag-red">${r.missed} 步</span>` : '<span class="tag tag-green">无</span>'}</td>
        <td>${r.order_errors ? `<span class="tag tag-amber">${r.order_errors} 步</span>` : '<span class="tag tag-green">无</span>'}</td>
        <td>${new Date((r.started_at || 0) * 1000).toLocaleString()}</td>
        <td><button class="btn btn-sm btn-primary" onclick="App.renderAssessmentReport('${r.id}')">查看报告</button></td>
      </tr>`).join("") : `<tr><td colspan="6">${this.emptyHtml("暂无对比记录")}</td></tr>`}</tbody></table></div>`;
  },
  async startAssessment() {
    const processId = document.getElementById("as-process").value;
    const source = document.getElementById("as-source").value.trim() || "0";
    const taskMode = !!this.state.taskId;
    if (!taskMode && !processId) { alert("请选择流程"); return; }
    try {
      const body = taskMode ? { task_id: this.state.taskId, source }
        : { source, process_id: processId };
      const data = await this.api("/api/assessment/sessions", "POST", body);
      this.state.monSession = data.session_id;
      this.state.assessMode = true;
      const img = document.getElementById("as-stream");
      img.style.display = "block";
      document.getElementById("as-placeholder").style.display = "none";
      document.getElementById("as-start").disabled = true;
      document.getElementById("as-stop").disabled = false;
      document.getElementById("as-status").className = "tag tag-green";
      document.getElementById("as-status").textContent = "采集中";
      this.refreshFrame();
      if (this._asFrameTimer) clearInterval(this._asFrameTimer);
      this._asFrameTimer = setInterval(() => this.refreshFrame(), 120);
      this.pollAssessment();
    } catch (e) { alert(e.message); }
  },
  async stopAssessment(recordId) {
    const fromTask = !!this.state.taskId;
    window._reportFrom = fromTask ? "mytasks" : "assessment";
    if (this.state.monSession && !recordId) {
      try {
        const r = await this.api("/api/assessment/sessions/" + this.state.monSession, "DELETE");
        recordId = r.record_id;
      } catch (e) {}
    }
    this.state.monSession = null;
    this.state.assessMode = false;
    this.state.taskId = null;
    this.state.taskTitle = "";
    if (this._asFrameTimer) { clearInterval(this._asFrameTimer); this._asFrameTimer = null; }
    if (this._asTimer) { clearInterval(this._asTimer); this._asTimer = null; }
    const img = document.getElementById("as-stream");
    if (img) { img.style.display = "none"; img.src = ""; }
    const ph = document.getElementById("as-placeholder");
    if (ph) ph.style.display = "";
    const st = document.getElementById("as-start");
    if (st) st.disabled = false;
    const sp = document.getElementById("as-stop");
    if (sp) sp.disabled = true;
    const ms = document.getElementById("as-status");
    if (ms) { ms.className = "tag tag-gray"; ms.textContent = "未开始"; }
    if (recordId) this.renderAssessmentReport(recordId);
  },
  pollAssessment() {
    if (this._asTimer) clearInterval(this._asTimer);
    this._asTimer = setInterval(async () => {
      const sid = this.state.monSession;
      if (!sid) { clearInterval(this._asTimer); this._asTimer = null; return; }
      try {
        const s = await this.api("/api/monitoring/sessions/" + sid + "/state");
        this.renderAssessmentState(s);
        if (s.running === false) {
          this.stopAssessment();
        }
      } catch (e) {}
    }, 600);
  },
  renderAssessmentState(s) {
    if (s.error) {
      const statusEl = document.getElementById("as-status");
      if (statusEl) { statusEl.className = "tag tag-red"; statusEl.textContent = "识别出错"; }
      const ph = document.getElementById("as-placeholder");
      if (ph) { ph.textContent = "识别出错：" + s.error; ph.style.display = ""; }
      const img = document.getElementById("as-stream");
      if (img) { img.style.display = "none"; img.src = ""; }
      return;
    }
    const scoreEl = document.getElementById("as-score");
    if (scoreEl) {
      scoreEl.textContent = "完成度 " + (s.score != null ? s.score + "%" : "--");
      scoreEl.className = "tag " + (s.score >= 80 ? "tag-green" : s.score >= 60 ? "tag-amber" : "tag-red");
    }
    const stepsEl = document.getElementById("as-steps");
    if (stepsEl && s.steps) {
      stepsEl.innerHTML = s.steps.map(st => {
        const cls = st.status === "done" ? "done" : st.status === "miss" ? "miss"
          : st.status === "current" ? "current" : "";
        const label = st.status === "done" ? "已完成" : st.status === "miss" ? "漏步"
          : st.status === "current" ? "当前" : "待执行";
        return `<div class="step-item ${cls}"><span class="order">${st.order}</span>
          <span>${this.esc(st.name)}</span><span style="margin-left:auto;font-size:12px;color:var(--muted)">${label}</span></div>`;
      }).join("");
    }
  },
  async renderAssessmentReport(rid) {
    const c = document.getElementById("content");
    const backView = window._reportFrom === "mytasks" ? "mytasks" : "assessment";
    const backLabel = backView === "mytasks" ? "返回我的任务" : "返回";
    let r;
    try { r = await this.api("/api/assessment/records/" + rid); }
    catch (e) { alert(e.message); return; }
    const segRows = (r.segments || []).map(s => {
      const tagMap = { matched: '<span class="tag tag-green">完成</span>',
        order_error: '<span class="tag tag-amber">顺序错误</span>',
        missed: '<span class="tag tag-red">遗漏</span>' };
      const scoreHtml = s.comparable
        ? (s.score == null ? '<span class="tag tag-gray">--</span>' : `${s.score} 分`)
        : '<span class="tag tag-gray">无标准</span>';
      const startSec = s.start_ts != null ? s.start_ts.toFixed(2) : "";
      const endSec = s.end_ts != null ? s.end_ts.toFixed(2) : "";
      const editable = s.result === "matched" ? `
        <input type="number" step="0.1" data-order="${s.order}" data-edge="start" value="${startSec}" style="width:76px">
        ~ <input type="number" step="0.1" data-order="${s.order}" data-edge="end" value="${endSec}" style="width:76px"> 秒` : startSec ? `${startSec} ~ ${endSec} 秒` : "--";
      return `<tr><td>${s.order}. ${this.esc(s.name)}</td><td>${tagMap[s.result] || s.result}</td>
        <td>${editable}</td><td>${scoreHtml}</td></tr>`;
    }).join("");
    c.innerHTML = `
      <div class="card">
        <div class="card-head"><h3>对比报告 · ${this.esc(r.process_name || "")}</h3>
          <button class="btn" onclick="App.go('${backView}')">${backLabel}</button></div>
        <div class="grid grid-4" style="margin-bottom:16px">
          <div class="stat"><div class="num">${r.score == null ? "--" : r.score}</div><div class="lbl">总分（有标准步骤平均）</div></div>
          <div class="stat"><div class="num">${(r.segments || []).filter(s => s.result === "missed").length}</div><div class="lbl">遗漏步数</div></div>
          <div class="stat"><div class="num">${(r.segments || []).filter(s => s.result === "order_error").length}</div><div class="lbl">顺序错误步数</div></div>
          <div class="stat"><div class="num">${(r.segments || []).filter(s => s.comparable).length}</div><div class="lbl">可评分步数</div></div>
        </div>
        <div class="banner banner-info">完成步可在「边界」列直接改起止秒数后点保存重算。</div>
        <table><thead><tr><th>步骤</th><th>结果</th><th>边界</th><th>得分</th></tr></thead>
        <tbody>${segRows || `<tr><td colspan="4">${this.emptyHtml("无步骤")}</td></tr>`}</tbody></table>
        <div class="modal-actions"><button class="btn btn-primary" onclick="App.saveAssessmentSegments('${rid}')">保存边界并重算</button></div>
      </div>`;
  },
  async saveAssessmentSegments(rid) {
    const rows = [...document.querySelectorAll("#content input[data-edge]")];
    const segments = rows.reduce((acc, el) => {
      const o = Number(el.dataset.order);
      acc[o] = acc[o] || { order: o };
      acc[o][el.dataset.edge === "start" ? "start_ts" : "end_ts"] = parseFloat(el.value) || 0;
      return acc;
    }, {});
    try {
      const r = await this.api("/api/assessment/records/" + rid + "/segments", "PUT",
        { segments: Object.values(segments) });
      alert("已重算，总分 " + (r.score == null ? "--" : r.score));
      this.renderAssessmentReport(rid);
    } catch (e) { alert(e.message); }
  },

  // ---------- 培训记录 ----------
  async renderTraining() {
    const c = document.getElementById("content");
    const data = await this.api("/api/training/records");
    const isStudent = ["nursing_student", "long_term_caregiver", "elderly_caregiver"].includes(this.state.user && this.state.user.role);
    c.innerHTML = `<div class="card"><div class="card-head"><h3>${isStudent ? "个人记录（我的练习）" : "培训记录（操作过程防漏训练）"}</h3></div>
      <table><thead><tr><th>学员</th><th>护理流程</th><th>得分</th><th>完成/漏步</th><th>用时(秒)</th><th>时间</th><th>操作</th></tr></thead>
      <tbody>${data.items.length ? data.items.map(t => `<tr>
        <td>${this.esc(t.user_name || "-")}</td><td>${this.esc(t.process_name || "-")}</td>
        <td><span class="tag ${t.score >= 80 ? "tag-green" : t.score >= 60 ? "tag-amber" : "tag-red"}">${t.score}分</span></td>
        <td>${(t.completed_steps || []).length} 完成 / ${(t.missed_steps || []).length} 漏步</td>
        <td>${t.duration}</td><td>${new Date((t.created_at || 0) * 1000).toLocaleString()}</td>
        <td>${t.capture_record_id ? `<button class="btn btn-sm btn-primary" onclick="App.renderAssessmentReport('${t.capture_record_id}')">查看报告</button>` : ""}</td>
      </tr>`).join("") : `<tr><td colspan="7">${this.emptyHtml(isStudent ? "暂无练习记录" : "暂无培训记录")}</td></tr>`}</tbody></table></div>`;
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

  // 说明：教学看板（renderLive/refreshLive）与学员档案（renderArchive/loadArchive）
  // 的统一实现位于文件末尾的“提效改造”区块，此处不再重复定义，避免误改死代码。

  async loadStudentRecords(uid) {
    const box = document.getElementById("student-records");
    const d = await this.api("/api/teacher/students/" + uid + "/records");
    box.innerHTML = `<div style="margin-top:14px"><h4 style="margin-bottom:8px">练习明细</h4>
      <table><thead><tr><th>任务</th><th>流程</th><th>得分</th><th>遗漏</th><th>顺序错误</th><th>时间</th><th>操作</th></tr></thead>
      <tbody>${d.items.map(r => `<tr>
        <td>${this.esc(r.task_name || "-")}</td><td>${this.esc(r.process_name || "-")}</td>
        <td>${r.score == null ? "--" : r.score}</td><td>${r.missed}</td><td>${r.order_errors}</td>
        <td>${new Date((r.started_at || 0) * 1000).toLocaleString()}</td>
        <td><button class="btn btn-sm" onclick="App.renderAssessmentReport('${r.id}')">报告</button></td>
      </tr>`).join("") || `<tr><td colspan="7">${this.emptyHtml("暂无练习记录")}</td></tr>`}</tbody></table></div>`;
  },

  // ---------- 班级管理 ----------
  async renderClasses() {
    const c = document.getElementById("content");
    const data = await this.api("/api/classes");
    c.innerHTML = `<div class="card"><div class="card-head"><h3>我的班级</h3>
      <button class="btn btn-primary" onclick="App.editClass()">+ 新建班级</button></div>
      <div id="class-list"></div></div>`;
    const list = document.getElementById("class-list");
    if (!data.items.length) { list.innerHTML = this.emptyHtml("暂无班级"); return; }
    list.innerHTML = data.items.map(cls => `
      <div class="card" style="box-shadow:none;border:1px solid var(--border)">
        <div class="card-head"><h3>${this.esc(cls.name)}
          <span class="tag tag-blue">${cls.member_count} 人</span></h3>
          <div>
            <button class="btn btn-sm" onclick="App.renderClassMembers('${cls.id}')">成员管理</button>
            <button class="btn btn-sm btn-primary" onclick="App.editClass('${cls.id}')">编辑</button>
            <button class="btn btn-sm btn-danger" onclick="App.delClass('${cls.id}')">删除</button>
          </div></div>
        <p style="color:var(--muted);font-size:13px">${this.esc(cls.description || "")}</p>
      </div>`).join("");
  },
  async editClass(cid) {
    let cls = { name: "", description: "" };
    if (cid) {
      const data = await this.api("/api/classes");
      cls = data.items.find(x => x.id === cid);
    }
    this.openModal(`
      <h3>${cid ? "编辑" : "新建"}班级</h3>
      <div class="form-row"><label>班级名称</label><input id="c-name" value="${this.esc(cls.name)}"></div>
      <div class="form-row"><label>描述（可选）</label><textarea id="c-desc">${this.esc(cls.description || "")}</textarea></div>
      <div class="modal-actions">
        <button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn btn-primary" onclick="App.saveClass('${cid || ""}')">保存</button>
      </div>`);
  },
  async saveClass(cid) {
    const payload = { name: document.getElementById("c-name").value.trim(),
      description: document.getElementById("c-desc").value.trim() };
    if (!payload.name) { alert("请填写班级名称"); return; }
    try {
      if (cid) await this.api("/api/classes/" + cid, "PUT", payload);
      else await this.api("/api/classes", "POST", payload);
      this.closeModal(); this.go("classes");
    } catch (e) { alert(e.message); }
  },
  async delClass(cid) {
    if (!confirm("确认删除该班级？成员关系会一并删除")) return;
    await this.api("/api/classes/" + cid, "DELETE");
    this.go("classes");
  },
  async renderClassMembers(cid) {
    const data = await this.api("/api/classes/" + cid + "/members");
    this.openModal(`<h3>成员管理</h3>
      <div class="form-row"><label>按账号添加学员（逗号分隔多个）</label>
        <input id="m-names" placeholder="nurse001, elderly001"></div>
      <button class="btn btn-primary" onclick="App.addClassMembers('${cid}')">添加</button>
      <p id="m-msg" class="msg"></p>
      <table style="margin-top:12px"><thead><tr><th>账号</th><th>姓名</th><th>角色</th><th>操作</th></tr></thead>
      <tbody>${data.items.map(m => `<tr><td>${this.esc(m.username)}</td><td>${this.esc(m.name)}</td>
        <td>${this.esc(this.roleNames[m.role] || m.role)}</td>
        <td><button class="btn btn-sm btn-danger" onclick="App.removeClassMember('${cid}','${m.id}')">移除</button></td></tr>`).join("")
        || `<tr><td colspan="4">${this.emptyHtml("暂无成员")}</td></tr>`}</tbody></table>
      <div class="modal-actions"><button class="btn" onclick="App.closeModal()">关闭</button></div>`);
  },
  async addClassMembers(cid) {
    const msg = document.getElementById("m-msg");
    const usernames = document.getElementById("m-names").value.split(/[,，\s]+/).filter(Boolean);
    try {
      const r = await this.api("/api/classes/" + cid + "/members", "POST", { usernames });
      msg.textContent = `已添加 ${r.added.length} 人` + (r.errors.length ? "；失败: " + r.errors.join("；") : "");
      this.renderClassMembers(cid);
    } catch (e) { msg.textContent = e.message; }
  },
  async removeClassMember(cid, uid) {
    if (!confirm("确认移除该学员？")) return;
    await this.api("/api/classes/" + cid + "/members/" + uid, "DELETE");
    this.renderClassMembers(cid);
  },

  // ---------- 任务发布 ----------
  async renderTasks() {
    const c = document.getElementById("content");
    const [data, clsData] = await Promise.all([
      this.api("/api/tasks"), this.api("/api/classes")]);
    c.innerHTML = `<div class="card"><div class="card-head"><h3>已发布任务</h3>
      <button class="btn btn-primary" onclick="App.editTask()">+ 发布任务</button></div>
      <table><thead><tr><th>标题</th><th>班级</th><th>流程</th><th>截止</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${data.items.map(t => `<tr>
        <td><b>${this.esc(t.title)}</b></td><td>${this.esc(t.class_name)}</td>
        <td>${this.esc(t.process_name)}</td>
        <td>${new Date((t.deadline || 0) * 1000).toLocaleString()}</td>
        <td><span class="tag ${t.status === "published" ? "tag-green" : "tag-gray"}">${t.status === "published" ? "进行中" : "已关闭"}</span></td>
        <td><button class="btn btn-sm btn-primary" onclick="App.editTask('${t.id}')">编辑</button>
            <button class="btn btn-sm btn-danger" onclick="App.delTask('${t.id}')">删除</button></td>
      </tr>`).join("") || `<tr><td colspan="6">${this.emptyHtml("暂无任务")}</td></tr>`}</tbody></table></div>`;
    window._teacherClasses = clsData.items;
  },
  async editTask(tid) {
    let t = { title: "", description: "", status: "published" };
    if (tid) {
      const data = await this.api("/api/tasks");
      t = data.items.find(x => x.id === tid);
    }
    const procs = await this.api("/api/processes");
    const clsOpts = (window._teacherClasses || []).map(x => `<option value="${x.id}">${this.esc(x.name)}</option>`).join("");
    const procOpts = procs.items.map(p => `<option value="${p.id}">${this.esc(p.name)}</option>`).join("");
    let deadlineVal = "";
    if (t.deadline) {
      const d = new Date(t.deadline * 1000);
      const pad = n => String(n).padStart(2, "0");
      deadlineVal = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
    this.openModal(`
      <h3>${tid ? "编辑" : "发布"}任务</h3>
      <div class="form-row"><label>标题</label><input id="t-title" value="${this.esc(t.title)}"></div>
      <div class="form-row"><label>班级</label><select id="t-class">${clsOpts.replace(`value="${t.class_id}"`, `value="${t.class_id}" selected`)}</select></div>
      <div class="form-row"><label>流程</label><select id="t-proc">${procOpts.replace(`value="${t.process_id}"`, `value="${t.process_id}" selected`)}</select></div>
      <div class="form-row"><label>截止时间</label>
        <input id="t-deadline" type="datetime-local" value="${deadlineVal}"></div>
      <div class="form-row"><label>描述（可选）</label><textarea id="t-desc">${this.esc(t.description || "")}</textarea></div>
      <div class="modal-actions">
        <button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn btn-primary" onclick="App.saveTask('${tid || ""}')">保存</button>
      </div>`);
  },
  async saveTask(tid) {
    const deadline = new Date(document.getElementById("t-deadline").value).getTime();
    const payload = {
      title: document.getElementById("t-title").value.trim(),
      class_id: document.getElementById("t-class").value,
      process_id: document.getElementById("t-proc").value,
      deadline: Math.floor(deadline / 1000),
      description: document.getElementById("t-desc").value.trim(),
    };
    if (!payload.title || !deadline) { alert("标题与截止时间必填"); return; }
    try {
      if (tid) await this.api("/api/tasks/" + tid, "PUT", payload);
      else await this.api("/api/tasks", "POST", payload);
      this.closeModal(); this.go("tasks");
    } catch (e) { alert(e.message); }
  },
  async delTask(tid) {
    if (!confirm("确认删除该任务？")) return;
    await this.api("/api/tasks/" + tid, "DELETE");
    this.go("tasks");
  },

  // ---------- 我的任务 ----------
  async renderMyTasks() {
    const c = document.getElementById("content");
    const data = await this.api("/api/tasks/mine");
    window._myTasks = data.items;
    const stateMap = { pending: ["tag-gray", "待完成"], overdue: ["tag-red", "已逾期"], done: ["tag-green", "已完成"] };
    c.innerHTML = `<div class="card"><div class="card-head"><h3>我的任务</h3></div>
      <div id="mytask-list"></div></div>`;
    const list = document.getElementById("mytask-list");
    if (!data.items.length) { list.innerHTML = this.emptyHtml("暂无任务，等待老师发布"); return; }
    list.innerHTML = data.items.map(t => {
      const [tagCls, tagTxt] = stateMap[t.state] || stateMap.pending;
      return `<div class="card" style="box-shadow:none;border:1px solid var(--border)">
        <div class="card-head"><h3>${this.esc(t.title)} <span class="tag ${tagCls}">${tagTxt}</span></h3>
          <span style="color:var(--muted);font-size:12px">${t.latest_score == null ? "" : "最新得分 " + t.latest_score + " 分"}</span></div>
        <p style="color:var(--muted);font-size:13px">班级：${this.esc(t.class_name)} ｜ 流程：${this.esc(t.process_name)}（${t.steps_count} 步）</p>
        <p style="color:var(--muted);font-size:13px">截止：${new Date((t.deadline || 0) * 1000).toLocaleString()}</p>
        <div style="margin-top:10px">
          <button class="btn btn-primary btn-sm" onclick="App.startTaskPractice('${t.id}')">开始练习</button>
        </div>
      </div>`;
    }).join("");
  },
  startTaskPractice(taskId) {
    const task = (window._myTasks || []).find(t => t.id === taskId);
    this.state.taskId = taskId;
    this.state.taskTitle = task ? task.title : "任务练习";
    this.go("assessment");
  },
};

// ---------- 提效改造：教学看板 / 大屏 / 消息 / 步骤库 / 档案增强 ----------
Object.assign(App, {
  async renderLive() {
    const c = document.getElementById("content");
    this.state.liveFilter = this.state.liveFilter || "all";
    c.innerHTML = `
      <div class="card">
        <div class="card-head"><h3>教学看板</h3>
          <div style="display:flex;gap:8px">
            <button class="btn" onclick="App.showThresholds()">阈值设置</button>
            <button class="btn" onclick="window.open('/?tv=1','_blank')">大屏模式</button>
          </div></div>
        <div class="grid grid-4" id="live-stats"></div>
        <div style="display:flex;gap:8px;margin:14px 0">
          <button class="btn btn-sm" onclick="App.setLiveFilter('all')">全部进行中</button>
          <button class="btn btn-sm" onclick="App.setLiveFilter('alert')">仅看异常</button>
          <button class="btn btn-sm" onclick="App.setLiveFilter('off')">离屏</button>
        </div>
        <div id="live-sessions">加载中…</div>
      </div>
      <div class="card"><div class="card-head"><h3>完成后预警（最近记录）</h3></div>
      <div id="live-alerts">加载中…</div></div>`;
    await this.refreshLive();
    if (this._liveTimer) clearInterval(this._liveTimer);
    this._liveTimer = setInterval(() => this.refreshLive(), 3000);
  },
  setLiveFilter(f) { this.state.liveFilter = f; this.refreshLive(); },
  async refreshLive() {
    try {
      const d = await this.api("/api/dashboard/live");
      const levels = { ok: ["tag-green", "正常"], warn: ["tag-amber", "卡住"],
        critical: ["tag-red", "严重卡顿"], off: ["tag-red", "离开画面"] };
      const stats = [
        { n: d.sessions.length, l: "在线练习" },
        { n: d.sessions.filter(s => s.alert_level !== "ok").length, l: "实时异常" },
        { n: d.alerts.length, l: "完成后预警" },
        { n: d.thresholds ? d.thresholds.step_warn + "/" + d.thresholds.step_crit + "s" : "--", l: "卡住/严重阈值" },
      ];
      const st = document.getElementById("live-stats");
      if (st) st.innerHTML = stats.map(s => `<div class="stat"><div class="num">${s.n}</div><div class="lbl">${s.l}</div></div>`).join("");
      const filter = this.state.liveFilter || "all";
      const rows = d.sessions.filter(s => filter === "all" ? true
        : filter === "alert" ? s.alert_level !== "ok" : s.alert_level === "off");
      const sEl = document.getElementById("live-sessions");
      if (sEl) sEl.innerHTML = rows.length
        ? `<table><thead><tr><th>学员</th><th>班级/任务</th><th>流程</th><th>当前步骤</th>
            <th>停留</th><th>完成度</th><th>状态</th><th>操作</th></tr></thead><tbody>`
          + rows.map(s => {
            const [cls, txt] = levels[s.alert_level] || levels.ok;
            return `<tr><td><b>${this.esc(s.user_name)}</b></td>
              <td>${this.esc(s.class_name || "-")}<br><span style="color:var(--muted);font-size:12px">${this.esc(s.task_name || "-")}</span></td>
              <td>${this.esc(s.process_name)}</td>
              <td>第 ${(s.current_step ?? 0) + 1} 步</td>
              <td>${s.step_stay}s</td>
              <td>${s.score ?? "--"}%</td>
              <td><span class="tag ${cls}">${txt}</span></td>
              <td><button class="btn btn-sm" onclick="App.viewLiveFrame('${s.session_id}','${this.jss(s.user_name)}')">看画面</button>
                  <button class="btn btn-sm btn-primary" onclick="App.messageModal('${s.user_id}','${this.jss(s.user_name)}')">发消息</button></td></tr>`;
          }).join("") + "</tbody></table>"
        : this.emptyHtml("暂无进行中的练习");
      const aEl = document.getElementById("live-alerts");
      if (aEl) aEl.innerHTML = d.alerts.length
        ? `<table><thead><tr><th>学员</th><th>班级/任务</th><th>得分</th><th>遗漏</th>
            <th>顺序错误</th><th>时间</th><th>操作</th></tr></thead><tbody>`
          + d.alerts.map(a => `<tr><td>${this.esc(a.user_name)}</td>
              <td>${this.esc(a.class_name || "-")}<br><span style="color:var(--muted);font-size:12px">${this.esc(a.task_name || "-")}</span></td>
              <td>${a.score ?? "--"}</td><td>${a.missed}</td><td>${a.order_errors}</td>
              <td>${new Date((a.started_at || 0) * 1000).toLocaleString()}</td>
              <td><button class="btn btn-sm" onclick="App.renderAssessmentReport('${a.record_id}')">报告</button>
                  <button class="btn btn-sm btn-primary" onclick="App.messageModal('${a.user_id}','${this.jss(a.user_name)}')">发消息</button></td></tr>`).join("")
          + "</tbody></table>"
        : this.emptyHtml("暂无完成后预警");
    } catch (e) {}
  },
  viewLiveFrame(sid, name) {
    this.openModal(`<h3>${this.esc(name)} · 实时画面</h3>
      <img id="live-frame-img" style="width:100%;border-radius:8px;background:#111"
        src="${this.frameUrl(sid)}">
      <div class="modal-actions"><button class="btn" onclick="App.closeModal()">关闭</button></div>`);
    if (this._frameModalTimer) clearInterval(this._frameModalTimer);
    this._frameModalTimer = setInterval(() => {
      const img = document.getElementById("live-frame-img");
      if (!img) { clearInterval(this._frameModalTimer); this._frameModalTimer = null; return; }
      img.src = this.frameUrl(sid);
    }, 300);
  },
  async showThresholds() {
    const t = await this.api("/api/dashboard/thresholds");
    this.openModal(`<h3>预警阈值设置</h3>
      <div class="form-row"><label>当前步骤停留（秒）标黄</label><input type="number" id="th-warn" value="${t.step_warn}"></div>
      <div class="form-row"><label>当前步骤停留（秒）标红</label><input type="number" id="th-crit" value="${t.step_crit}"></div>
      <div class="form-row"><label>离开画面（秒）提示</label><input type="number" id="th-off" value="${t.off}"></div>
      <div class="modal-actions"><button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn btn-primary" onclick="App.saveThresholds()">保存</button></div>`);
  },
  async saveThresholds() {
    const payload = { step_warn: Number(document.getElementById("th-warn").value),
      step_crit: Number(document.getElementById("th-crit").value),
      off: Number(document.getElementById("th-off").value) };
    try {
      await this.api("/api/dashboard/thresholds", "PUT", payload);
      this.closeModal(); this.refreshLive();
    } catch (e) { alert(e.message); }
  },
  messageModal(userId, name) {
    this.openModal(`<h3>联系 ${this.esc(name)}</h3>
      <div class="form-row"><label>内容</label><textarea id="msg-body" placeholder="填写提醒或点评内容"></textarea></div>
      <div class="modal-actions">
        <button class="btn" onclick="App.closeModal()">取消</button>
        <button class="btn" onclick="App.sendToUser('${userId}', true)">存为私密备注</button>
        <button class="btn btn-primary" onclick="App.sendToUser('${userId}', false)">发送消息</button>
      </div>`);
  },
  async sendToUser(userId, asNote) {
    const body = (document.getElementById("msg-body").value || "").trim();
    if (!body) { alert("请填写内容"); return; }
    try {
      await this.api("/api/messages", "POST",
        { to_user_id: userId, body, kind: asNote ? "note" : "message" });
      this.closeModal();
      if (!asNote) this.refreshUnread();
    } catch (e) { alert(e.message); }
  },
  async refreshUnread() {
    try {
      const d = await this.api("/api/messages/unread-count");
      const item = document.querySelector('.nav-item[data-view="messages"]');
      if (!item) return;
      item.innerHTML = d.count > 0
        ? `消息 <span class="tag tag-red" style="margin-left:6px">${d.count}</span>`
        : "消息";
    } catch (e) {}
  },
  async renderMessages() {
    const c = document.getElementById("content");
    const data = await this.api("/api/messages/conversations");
    this.state.msgPeer = this.state.msgPeer || null;
    c.innerHTML = `<div class="card"><div class="card-head"><h3>消息</h3></div>
      <div class="grid" style="grid-template-columns:280px 1fr;gap:16px">
        <div id="msg-list"></div>
        <div id="msg-thread" class="empty">选择左侧会话查看消息</div>
      </div></div>`;
    const list = document.getElementById("msg-list");
    list.innerHTML = data.items.length ? data.items.map(m => `
      <div class="step-item" style="cursor:pointer" onclick="App.openThread('${m.id}','${this.jss(m.name)}')">
        <div style="flex:1"><b>${this.esc(m.name)}</b>
          <div style="color:var(--muted);font-size:12px">${this.esc((m.last_body || "").slice(0, 16))}</div></div>
        ${m.unread ? `<span class="tag tag-red">${m.unread}</span>` : ""}
      </div>`).join("") : this.emptyHtml("暂无对话");
    if (this.state.msgPeer) this.openThread(this.state.msgPeer);
    this.refreshUnread();
  },
  async openThread(uid, name) {
    this.state.msgPeer = uid;
    const d = await this.api("/api/messages/thread/" + uid);
    const box = document.getElementById("msg-thread");
    if (!box) return;
    box.className = "";
    box.innerHTML = `<div style="max-height:420px;overflow-y:auto;padding-right:6px">
      ${(d.items || []).map(m => {
        const mine = m.from_user_id === (this.state.user && this.state.user.id);
        const note = m.kind === "note";
        return `<div style="margin:8px 0;text-align:${mine ? "right" : "left"}">
          <span class="tag ${note ? "tag-amber" : mine ? "tag-blue" : "tag-gray"}">${note ? "私密备注" : mine ? "我" : this.esc(d.user.name)}</span>
          <div style="margin-top:4px;white-space:pre-wrap">${this.esc(m.body || "")}</div>
          <div style="color:var(--muted);font-size:11px">${new Date((m.created_at || 0) * 1000).toLocaleString()}</div>
        </div>`;
      }).join("") || this.emptyHtml("暂无消息")}
    </div>
    <div class="form-row" style="margin-top:12px"><textarea id="reply-body" placeholder="输入回复内容"></textarea></div>
    <div style="text-align:right"><button class="btn btn-primary" onclick="App.sendReply('${uid}')">发送</button></div>`;
    this.refreshUnread();
  },
  async sendReply(uid) {
    const body = (document.getElementById("reply-body").value || "").trim();
    if (!body) { alert("请输入内容"); return; }
    try {
      await this.api("/api/messages", "POST", { to_user_id: uid, body });
      this.openThread(uid);
    } catch (e) { alert(e.message); }
  },
  async renderTv() {
    const c = document.getElementById("content");
    this.state.tvTab = this.state.tvTab || "sessions";
    c.innerHTML = `<div class="card">
      <div class="card-head"><h3 style="font-size:24px">教学大屏</h3>
        <div style="display:flex;gap:8px">
          <button class="btn" onclick="App.setTvTab('sessions')">总览</button>
          <button class="btn" onclick="App.setTvTab('alerts')">预警</button>
          <button class="btn" onclick="App.setTvTab('classes')">班级进度</button>
          <button class="btn" onclick="App.go('live')">退出大屏</button>
        </div></div>
      <div id="tv-body">加载中…</div></div>`;
    await this.refreshTv();
    if (this._tvTimer) clearInterval(this._tvTimer);
    this._tvTimer = setInterval(() => this.refreshTv(), 5000);
  },
  setTvTab(t) { this.state.tvTab = t; this.refreshTv(); },
  async refreshTv() {
    const d = await this.api("/api/dashboard/live");
    const tab = this.state.tvTab || "sessions";
    const box = document.getElementById("tv-body");
    if (!box) { if (this._tvTimer) { clearInterval(this._tvTimer); this._tvTimer = null; } return; }
    if (tab === "alerts") {
      box.innerHTML = d.alerts.length ? d.alerts.map(a => `
        <div class="tv-row tv-alert"><b>${this.esc(a.user_name)}</b> · ${this.esc(a.class_name || "")} ·
          得分 ${a.score ?? "--"} · 遗漏 ${a.missed} · 顺序错 ${a.order_errors}</div>`).join("")
        : this.emptyHtml("暂无预警");
    } else if (tab === "classes") {
      const cls = await this.api("/api/classes");
      const ongoing = {};
      d.sessions.forEach(s => { ongoing[s.class_name || ""] = (ongoing[s.class_name || ""] || 0) + 1; });
      box.innerHTML = `<table><thead><tr><th>班级</th><th>学员数</th><th>进行中</th></tr></thead><tbody>
        ${cls.items.map(x => `<tr><td class="tv-cell">${this.esc(x.name)}</td>
          <td class="tv-cell">${x.member_count}</td>
          <td class="tv-cell">${ongoing[x.name] || 0}</td></tr>`).join("") || `<tr><td colspan="3">${this.emptyHtml("暂无班级")}</td></tr>`}
      </tbody></table>`;
    } else {
      box.innerHTML = d.sessions.length ? d.sessions.map(s => `
        <div class="tv-row"><b>${this.esc(s.user_name)}</b> · ${this.esc(s.class_name || "自由练习")} ·
          ${this.esc(s.process_name)} · 第 ${(s.current_step ?? 0) + 1} 步 · 完成度 ${s.score ?? "--"}% ·
          ${s.alert_level === "ok" ? "正常" : s.alert_level === "warn" ? "卡住" : s.alert_level === "critical" ? "严重卡顿" : "离开画面"}</div>`).join("")
        : this.emptyHtml("暂无进行中的练习");
    }
  },
  async renderLibrary(tab) {
    this.state.libTab = tab || this.state.libTab || "processes";
    if (this.state.libTab === "actions") await this.renderActions();
    else await this.renderProcesses();
    const c = document.getElementById("content");
    if (!c) return;
    c.insertAdjacentHTML("afterbegin", `<div class="card" style="padding:10px 20px">
      <div style="display:flex;gap:8px">
        <button class="btn ${this.state.libTab === "processes" ? "btn-primary" : ""}"
          onclick="App.renderLibrary('processes')">护理流程</button>
        <button class="btn ${this.state.libTab === "actions" ? "btn-primary" : ""}"
          onclick="App.renderLibrary('actions')">动作模板</button>
      </div></div>`);
  },
  async renderArchive() {
    const c = document.getElementById("content");
    const cls = await this.api("/api/classes");
    c.innerHTML = `<div class="card"><div class="card-head"><h3>学员档案</h3>
      <div style="display:flex;gap:8px">
        <input id="archive-q" placeholder="搜索姓名或账号" style="padding:6px 10px;border:1px solid var(--border);border-radius:6px">
        <select id="archive-sort" onchange="App.loadArchive()" style="padding:6px 10px;border:1px solid var(--border);border-radius:6px">
          <option value="name">按姓名</option>
          <option value="avg_score">按平均分</option>
          <option value="last_at">按最近练习</option>
          <option value="warnings">按预警次数</option>
        </select>
        <select id="archive-class" onchange="App.loadArchive()">
          <option value="">请选择班级</option>
          ${cls.items.map(x => `<option value="${x.id}">${this.esc(x.name)}</option>`).join("")}
        </select>
      </div></div>
      <div id="archive-list">${this.emptyHtml("先选择班级")}</div></div>`;
  },
  async loadArchive() {
    const cid = document.getElementById("archive-class").value;
    const q = encodeURIComponent((document.getElementById("archive-q") || {}).value || "");
    const sort = (document.getElementById("archive-sort") || {}).value || "name";
    const box = document.getElementById("archive-list");
    if (!cid) { box.innerHTML = this.emptyHtml("先选择班级"); return; }
    const d = await this.api(`/api/teacher/students?class_id=${cid}&q=${q}&sort=${sort}`);
    box.innerHTML = `<table><thead><tr><th>学员</th><th>任务数</th><th>练习次数</th><th>平均分</th>
      <th>预警次数</th><th>最近练习</th><th>操作</th></tr></thead>
      <tbody>${d.items.map(s => `<tr>
        <td><b>${this.esc(s.name)}</b>（${this.esc(s.username)}）</td>
        <td>${s.tasks_done}</td><td>${s.records_count}</td>
        <td>${s.avg_score == null ? "--" : s.avg_score}</td>
        <td>${s.warnings || 0}</td>
        <td>${s.last_at ? new Date(s.last_at * 1000).toLocaleString() : "--"}</td>
        <td><button class="btn btn-sm btn-primary" onclick="App.loadStudentRecords('${s.id}')">查看记录</button>
            <button class="btn btn-sm" onclick="App.messageModal('${s.id}','${this.jss(s.name)}')">发消息</button></td>
      </tr>`).join("") || `<tr><td colspan="7">${this.emptyHtml("班级暂无学员")}</td></tr>`}</tbody></table>
      <div id="student-records"></div>`;
  },
});

// 点击遮罩关闭弹窗
document.getElementById("modal-mask").addEventListener("click", e => {
  if (e.target.id === "modal-mask") App.closeModal();
});
// 登录回车
document.addEventListener("keydown", e => {
  if (e.key === "Enter" && !document.getElementById("login-view").classList.contains("hidden")) App.login();
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && App.state.view === "tv") App.go("live");
});
