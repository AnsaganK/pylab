(function () {
  "use strict";

  // Код только для чтения (отправки)
  document.querySelectorAll("textarea.code-view").forEach(function (ta) {
    var cm = CodeMirror.fromTextArea(ta, {
      mode: "python", readOnly: true, lineNumbers: true, viewportMargin: Infinity,
    });
    cm.getWrapperElement().classList.add("view");
  });

  var bench = document.getElementById("workbench");
  if (!bench) return;

  var csrf = bench.querySelector("input[name=csrfmiddlewaretoken]").value;
  // Черновики привязаны к студенту: за одним ПК сидят разные люди
  var keyBase = "pylab:" + bench.dataset.user + ":" + bench.dataset.task;
  var draftKey = keyBase + ":draft";
  var stdinKey = keyBase + ":stdin";
  var FONT_KEY = "pylab:font";
  var codeTa = document.getElementById("code");
  var stdin = document.getElementById("stdin");
  var out = document.getElementById("out");
  var meta = document.getElementById("run-meta");
  var saveState = document.getElementById("save-state");
  var btnRun = document.getElementById("btn-run");
  var btnSubmit = document.getElementById("btn-submit");
  var io = document.getElementById("io");

  function store(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }
  function load(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }

  var UNIT = 4;

  // Backspace в отступе удаляет его целиком до предыдущего уровня, как таб
  function smartBackspace(cm) {
    if (cm.somethingSelected()) return CodeMirror.Pass;
    var sels = cm.listSelections();
    for (var i = 0; i < sels.length; i++) {
      var pos = sels[i].head;
      var before = cm.getLine(pos.line).slice(0, pos.ch);
      if (pos.ch === 0 || /[^ ]/.test(before)) return CodeMirror.Pass;
    }
    cm.operation(function () {
      cm.listSelections().slice().reverse().forEach(function (sel) {
        var pos = sel.head;
        var target = Math.floor((pos.ch - 1) / UNIT) * UNIT;
        cm.replaceRange("", CodeMirror.Pos(pos.line, target), pos);
      });
    });
  }

  // Delete перед отступом тоже съедает его уровнем
  function smartDelete(cm) {
    if (cm.somethingSelected()) return CodeMirror.Pass;
    var pos = cm.getCursor();
    var line = cm.getLine(pos.line);
    var before = line.slice(0, pos.ch), after = line.slice(pos.ch);
    if (/[^ ]/.test(before) || !/^ /.test(after)) return CodeMirror.Pass;
    var spaces = after.match(/^ */)[0].length;
    var n = Math.min(spaces, UNIT - (pos.ch % UNIT));
    cm.replaceRange("", pos, CodeMirror.Pos(pos.line, pos.ch + n));
  }

  // Tab: с выделением — сдвиг блока; иначе — пробелы до следующей позиции табуляции
  function smartTab(cm) {
    if (cm.somethingSelected()) { cm.indentSelection("add"); return; }
    var ch = cm.getCursor().ch;
    cm.replaceSelection(" ".repeat(UNIT - (ch % UNIT)), "end");
  }

  // Enter: после return/pass/break/continue/raise следующая строка на уровень левее
  function smartEnter(cm) {
    cm.execCommand("newlineAndIndent");
    var cur = cm.getCursor();
    if (cur.line === 0) return;
    var prev = cm.getLine(cur.line - 1);
    var m = /^( *)(return\b|pass\b|break\b|continue\b|raise\b)/.exec(prev);
    if (m && cur.ch >= m[1].length && m[1].length > 0) {
      var target = m[1].length - UNIT;
      cm.replaceRange(" ".repeat(Math.max(target, 0)), CodeMirror.Pos(cur.line, 0), CodeMirror.Pos(cur.line, cur.ch));
    }
  }

  function duplicateLine(cm) {
    var cur = cm.getCursor();
    var from = cm.somethingSelected() ? cm.getCursor("from").line : cur.line;
    var to = cm.somethingSelected() ? cm.getCursor("to").line : cur.line;
    var text = cm.getRange(CodeMirror.Pos(from, 0), CodeMirror.Pos(to));
    cm.replaceRange("\n" + text, CodeMirror.Pos(to));
    cm.setCursor(CodeMirror.Pos(cur.line + (to - from) + 1, cur.ch));
  }

  function moveLine(cm, dir) {
    var from = cm.getCursor("from").line, to = cm.getCursor("to").line;
    if ((dir < 0 && from === 0) || (dir > 0 && to === cm.lastLine())) return;
    var head = cm.getCursor("head"), anchor = cm.getCursor("anchor");
    cm.operation(function () {
      if (dir < 0) {
        var above = cm.getLine(from - 1);
        cm.replaceRange("", CodeMirror.Pos(from - 1, 0), CodeMirror.Pos(from, 0));
        cm.replaceRange("\n" + above, CodeMirror.Pos(to - 1));
      } else {
        var below = cm.getLine(to + 1);
        cm.replaceRange("", CodeMirror.Pos(to), CodeMirror.Pos(to + 1));
        cm.replaceRange(below + "\n", CodeMirror.Pos(from, 0));
      }
      cm.setSelection(CodeMirror.Pos(anchor.line + dir, anchor.ch), CodeMirror.Pos(head.line + dir, head.ch));
    });
  }

  function flash(text) {
    saveState.textContent = text;
    clearTimeout(flash.t);
    flash.t = setTimeout(function () { saveState.textContent = ""; }, 1800);
  }

  var editor = CodeMirror.fromTextArea(codeTa, {
    mode: { name: "python", version: 3 },
    lineNumbers: true, indentUnit: UNIT, tabSize: UNIT, indentWithTabs: false,
    matchBrackets: true, autoCloseBrackets: true, styleActiveLine: true,
    extraKeys: {
      "Tab": smartTab,
      "Shift-Tab": function (cm) { cm.indentSelection("subtract"); },
      "Backspace": smartBackspace,
      "Delete": smartDelete,
      "Enter": smartEnter,
      "Ctrl-/": "toggleComment", "Cmd-/": "toggleComment",
      "Ctrl-D": duplicateLine, "Cmd-D": duplicateLine,
      "Alt-Up": function (cm) { moveLine(cm, -1); },
      "Alt-Down": function (cm) { moveLine(cm, 1); },
      "Ctrl-S": function () { store(draftKey, editor.getValue()); flash("Черновик сохранён"); },
      "Cmd-S": function () { store(draftKey, editor.getValue()); flash("Черновик сохранён"); },
      "Ctrl-Enter": function () { run(); }, "Cmd-Enter": function () { run(); },
    },
  });

  // Вставленный код с табами сразу превращаем в пробелы — иначе TabError
  editor.on("beforeChange", function (cm, change) {
    if (change.origin === "paste" && change.text.some(function (t) { return t.indexOf("\t") >= 0; })) {
      change.update(null, null, change.text.map(function (t) { return t.replace(/\t/g, "    "); }));
    }
  });

  // Размер шрифта (удобно на проекторе)
  var fontSize = parseInt(load(FONT_KEY), 10) || 14;
  function applyFont() {
    editor.getWrapperElement().style.fontSize = fontSize + "px";
    editor.refresh();
  }
  applyFont();
  document.getElementById("font-minus").addEventListener("click", function () { fontSize = Math.max(11, fontSize - 1); store(FONT_KEY, fontSize); applyFont(); });
  document.getElementById("font-plus").addEventListener("click", function () { fontSize = Math.min(28, fontSize + 1); store(FONT_KEY, fontSize); applyFont(); });

  // Черновик
  var draft = load(draftKey);
  if (draft !== null && draft !== editor.getValue()) editor.setValue(draft);
  var savedStdin = load(stdinKey);
  if (savedStdin !== null) stdin.value = savedStdin;
  editor.on("change", function () { store(draftKey, editor.getValue()); });
  stdin.addEventListener("input", function () { store(stdinKey, stdin.value); });
  stdin.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); run(); }
  });

  document.getElementById("btn-reset").addEventListener("click", function () {
    if (!confirm("Заменить код в редакторе начальным? Текущий черновик пропадёт.")) return;
    editor.setValue(document.getElementById("starter").value);
    editor.focus();
  });

  var help = document.getElementById("btn-help");
  help.addEventListener("click", function () {
    var keys = document.getElementById("keys");
    keys.hidden = !keys.hidden;
    help.setAttribute("aria-expanded", String(!keys.hidden));
  });

  // Задачи без ввода: поле stdin спрятано, но его можно открыть
  var showStdin = document.getElementById("show-stdin");
  var hideStdin = document.getElementById("hide-stdin");
  if (showStdin) {
    if (stdin.value.trim()) io.classList.add("show-input");
    showStdin.addEventListener("click", function () { io.classList.add("show-input"); stdin.focus(); });
    hideStdin.addEventListener("click", function () { io.classList.remove("show-input"); });
  }

  document.querySelectorAll("[data-to-stdin]").forEach(function (b) {
    b.addEventListener("click", function () {
      var pre = document.querySelectorAll(".sample-in")[+b.dataset.toStdin];
      stdin.value = pre.textContent;
      store(stdinKey, stdin.value);
      stdin.focus();
    });
  });

  function post(url, data) {
    var body = new FormData();
    Object.keys(data).forEach(function (k) { body.append(k, data[k]); });
    return fetch(url, { method: "POST", body: body, headers: { "X-CSRFToken": csrf }, credentials: "same-origin" })
      .then(function (r) {
        return r.json().catch(function () { return { error: "Ошибка сервера (" + r.status + ")." }; });
      });
  }

  function show(parts) {
    out.innerHTML = "";
    parts.forEach(function (p) {
      if (!p.text) return;
      var el = document.createElement(p.hint ? "p" : "pre");
      if (p.err) el.className = "err";
      if (p.hint) el.className = "run-hint";
      el.textContent = p.text;
      out.appendChild(el);
    });
    if (!out.childNodes.length) out.innerHTML = '<span class="muted">Программа ничего не вывела.</span>';
  }

  function run() {
    if (btnRun.disabled) return;
    btnRun.disabled = true;
    meta.textContent = "выполняется…";
    post(bench.dataset.runUrl, { code: editor.getValue(), stdin: stdin.value }).then(function (r) {
      if (r.error) { show([{ text: r.error, err: true }]); meta.textContent = ""; return; }
      show([{ text: r.stdout }, { text: r.stderr, err: true }, { text: r.hint, hint: true }]);
      meta.textContent = r.label + (r.time ? ", " + r.time.toFixed(2) + " с" : "");
    }).catch(function () {
      show([{ text: "Нет связи с сервером.", err: true }]); meta.textContent = "";
    }).finally(function () { btnRun.disabled = false; });
  }

  // Отправки и статус задачи обновляются без перезагрузки
  var subsBox = document.getElementById("subs");
  var state = document.getElementById("task-state");
  var pill = document.querySelector(".siblings a.on");
  var pollTimer = null;
  function applyState() {
    var box = subsBox.querySelector("[data-best]");
    var tpl = subsBox.querySelector("template.state-tpl");
    if (tpl && state) state.innerHTML = tpl.innerHTML;
    if (pill && box) {
      pill.classList.remove("solved", "failed", "waiting");
      if (box.dataset.best) pill.classList.add(box.dataset.best);
    }
  }
  function refreshSubs() {
    fetch(subsBox.dataset.url, { credentials: "same-origin" }).then(function (r) { return r.text(); })
      .then(function (html) {
        subsBox.innerHTML = html;
        applyState();
        clearTimeout(pollTimer);
        if (subsBox.querySelector("[data-busy='1']")) pollTimer = setTimeout(refreshSubs, 1200);
      });
  }
  if (subsBox.querySelector("[data-busy='1']")) refreshSubs();

  btnSubmit.addEventListener("click", function () {
    if (btnSubmit.disabled) return;
    btnSubmit.disabled = true;
    post(bench.dataset.submitUrl, { code: editor.getValue() }).then(function (r) {
      if (r.error) { show([{ text: r.error, err: true }]); return; }
      flash("Отправлено");
      refreshSubs();
      subsBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }).catch(function () {
      show([{ text: "Нет связи с сервером.", err: true }]);
    }).finally(function () { setTimeout(function () { btnSubmit.disabled = false; }, 1500); });
  });
  btnRun.addEventListener("click", run);
})();
