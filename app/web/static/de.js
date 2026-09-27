/* ==========================================================================
   LE DÉ QUI ROULE

   Le serveur a déjà lancé ; ce fichier ne décide de rien. Il fait ce que fait
   un dé sur une table : il roule un instant sous les yeux de tout le monde
   avant de s'arrêter sur SA face — celle que le moteur a tirée. Un son sec
   l'accompagne, fabriqué sur place (aucun fichier audio à embarquer).

   Ne bouge que pour un tour en cours d'écriture. Dans l'historique, le dé est
   déjà posé : on ne relance pas le passé.
   ========================================================================== */
(function () {
  var sobre = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var contexte = null;

  function audio() {
    if (contexte) return contexte;
    try { contexte = new (window.AudioContext || window.webkitAudioContext)(); }
    catch (e) { contexte = null; }
    return contexte;
  }

  /* Un claquement : une bouffée de bruit filtrée, très courte. Trois d'affilée
     et l'oreille entend un dé qui rebondit. */
  function claquer(quand, force) {
    var ctx = audio();
    if (!ctx) return;
    var duree = 0.045;
    var tampon = ctx.createBuffer(1, Math.floor(ctx.sampleRate * duree), ctx.sampleRate);
    var donnees = tampon.getChannelData(0);
    for (var i = 0; i < donnees.length; i++) {
      donnees[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / donnees.length, 2.2);
    }
    var source = ctx.createBufferSource();
    source.buffer = tampon;
    var filtre = ctx.createBiquadFilter();
    filtre.type = 'bandpass';
    filtre.frequency.value = 1800 + Math.random() * 900;
    filtre.Q.value = 1.2;
    var gain = ctx.createGain();
    gain.gain.value = 0.35 * force;
    source.connect(filtre).connect(gain).connect(ctx.destination);
    source.start(ctx.currentTime + quand);
  }

  function rouler(bloc) {
    var de = bloc.querySelector('.de');
    if (!de || bloc.dataset.roule) return;
    bloc.dataset.roule = '1';
    var final = parseInt(bloc.dataset.de, 10);
    if (isNaN(final)) return;

    var couleur = bloc.className;            /* ok / mi / ko : gardée pour la fin */
    bloc.classList.remove('ok', 'mi', 'ko');
    bloc.classList.add('roule');

    if (sobre) {
      de.textContent = final;
      bloc.className = couleur;
      return;
    }

    var ctx = audio();
    if (ctx && ctx.state === 'suspended') ctx.resume();
    claquer(0.0, 1); claquer(0.16, 0.8); claquer(0.34, 0.6); claquer(0.62, 0.9);

    var pas = 0, total = 14;
    (function tic() {
      pas += 1;
      de.textContent = 1 + Math.floor(Math.random() * 20);
      if (pas < total) {
        /* Le dé ralentit : les premiers tours vont vite, les derniers hésitent. */
        setTimeout(tic, 35 + pas * pas * 3.2);
      } else {
        de.textContent = final;
        bloc.classList.remove('roule');
        bloc.className = couleur + ' pose';
        setTimeout(function () { bloc.classList.remove('pose'); }, 700);
      }
    })();
  }

  /* Le flux signale l'arrivée du jet ; on n'anime que dans un tour en cours. */
  document.addEventListener('chroniques:jet', function (e) {
    var racine = e.detail && e.detail.racine;
    if (!racine) return;
    racine.querySelectorAll('.bloc-jet[data-de]').forEach(function (b, i) {
      setTimeout(function () { rouler(b); }, i * 250);
    });
  });

  /* Le contexte audio ne s'ouvre que sur un geste : on le prépare au premier
     clic, pour que le premier dé fasse déjà du bruit. */
  document.addEventListener('click', function () {
    var ctx = audio();
    if (ctx && ctx.state === 'suspended') ctx.resume();
  }, { once: true });
})();
