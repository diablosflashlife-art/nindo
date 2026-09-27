/* ==========================================================================
   LA LECTURE À VOIX HAUTE

   Le navigateur embarque un synthétiseur branché sur les voix du système :
   hors ligne, sans rien à installer, sans dépendance Python ni modèle vocal
   à télécharger. Le serveur produit un PLAN — quoi dire, avec quelle hauteur,
   à quelle vitesse — et ce fichier le joue.

   TROIS PRINCIPES.

   1. LE JEU NE DÉPEND PAS DE LA VOIX. Si `speechSynthesis` manque, le bouton
      ne s'affiche pas et rien d'autre ne change. C'est un agrément.

   2. ON PARLE UNE CHOSE À LA FOIS. Un nouveau tour coupe le précédent :
      entendre deux scènes se chevaucher est pire que le silence.

   3. LE CHOIX DU JOUEUR SE GARDE. Il est dans `localStorage` — et son absence
      (navigation privée, données effacées) se lit simplement comme « éteint ».
   ========================================================================== */
(function () {
  var synth = window.speechSynthesis;
  if (!synth) return;

  var CLE = 'chroniques.voix';
  var actif = false;
  try { actif = localStorage.getItem(CLE) === '1'; } catch (e) { actif = false; }

  var bouton = document.getElementById('voix');
  if (!bouton) return;
  bouton.hidden = false;

  /* La meilleure voix française disponible. Les voix se chargent de façon
     asynchrone sur certains navigateurs, d'où la relecture à chaque usage
     plutôt qu'une capture au démarrage. */
  function voixFr() {
    var toutes = synth.getVoices() || [];
    return toutes.find(function (v) { return /^fr(-|_|$)/i.test(v.lang); })
        || toutes.find(function (v) { return /fran/i.test(v.name); })
        || null;
  }

  function taire() {
    try { synth.cancel(); } catch (e) { /* rien à faire */ }
  }

  /* Le silence entre deux segments. `speechSynthesis` n'a pas de pause : on
     la fabrique avec une phrase vide, ce que tous les moteurs acceptent et
     tiennent à peu près. Un murmure appelle un blanc, un cri n'en veut pas —
     c'est le champ `pause` du plan de lecture qui le dit. */
  function silence(facteur) {
    var u = new SpeechSynthesisUtterance(' ');
    u.volume = 0;
    u.rate = Math.max(0.5, Math.min(2, 1 / Math.max(0.4, facteur)));
    return u;
  }

  function dire(segments) {
    taire();
    if (!segments || !segments.length) return;
    var voix = voixFr();
    segments.forEach(function (s, i) {
      if (!s.texte) return;
      var u = new SpeechSynthesisUtterance(s.texte);
      if (voix) { u.voice = voix; u.lang = voix.lang; } else { u.lang = 'fr-FR'; }
      /* Les bornes sont celles de l'API : au-delà, la voix casse. */
      u.pitch = Math.max(0.1, Math.min(2, s.pitch || 1));
      u.rate = Math.max(0.5, Math.min(2, s.vitesse || 1));
      /* Le TON. Une réplique criée et une réplique murmurée se lisaient
         exactement pareil : le volume est ce qui les sépare le plus
         nettement, bien avant la hauteur. Voir app/engine/voix.py. */
      u.volume = Math.max(0, Math.min(1, s.volume === undefined ? 1 : s.volume));
      synth.speak(u);
      if (s.pause && s.pause > 1.05 && i < segments.length - 1) {
        synth.speak(silence(s.pause));
      }
    });
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
    bouton.setAttribute('aria-pressed', actif ? 'true' : 'false');
    bouton.title = actif ? 'Couper la lecture à voix haute'
                         : 'Lire les scènes à voix haute';
  }

  bouton.addEventListener('click', function () {
    actif = !actif;
    try { localStorage.setItem(CLE, actif ? '1' : '0'); } catch (e) { /* tant pis */ }
    accorder();
    if (!actif) { taire(); return; }
    /* On lit tout de suite la scène en cours : l'effet du bouton doit
       s'entendre, sinon on ne sait pas s'il a marché. */
    var dernier = document.querySelector('#recit .tour[data-id]');
    if (dernier) lireTour(dernier.dataset.campagne, dernier.dataset.id);
  });
  accorder();

  /* Un tour vient d'arriver — par htmx ou par le flux — on le lit. */
  function surNouveauTour() {
    var tours = document.querySelectorAll('#recit .tour[data-id]');
    var dernier = tours[tours.length - 1];
    if (dernier) lireTour(dernier.dataset.campagne, dernier.dataset.id);
  }
  document.body.addEventListener('htmx:afterSwap', function (e) {
    if (e.target && e.target.id === 'recit') surNouveauTour();
  });
  document.addEventListener('chroniques:tour', surNouveauTour);

  /* Chaque tour du récit peut être relu seul. */
  document.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.tour-lire') : null;
    if (!b) return;
    e.preventDefault();
    var tour = b.closest('.tour');
    if (tour) lireTour(tour.dataset.campagne, tour.dataset.id);
  });

  /* Quitter la page en pleine lecture laisserait la voix courir. */
  addEventListener('pagehide', taire);
})();
