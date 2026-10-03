/* Bewusst ES3: läuft auf Kindle 4 (WebKit 533), iPad 2 (iOS 9) und allem Neueren.
   Kein let/const, keine Pfeilfunktionen, kein fetch, keine WebSockets. */
(function () {
  var D = window.DASH;
  if (!D || !window.XMLHttpRequest) { return; }

  var timer = null, failures = 0;

  function $(id) { return document.getElementById(id); }

  function parse(t) {
    try { return window.JSON ? JSON.parse(t) : eval("(" + t + ")"); } catch (e) { return null; }
  }

  function setText(el, t) {
    if (!el) { return; }
    while (el.firstChild) { el.removeChild(el.firstChild); }
    el.appendChild(document.createTextNode(t));
  }

  function xhr(method, url, body, cb) {
    var r = new XMLHttpRequest();
    r.open(method, url, true);
    r.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    if (body) { r.setRequestHeader("Content-Type", "application/x-www-form-urlencoded"); }
    r.onreadystatechange = function () {
      if (r.readyState === 4) { cb(r.status, r.responseText); }
    };
    r.send(body || null);
  }

  function msg(t, bad) {
    var el = $("msg");
    setText(el, t);
    el.className = bad ? "bad" : "ok";
    setTimeout(function () { setText(el, ""); el.className = ""; }, 4000);
  }

  function apply(data) {
    var k, s, card, img, fill, imgs, i, src;
    for (k in data.w) {
      if (!data.w.hasOwnProperty(k)) { continue; }
      s = data.w[k];
      card = $("w" + k);
      if (!card) { continue; }
      card.className = card.getAttribute("data-base") + " " + s.c;
      setText($("v" + k), s.t);
      img = $("i" + k);
      if (img && s.i && img.getAttribute("src") !== s.i) { img.src = s.i; img.alt = s.t; }
      fill = $("p" + k);
      if (fill) { fill.style.width = s.p + "%"; }
    }
    if (data.cb !== D.cb) {
      D.cb = data.cb;
      imgs = document.getElementsByTagName("img");
      for (i = 0; i < imgs.length; i++) {
        src = imgs[i].getAttribute("data-src");
        if (src) { imgs[i].src = src + "&t=" + data.cb; }
      }
    }
    setText($("stamp"), "Stand " + data.now + (data.mqtt ? "" : " · MQTT getrennt"));
    $("stamp").className = data.mqtt ? "stamp" : "stamp bad";
  }

  function poll() {
    clearTimeout(timer);
    xhr("GET", D.state + "?_=" + new Date().getTime(), null, function (status, text) {
      var data = status === 200 ? parse(text) : null;
      if (data && data.w) {
        failures = 0;
        apply(data);
      } else if (status === 200 || status === 401 || status === 403) {
        window.location.reload(); /* Sitzung abgelaufen -> Login-Seite */
        return;
      } else {
        failures++;
        setText($("stamp"), "Server nicht erreichbar");
        $("stamp").className = "stamp bad";
      }
      timer = setTimeout(poll, D.refresh * 1000 * (failures > 3 ? 3 : 1));
    });
  }

  function bind(form) {
    form.onsubmit = function () {
      var q = form.getAttribute("data-confirm");
      if (q && !window.confirm(q)) { return false; }
      var btn = form.getElementsByTagName("button")[0];
      if (btn) { btn.disabled = true; }
      xhr("POST", form.action, "ajax=1&csrf=" + encodeURIComponent(D.csrf), function (status, text) {
        var r = parse(text);
        if (btn) { btn.disabled = false; }
        msg(r && r.msg ? r.msg : "Fehler " + status, !(r && r.ok));
        setTimeout(poll, 700);
      });
      return false;
    };
  }

  /* Diagramme in der tatsächlichen Breite anfordern (scharf und lesbar, auch auf 600px-Kindle) */
  function sizeCharts() {
    var imgs = document.getElementsByTagName("img"), j, src, w;
    for (j = 0; j < imgs.length; j++) {
      src = imgs[j].getAttribute("data-src");
      w = imgs[j].parentNode.offsetWidth - 12;
      if (src && w > 150) {
        src = src.replace(/w=\d+/, "w=" + w);
        imgs[j].setAttribute("data-src", src);
        imgs[j].src = src + "&t=" + D.cb;
      }
    }
  }
  sizeCharts();

  var forms = document.getElementsByTagName("form"), i;
  for (i = 0; i < forms.length; i++) {
    if (/(^| )act( |$)/.test(forms[i].className)) { bind(forms[i]); }
  }
  timer = setTimeout(poll, D.refresh * 1000);
})();
