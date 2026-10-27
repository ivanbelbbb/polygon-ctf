/* Кнопка «Сдать задачу» - общая для всех уровней полигона.
 *
 * Как работает:
 *   1. Раз в POLL_MS спрашивает /__flag.json. Файл появляется, когда задача
 *      записала [FLAG_SUCCESS] и sidecar подготовил адрес WebSocket и токен.
 *   2. Когда файл есть - показывает плашку с кнопкой.
 *   3. Клик открывает WebSocket к sidecar и отправляет токен.
 *   4. Ответ {"status": "solved"} - плашка меняется на «Задача сдана».
 *      Состояние хранится в sidecar (flag.json, поле solved), после
 *      перезагрузки страницы плашка сразу показывает «уже сдано».
 */
(function () {
  "use strict";
  if (window.top !== window.self || window.__polygonSolveLoaded) return;
  window.__polygonSolveLoaded = true;

  var POLL_MS = 2000;
  var FLAG_URL = "/__flag.json";
  var box = null, button = null, status = null, state = null, busy = false;

  function build() {
    box = document.createElement("div");
    box.setAttribute("style",
      "position:fixed;right:16px;bottom:16px;z-index:2147483647;" +
      "max-width:320px;padding:14px 16px;border-radius:8px;" +
      "background:#161b22;color:#e6edf3;border:1px solid #30363d;" +
      "font:14px/1.4 system-ui,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.4)");

    var title = document.createElement("div");
    title.textContent = "Уязвимость найдена";
    title.setAttribute("style", "font-weight:600;margin-bottom:8px");

    button = document.createElement("button");
    button.type = "button";
    button.textContent = "Сдать задачу";
    button.setAttribute("style",
      "padding:8px 14px;border:0;border-radius:6px;cursor:pointer;" +
      "background:#238636;color:#fff;font:inherit");
    button.addEventListener("click", submit);

    status = document.createElement("div");
    status.setAttribute("style", "margin-top:8px;font-size:13px;color:#8b949e");

    box.appendChild(title);
    box.appendChild(button);
    box.appendChild(status);
    document.body.appendChild(box);
  }

  function setSolved() {
    button.disabled = true;
    button.style.background = "#30363d";
    button.style.cursor = "default";
    button.textContent = "Задача сдана";
    status.textContent = "Ожидайте следующий уровень от организатора.";
  }

  function wsUrl() {
    // хост берём из адресной строки (так работает и при доступе по другому
    // имени), порт - из flag.json
    if (state.ws_port) {
      var scheme = location.protocol === "https:" ? "wss://" : "ws://";
      return scheme + location.hostname + ":" + state.ws_port;
    }
    return state.ws;
  }

  function submit() {
    if (busy || !state) return;
    busy = true;
    button.disabled = true;
    status.textContent = "Отправка...";
    var done = false;
    var ws;
    try {
      ws = new WebSocket(wsUrl());
    } catch (e) {
      fail("Не удалось открыть соединение");
      return;
    }
    var timer = setTimeout(function () { fail("Нет ответа от сервера"); }, 10000);

    function fail(msg) {
      if (done) return;
      done = true;
      clearTimeout(timer);
      busy = false;
      button.disabled = false;
      status.textContent = msg + ". Попробуйте ещё раз.";
      try { ws && ws.close(); } catch (e) {}
    }

    ws.onopen = function () { ws.send(JSON.stringify({ token: state.token })); };
    ws.onerror = function () { fail("Ошибка подключения к серверу сдачи"); };
    ws.onmessage = function (ev) {
      if (done) return;
      var reply = {};
      try { reply = JSON.parse(ev.data); } catch (e) {}
      done = true;
      clearTimeout(timer);
      busy = false;
      try { ws.close(); } catch (e) {}
      if (reply.status === "solved") {
        state.solved = true;
        setSolved();
      } else {
        button.disabled = false;
        status.textContent = "Сервер отклонил токен. Попробуйте ещё раз.";
      }
    };
  }

  function poll() {
    fetch(FLAG_URL, { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (data && data.token) {
          state = data;
          if (!box) build();
          if (state.solved) setSolved();
        }
      })
      .catch(function () { /* файла ещё нет или сеть моргнула - ждём */ })
      .then(function () {
        // после того как плашка показана и задача сдана, опрос больше не нужен
        if (!(state && state.solved)) setTimeout(poll, POLL_MS);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", poll);
  } else {
    poll();
  }
})();
