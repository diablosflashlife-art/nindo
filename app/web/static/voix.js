/* ==========================================================================
   LA LECTURE À VOIX HAUTE

   DEUX VOIX, UNE SEULE LOGIQUE. Le serveur produit un PLAN — quoi dire, à
   quelle vitesse, à quel volume — et ce fichier le joue :
   - avec PIPER quand la voix réaliste est installée (le serveur fabrique
     l'audio de chaque phrase, voir app/voix_piper.py) ;
   - sinon avec la voix du navigateur, robotique mais toujours là.

   TROIS PRINCIPES.
   1. LE JEU NE DÉPEND PAS DE LA VOIX. Rien ne casse si elle manque.
   2. ON PARLE UNE CHOSE À LA FOIS. Un nouveau tour coupe le précédent.
   3. LE CHOIX DU JOUEUR SE GARDE, dans `localStorage`.
   ========================================================================== */
(function () {
  var synth = window.speechSynthesis;
  var bouton = document.getElementById('voix');
  if (!bouton) return;

  var CLE = 'chroniques.voix';
  var actif = false;
  try { actif = localStorage.getItem(CLE) === '1'; } catch (e) { actif = false; }

  var piper = false;          // la voix réaliste répond-elle ?
  var lecture = 0;            // numéro de la lecture en cours : en changer l'arrête
  var audio = null;           // l'élément audio qui parle

  /* LE VOLUME. Réglé par le joueur, gardé par le navigateur, appliqué aux
     deux voix. */
  var CLE_VOLUME = 'nindo.voix_volume';
  var volume = 1;
  try { volume = Math.max(0, Math.min(1, parseFloat(localStorage.getItem(CLE_VOLUME) || '1'))); }
  catch (e) { volume = 1; }
  var curseur = document.getElementById('voix-volume');
  if (curseur) {
    curseur.value = String(volume);
    curseur.addEventListener('input', function () {
      volume = parseFloat(curseur.value);
      if (audio) audio.volume = volume;
      try { localStorage.setItem(CLE_VOLUME, String(volume)); } catch (e) { /* tant pis */ }
    });
  }

  fetch('/voix/etat').then(function (r) { return r.json(); })
    .then(function (e) { piper = !!e.piper; montrer(); })
    .catch(montrer);

  function montrer() {
    if (!piper && !synth) return;          // aucune voix : pas de bouton
    bouton.hidden = false;
    accorder();
  }

  function voixFr() {
    var toutes = synth ? (synth.getVoices() || []) : [];
    return toutes.find(function (v) { return /^fr(-|_|$)/i.test(v.lang); })
        || toutes.find(function (v) { return /fran/i.test(v.name); })
        || null;
  }

  function taire() {
    lecture += 1;
    if (audio) { try { audio.pause(); } catch (e) { /* rien */ } audio = null; }
    if (synth) { try { synth.cancel(); } catch (e) { /* rien */ } }
  }

  /* ---- Piper : on prépare la phrase suivante pendant que la courante parle,
     pour qu'il n'y ait pas de blanc entre deux phrases. */
  function fabriquer(s) {
    return fetch('/voix/dire', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ texte: s.texte, vitesse: s.vitesse || 1, volume: s.volume === undefined ? 1 : s.volume })
    }).then(function (r) { return r.status === 200 ? r.blob() : null; });
  }

  /* UNE SEULE VOIX PAR LECTURE. Une phrase que Piper n'a pas pu dire est
     sautée, jamais confiée à la voix du navigateur : mesuré en partie, la
     voix réaliste commençait, puis la voix robotique prenait le relais au
     milieu du récit. */
  function direPiper(segments) {
    var ma = lecture;
    var utiles = segments.filter(function (s) { return s.texte; });
    var prochain = utiles.length ? fabriquer(utiles[0]) : null;
    (function suivant(i) {
      if (ma !== lecture || i >= utiles.length) return;
      var courant = prochain;
      prochain = i + 1 < utiles.length ? fabriquer(utiles[i + 1]) : null;
      courant.then(function (blob) {
        if (ma !== lecture) return;
        if (!blob) { suivant(i + 1); return; }
        audio = new Audio(URL.createObjectURL(blob));
        audio.volume = volume;
        audio.onended = function () {
          var pause = utiles[i].pause && utiles[i].pause > 1.05 ? (utiles[i].pause - 1) * 600 : 120;
          setTimeout(function () { suivant(i + 1); }, pause);
        };
        audio.onerror = function () { suivant(i + 1); };
        audio.play().catch(function () { /* lecture refusée par le navigateur */ });
      }).catch(function () { if (ma === lecture) suivant(i + 1); });
    })(0);
  }

  /* ---- La voix du navigateur, en secours. */
  function direNavigateur(segments) {
    if (!synth) return;
    var voix = voixFr();
    segments.forEach(function (s) {
      if (!s.texte) return;
      var u = new SpeechSynthesisUtterance(s.texte);
      if (voix) { u.voice = voix; u.lang = voix.lang; } else { u.lang = 'fr-FR'; }
      u.pitch = Math.max(0.1, Math.min(2, s.pitch || 1));
      u.rate = Math.max(0.5, Math.min(2, s.vitesse || 1));
      u.volume = Math.max(0, Math.min(1, (s.volume === undefined ? 1 : s.volume) * volume));
      synth.speak(u);
    });
  }

  function dire(segments) {
    taire();
    if (!segments || !segments.length) return;
    if (piper) direPiper(segments); else direNavigateur(segments);
  }

  function lireTour(campagne, tourId) {
    if (!actif || !tourId) return;
    fetch('/api/campagnes/' + campagne + '/voix/' + tourId)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) dire(d.segments); })
      .catch(function () { /* la voix n'est jamais une raison d'échouer */ });
  }

  function accorder() {
    bouton.classList.toggle('actif', actif);
    if (curseur) curseur.hidden = !actif;
    bouton.setAttribute('aria-pressed', actif ? 'true' : 'false');
    bouton.title = (actif ? 'Couper la lecture à voix haute' : 'Lire les scènes à voix haute')
      + (piper ? ' (voix réaliste)' : ' (voix du navigateur — installe la voix réaliste depuis le lanceur)');
  }

  bouton.addEventListener('click', function () {
    actif = !actif;
    try { localStorage.setItem(CLE, actif ? '1' : '0'); } catch (e) { /* tant pis */ }
    accorder();
    if (!actif) { taire(); return; }
    var dernier = document.querySelector('#recit .tour[data-id]');
    if (dernier) lireTour(dernier.dataset.campagne, dernier.dataset.id);
  });

  function surNouveauTour() {
    var tours = document.querySelectorAll('#recit .tour[data-id]');
    var dernier = tours[tours.length - 1];
    if (dernier) lireTour(dernier.dataset.campagne, dernier.dataset.id);
  }
  document.body.addEventListener('htmx:afterSwap', function (e) {
    if (e.target && e.target.id === 'recit') surNouveauTour();
  });
  document.addEventListener('chroniques:tour', surNouveauTour);

  document.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.tour-lire') : null;
    if (!b) return;
    e.preventDefault();
    var tour = b.closest('.tour');
    if (tour) lireTour(tour.dataset.campagne, tour.dataset.id);
  });

  addEventListener('pagehide', taire);
})();
