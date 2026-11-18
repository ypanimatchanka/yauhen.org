/* yauhen.org — progressive enhancements. Everything works without this file. */
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // the site root, wherever the site is hosted (data-root="/" is rewritten to a relative path at build time)
  var siteRoot = new URL(root.getAttribute("data-root") || "./", location.href);

  // ---- header: shadow on scroll, mobile menu, dropdowns
  var header = doc.querySelector("[data-header]");
  var onScroll = function () { header && header.classList.toggle("is-scrolled", window.scrollY > 8); };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
  var toggle = doc.querySelector(".nav-toggle"), nav = doc.getElementById("main-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      nav.classList.toggle("is-open", open);
    });
  }
  doc.querySelectorAll(".submenu-toggle").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var item = btn.closest(".has-children"), open = !item.classList.contains("is-open");
      item.classList.toggle("is-open", open);
      btn.setAttribute("aria-expanded", String(open));
    });
  });

  // ---- reveal on scroll
  var revealed = doc.querySelectorAll("[data-reveal]");
  if ("IntersectionObserver" in window && !reduced) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    revealed.forEach(function (el, i) { el.style.transitionDelay = (i % 4) * 60 + "ms"; io.observe(el); });
  } else {
    revealed.forEach(function (el) { el.classList.add("is-in"); });
  }

  // ---- hero slider (featured projects)
  doc.querySelectorAll("[data-slider]").forEach(function (box) {
    var slides = [].slice.call(box.querySelectorAll("[data-slide]"));
    var dots = [].slice.call(box.querySelectorAll("[data-goto]"));
    if (slides.length < 2) return;
    var ms = 7000, i = 0, timer = null, paused = false;
    box.style.setProperty("--slide-ms", ms + "ms");
    if (reduced) box.classList.add("is-static");
    function show(n) {
      i = (n + slides.length) % slides.length;
      slides.forEach(function (s, k) {
        var on = k === i;
        s.classList.toggle("is-active", on);
        s.setAttribute("aria-hidden", String(!on));
        s.querySelectorAll("a, button").forEach(function (el) { el.tabIndex = on ? 0 : -1; });
        var art = s.querySelector(".slide-art");
        if (art) art.tabIndex = -1;
      });
      dots.forEach(function (d, k) {
        if (k === i) { d.setAttribute("aria-current", "true"); } else { d.removeAttribute("aria-current"); }
        var bar = d.querySelector(".dot-bar i");  // restart the progress animation
        if (bar) { bar.style.transition = "none"; bar.offsetWidth; bar.style.transition = ""; }
      });
      schedule();
    }
    function schedule() {
      clearTimeout(timer);
      if (!paused && !reduced) timer = setTimeout(function () { show(i + 1); }, ms);
    }
    function pause(p) { paused = p; box.classList.toggle("is-paused", p); schedule(); }
    dots.forEach(function (d, k) { d.addEventListener("click", function () { show(k); }); });
    var prev = box.querySelector("[data-prev]"), next = box.querySelector("[data-next]");
    prev && prev.addEventListener("click", function () { show(i - 1); });
    next && next.addEventListener("click", function () { show(i + 1); });
    box.addEventListener("mouseenter", function () { pause(true); });
    box.addEventListener("mouseleave", function () { pause(false); });
    box.addEventListener("focusin", function () { pause(true); });
    box.addEventListener("focusout", function () { pause(false); });
    box.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight") show(i + 1);
      if (e.key === "ArrowLeft") show(i - 1);
    });
    var x0 = null;
    box.addEventListener("touchstart", function (e) { x0 = e.touches[0].clientX; }, { passive: true });
    box.addEventListener("touchend", function (e) {
      if (x0 === null) return;
      var dx = e.changedTouches[0].clientX - x0;
      if (Math.abs(dx) > 50) show(i + (dx < 0 ? 1 : -1));
      x0 = null;
    });
    doc.addEventListener("visibilitychange", function () { pause(doc.hidden); });
    show(0);
  });

  // ---- click-to-load players (nothing is loaded from Apple / YouTube before the click)
  var cover = (doc.querySelector(".prose[data-cover]") || {}).dataset;
  cover = cover && cover.cover ? new URL(cover.cover, location.href).href : "";
  var title = (doc.querySelector("h1") || {}).textContent || "";
  doc.querySelectorAll(".embed[data-provider]").forEach(function (el) {
    var yt = el.dataset.provider === "youtube";
    var bg = yt && el.dataset.thumb ? new URL(el.dataset.thumb, location.href).href : cover;
    var btn = doc.createElement("button");
    btn.type = "button";
    btn.className = "embed-facade";
    if (bg) btn.style.setProperty("--embed-bg", "url(\"" + bg + "\")");
    btn.innerHTML = '<span class="embed-play">▶</span><span class="embed-label"><small>' +
      (yt ? "YouTube" : "Apple Music") + "</small>" + (yt ? "Watch the video" : "Play " + title.trim()) +
      "<em>Loads the player from " + (yt ? "youtube-nocookie.com" : "music.apple.com") + "</em></span>";
    btn.addEventListener("click", function () { load(el); });
    el.appendChild(btn);
  });
  function load(el) {
    if (el.querySelector("iframe")) return;
    var f = doc.createElement("iframe");
    if (el.dataset.provider === "youtube") {
      f.src = "https://www.youtube-nocookie.com/embed/" + el.dataset.id + "?autoplay=1&rel=0";
      f.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
      f.title = "YouTube video";
    } else {
      var single = /[?&]i=\d/.test(el.dataset.src);
      f.src = el.dataset.src;
      f.height = single ? "175" : "450";
      f.allow = "autoplay *; encrypted-media *; fullscreen *; clipboard-write";
      f.sandbox = "allow-forms allow-popups allow-same-origin allow-scripts allow-storage-access-by-user-activation allow-top-navigation-by-user-activation";
      f.title = "Apple Music player";
    }
    f.loading = "lazy";
    el.innerHTML = "";
    el.style.minHeight = "0";
    el.appendChild(f);
  }
  doc.querySelectorAll("[data-play]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var el = doc.querySelector(".embed[data-provider]");
      if (!el) return;
      el.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
      el.classList.add("is-highlight");
      load(el);
    });
  });

  // ---- random-track mini player: plays a random YouTube track from the o0o.app pool (data/random-tracks.yaml)
  var poolEl = doc.getElementById("random-pool"), host = doc.getElementById("random-player");
  var pool = [];
  try { pool = poolEl ? JSON.parse(poolEl.textContent) : []; } catch (e) { pool = []; }
  var lastId = null, player = null;
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }
  function buildPlayer() {
    var qr = new URL(host.dataset.qr, location.href).href, link = host.dataset.link;
    host.className = "rp";
    host.setAttribute("role", "region");
    host.setAttribute("aria-label", "Random track player");
    host.innerHTML =
      '<div class="rp-head"><span class="rp-art"></span><div class="rp-meta"><small>Random track</small><strong class="rp-title"></strong></div>' +
      '<button class="rp-x" type="button" aria-label="Close player">×</button></div>' +
      '<div class="rp-video"></div>' +
      '<div class="rp-actions">' +
      '<button class="rp-btn rp-next" type="button">↻ Another one</button>' +
      '<a class="rp-btn rp-page" href="#">Story &amp; lyrics</a>' +
      '<a class="rp-btn rp-yt" href="#" target="_blank" rel="noopener">YouTube ↗</a>' +
      '<button class="rp-btn rp-qr-toggle" type="button" aria-expanded="false">On your phone</button></div>' +
      '<div class="rp-qr" hidden><img src="' + esc(qr) + '" alt="QR code: a random song" width="120" height="120">' +
      '<p>Scan for a random journey through sound — or open <a href="' + esc(link) + '" target="_blank" rel="noopener">' +
      esc(link.replace(/^https?:\/\//, "")) + "</a>. It’s safe: it only opens one of my songs.</p></div>";
    host.querySelector(".rp-x").addEventListener("click", closePlayer);
    host.querySelector(".rp-next").addEventListener("click", function () { playRandom(); });
    var t = host.querySelector(".rp-qr-toggle");
    t.addEventListener("click", function () {
      var box = host.querySelector(".rp-qr"), open = box.hidden;
      box.hidden = !open;
      t.setAttribute("aria-expanded", String(open));
    });
    doc.addEventListener("keydown", function (e) { if (e.key === "Escape" && !host.hidden) closePlayer(); });
    player = host;
  }
  function playRandom() {
    if (!player) buildPlayer();
    var choices = pool.filter(function (t) { return t.id !== lastId; });
    var t = choices[Math.floor(Math.random() * choices.length)] || pool[0];
    lastId = t.id;
    player.querySelector(".rp-title").textContent = t.title;
    var art = player.querySelector(".rp-art");
    art.innerHTML = t.cover ? '<img src="' + esc(new URL(t.cover, siteRoot).href) + '" alt="">' : "♪";
    var page = player.querySelector(".rp-page");
    page.hidden = !t.page;
    if (t.page) page.href = new URL(t.page, siteRoot).href;
    player.querySelector(".rp-yt").href = "https://www.youtube.com/watch?v=" + t.id;
    player.querySelector(".rp-video").innerHTML =
      '<iframe src="https://www.youtube-nocookie.com/embed/' + encodeURIComponent(t.id) +
      '?autoplay=1&rel=0&playsinline=1" title="' + esc(t.title) + '" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe>';
    player.hidden = false;
    requestAnimationFrame(function () { player.classList.add("is-open"); });
  }
  function closePlayer() {
    player.classList.remove("is-open");
    player.querySelector(".rp-video").innerHTML = "";   // stops the music
    setTimeout(function () { player.hidden = true; }, 250);
  }

  // ---- shuffle: random YouTube track in the mini player; without a pool, open a random song page
  var shuffleList = null;
  function shuffle() {
    if (pool.length && host) return playRandom();
    var go = function (list) {
      var here = location.href, pick, tries = 0;
      do { pick = list[Math.floor(Math.random() * list.length)]; tries++; }
      while (tries < 8 && new URL(pick.u, siteRoot).href === here);
      location.href = new URL(pick.u, siteRoot).href;
    };
    if (shuffleList) return go(shuffleList);
    fetch(new URL("search-index.json", siteRoot)).then(function (r) { return r.json(); }).then(function (docs) {
      shuffleList = docs.filter(function (d) { return d.l === "Song" || d.l === "Single"; });
      go(shuffleList.length ? shuffleList : docs);
    });
  }
  doc.querySelectorAll("[data-shuffle]").forEach(function (b) { b.addEventListener("click", shuffle); });

  // ---- filter chips (music page)
  doc.querySelectorAll("[data-filter-scope]").forEach(function (scope) {
    var chips = scope.querySelectorAll("[data-filter]"), cards = scope.querySelectorAll(".rcard");
    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        var f = chip.dataset.filter;
        chips.forEach(function (c) { c.classList.toggle("is-on", c === chip); });
        cards.forEach(function (card) {
          card.classList.toggle("is-hidden", f !== "all" && card.dataset.kind !== f);
          card.classList.add("is-in");
        });
      });
    });
  });

  // ---- subtle 3D tilt on release artwork (mouse only)
  if (!reduced && window.matchMedia("(pointer: fine)").matches) {
    doc.querySelectorAll(".release-art .art, .slide-art .art").forEach(function (art) {
      var box = art.parentElement;
      box.addEventListener("pointermove", function (e) {
        var r = box.getBoundingClientRect();
        var x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
        art.style.transform = "perspective(900px) rotateY(" + (x * 8) + "deg) rotateX(" + (-y * 8) + "deg)";
      });
      box.addEventListener("pointerleave", function () { art.style.transform = ""; });
    });
  }
})();
