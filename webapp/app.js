/* Логика мини-приложения.
 *
 * Экранов три: выбор темы, анкета, результат. Данные берём с того же
 * сервера, который отдал эту страницу (bot/webapp.py); подпись Telegram
 * (initData) уходит с каждым запросом — сервер по ней проверяет, что
 * запрос действительно из Telegram, а не набран руками в браузере.
 */
"use strict";

var tg = window.Telegram && window.Telegram.WebApp;
var state = { sphere: null, place: null, places: [], screen: "home" };

/* --- мелкие помощники --------------------------------------------------- */

function $(id) { return document.getElementById(id); }

function haptic(style) {
  try { tg.HapticFeedback.impactOccurred(style || "light"); } catch (e) {}
}

function show(screenId) {
  var screens = document.querySelectorAll(".screen");
  for (var i = 0; i < screens.length; i++) screens[i].classList.remove("is-active");
  $(screenId).classList.add("is-active");
  window.scrollTo(0, 0);
  state.screen = screenId;

  if (!tg) return;
  if (screenId === "screen-home") tg.BackButton.hide();
  else tg.BackButton.show();
}

function loading(on, text) {
  $("loader-text").textContent = text || "Считаю карту…";
  $("loader").classList.toggle("is-active", !!on);
}

function api(path, payload) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(Object.assign(
      { initData: (tg && tg.initData) || "" }, payload || {}))
  }).then(function (response) {
    return response.json().then(function (data) {
      if (!response.ok || data.error) throw new Error(data.error || "Сервер недоступен");
      return data;
    });
  });
}

/* --- фон ---------------------------------------------------------------- */

function drawStars() {
  var box = $("stars"), html = "";
  for (var i = 0; i < 40; i++) {
    html += '<i style="left:' + (Math.random() * 100).toFixed(2) + '%;top:' +
            (Math.random() * 100).toFixed(2) + '%;animation-delay:' +
            (Math.random() * 4).toFixed(2) + 's"></i>';
  }
  box.innerHTML = html;
}

/* --- главный экран ------------------------------------------------------ */

var TOOLS = [
  { id: "chart", icon: "🪐", title: "Натальная карта", hint: "Планеты, дома, аспекты" },
  { id: "week", icon: "📅", title: "Прогноз на неделю", hint: "Карта на каждый день" },
  { id: "year", icon: "🗓", title: "Прогноз на год", hint: "Тема года и месяцы" },
  { id: "transits", icon: "⏳", title: "Транзиты", hint: "Что происходит сейчас" }
];

function renderHome(spheres) {
  var grid = $("spheres");
  grid.innerHTML = "";
  spheres.forEach(function (sphere) {
    var button = document.createElement("button");
    button.className = "tile";
    button.innerHTML =
      '<span class="tile__emoji">' + sphere.emoji + "</span>" +
      '<span class="tile__title">' + sphere.title + "</span>" +
      '<span class="tile__hint">' + sphere.hint + "</span>";
    button.addEventListener("click", function () {
      haptic("medium");
      openForm(sphere);
    });
    grid.appendChild(button);
  });

  var tools = $("tools");
  tools.innerHTML = "";
  TOOLS.forEach(function (tool) {
    var button = document.createElement("button");
    button.className = "tool";
    button.innerHTML =
      '<span class="tool__icon">' + tool.icon + "</span>" +
      "<span><span class=\"tool__title\">" + tool.title + "</span><br>" +
      '<span class="tool__hint">' + tool.hint + "</span></span>" +
      '<span class="tool__arrow">›</span>';
    button.addEventListener("click", function () {
      haptic("medium");
      openForm({ key: tool.id, emoji: tool.icon, title: tool.title, tool: true });
    });
    tools.appendChild(button);
  });
}

/* --- анкета ------------------------------------------------------------- */

function openForm(sphere) {
  state.sphere = sphere;
  $("form-emoji").textContent = sphere.emoji;
  $("form-title").textContent = sphere.title;
  $("input-question").closest(".field").style.display = sphere.tool ? "none" : "block";
  show("screen-form");
  setMainButton(sphere.tool ? "Показать" : "Получить разбор", submit);
}

function setMainButton(text, handler) {
  if (!tg) return;
  tg.MainButton.setText(text);
  tg.MainButton.offClick(setMainButton.current);
  setMainButton.current = handler;
  tg.MainButton.onClick(handler);
  tg.MainButton.show();
}

function hideMainButton() { if (tg) tg.MainButton.hide(); }

var cityTimer = null;

function onCityInput() {
  clearTimeout(cityTimer);
  var query = $("input-city").value.trim();
  state.place = null;
  if (query.length < 2) { $("city-options").innerHTML = ""; return; }
  cityTimer = setTimeout(function () {
    api("/api/places", { query: query }).then(function (data) {
      state.places = data.places || [];
      var box = $("city-options");
      box.innerHTML = "";
      state.places.forEach(function (place, index) {
        var button = document.createElement("button");
        button.className = "option";
        button.type = "button";
        button.innerHTML = place.label + "<small>" + place.timezone + "</small>";
        button.addEventListener("click", function () {
          haptic("light");
          state.place = index;
          var chosen = box.querySelectorAll(".option");
          for (var i = 0; i < chosen.length; i++) chosen[i].classList.remove("is-chosen");
          button.classList.add("is-chosen");
          $("city-hint").textContent = "Координаты и пояс: " + place.timezone;
        });
        box.appendChild(button);
      });
      if (!state.places.length) {
        $("city-hint").textContent = "Такого города не нашёл — попробуйте иначе";
      }
    }).catch(function () {});
  }, 350);
}

function submit() {
  var birth = $("input-birth").value.trim();
  var field = $("input-birth");
  if (!birth) {
    field.classList.add("is-error");
    field.focus();
    if (tg) tg.HapticFeedback.notificationOccurred("error");
    return;
  }
  field.classList.remove("is-error");

  var payload = {
    sphere: state.sphere.key,
    tool: !!state.sphere.tool,
    name: $("input-name").value.trim(),
    birth: birth,
    time: $("input-time").value.trim(),
    question: $("input-question").value.trim(),
    place: state.place !== null ? state.places[state.place] : null
  };

  hideMainButton();
  loading(true, state.sphere.tool ? "Строю карту…" : "Раскладываю карты…");

  api("/api/reading", payload).then(function (data) {
    loading(false);
    renderResult(data);
    if (tg) tg.HapticFeedback.notificationOccurred("success");
  }).catch(function (error) {
    loading(false);
    renderError(error.message);
    setMainButton("Попробовать снова", submit);
  });
}

/* --- результат ---------------------------------------------------------- */

function renderResult(data) {
  var box = $("result");
  box.innerHTML = "";

  (data.blocks || []).forEach(function (block) {
    var card = document.createElement("div");
    card.className = "card" + (block.hero ? " card--hero" : "")
      + (block.note ? " card--note" : "");
    var html = "";
    if (block.hero) {
      html += '<div class="card__emoji">' + block.emoji + "</div>";
      html += "<h2>" + block.title + "</h2>";
      html += '<div class="card__meta">' + block.body + "</div>";
    } else {
      if (block.title) html += '<h3 class="card__title">' + block.title + "</h3>";
      html += block.body;
    }
    card.innerHTML = html;
    box.appendChild(card);
  });

  show("screen-result");
  hideMainButton();
}

function renderError(message) {
  var box = $("result");
  box.innerHTML = '<div class="error-note">' + message + "</div>";
  show("screen-result");
}

/* --- запуск ------------------------------------------------------------- */

function init() {
  drawStars();

  if (tg && tg.initData !== undefined && tg.platform !== "unknown") {
    document.body.classList.add("in-telegram");
  }
  $("btn-submit").addEventListener("click", function () {
    haptic("medium");
    submit();
  });

  if (tg) {
    tg.ready();
    tg.expand();
    try { tg.setHeaderColor("bg_color"); } catch (e) {}
    tg.BackButton.onClick(function () {
      if (state.screen === "screen-result") show("screen-form");
      else { show("screen-home"); hideMainButton(); }
    });
  }

  $("input-city").addEventListener("input", onCityInput);
  $("btn-again").addEventListener("click", function () {
    haptic("light");
    show("screen-home");
    hideMainButton();
  });
  var backs = document.querySelectorAll("[data-back]");
  for (var i = 0; i < backs.length; i++) {
    backs[i].addEventListener("click", function () {
      show("screen-home");
      hideMainButton();
    });
  }

  api("/api/spheres", {}).then(function (data) {
    renderHome(data.spheres || []);
    if (data.note) $("footnote").textContent = data.note;
    if (data.profile) {
      $("input-name").value = data.profile.name || "";
      $("input-birth").value = data.profile.birth || "";
      $("input-time").value = data.profile.time || "";
      if (data.profile.city) {
        $("input-city").value = data.profile.city;
        $("city-hint").textContent = "Сохранено с прошлого раза";
      }
    }
  }).catch(function (error) {
    document.body.insertAdjacentHTML("afterbegin",
      '<div class="error-note">Не удалось связаться с сервером: ' +
      error.message + "</div>");
  });
}

document.addEventListener("DOMContentLoaded", init);
