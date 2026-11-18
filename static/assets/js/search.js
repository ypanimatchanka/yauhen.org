/* Client-side search over search-index.json: titles, stories and lyrics. */
(function () {
  "use strict";
  var box = document.getElementById("search-results");
  var status = document.getElementById("search-status");
  var input = document.getElementById("q");
  if (!box) return;
  var indexUrl = new URL(box.getAttribute("data-index"), location.href);
  var root = new URL("./", indexUrl);
  var query = (new URLSearchParams(location.search).get("q") || "").trim();
  input.value = query;
  input.focus();
  if (!query) { status.textContent = "Type a title, a word or a line you remember."; return; }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function norm(s) { return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }
  function mark(text, terms) {
    var out = esc(text);
    terms.forEach(function (t) {
      if (t.length > 1) out = out.replace(new RegExp("(" + t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi"), "<mark>$1</mark>");
    });
    return out;
  }
  function snippet(d, terms) {
    var text = d.x || "", low = norm(text);
    for (var i = 0; i < terms.length; i++) {
      var at = low.indexOf(terms[i]);
      if (at > -1) {
        var s = Math.max(0, at - 60);
        return (s ? "…" : "") + text.slice(s, s + 160) + "…";
      }
    }
    return d.s || "";
  }

  status.textContent = "Searching…";
  fetch(indexUrl).then(function (r) { return r.json(); }).then(function (docs) {
    var phrase = norm(query), terms = phrase.split(/\s+/).filter(Boolean);
    var hits = docs.map(function (d) {
      var t = norm(d.t), k = norm((d.k || []).join(" ")), s = norm(d.s), x = norm(d.x), score = 0;
      if (x.indexOf(phrase) > -1 && terms.length > 1) score += 25;   // an exact lyric line
      for (var i = 0; i < terms.length; i++) {
        var w = terms[i], p = 0;
        if (t.indexOf(w) > -1) p += 12;
        if (k.indexOf(w) > -1) p += 5;
        if (s.indexOf(w) > -1) p += 3;
        if (x.indexOf(w) > -1) p += 1;
        if (!p) return null;
        score += p;
      }
      return { d: d, score: score };
    }).filter(Boolean).sort(function (a, b) { return b.score - a.score; });

    status.textContent = hits.length
      ? hits.length + (hits.length === 1 ? " result" : " results") + " for “" + query + "”"
      : "Nothing found for “" + query + "”. Try fewer words.";
    box.innerHTML = hits.map(function (h) {
      var d = h.d, href = new URL(d.u, root).href;
      var art = d.c ? '<img class="art" src="' + esc(new URL(d.c, root).href) + '" alt="" loading="lazy">'
                    : '<span class="art art-placeholder" aria-hidden="true">♪</span>';
      return '<article class="rcard is-in"><a class="rcard-link" href="' + esc(href) + '">' +
        '<span class="rcard-art">' + art + "</span>" +
        '<span class="rcard-body"><span class="rcard-kind">' + esc(d.l || "Page") + (d.y ? " · " + d.y : "") + "</span>" +
        '<span class="rcard-title">' + mark(d.t, terms) + "</span>" +
        '<span class="rcard-snippet">' + mark(snippet(d, terms), terms) + "</span></span></a></article>";
    }).join("");
  }).catch(function () { status.textContent = "Search is unavailable right now."; });
})();
