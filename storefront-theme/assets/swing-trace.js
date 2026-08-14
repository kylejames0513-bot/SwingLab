/* CaddieInsight — the live read.
 *
 * A pose skeleton runs one swing cycle and the clubhead draws its own arc
 * behind it. The trace is not a decorative curve: it is the path the
 * clubhead actually takes through the poses below, sampled every frame, so
 * the shape on screen is produced by the motion rather than drawn to
 * suggest it.
 *
 * The timing is the engine's own benchmark. tempo_ratio targets 3:1 —
 * address-to-top takes three beats and top-to-impact takes one — so the
 * backswing occupies 0.42 of the cycle against the downswing's 0.14.
 * Changing one without the other breaks the only claim this animation
 * makes.
 *
 * DEGRADATION IS A REQUIREMENT. The markup ships a fully drawn SVG still
 * behind this canvas. The canvas is only revealed once init() succeeds, so
 * no-JS, canvas-less, reduced-motion and screenshot clients all get a
 * complete trace instead of an empty box. Under prefers-reduced-motion the
 * canvas paints one static frame at impact and never starts a loop.
 *
 * Shipped byte-identically to storefront-theme/assets/ and
 * swinglab/web/static/ — scripts/sync_shared_assets.py, and a parity test
 * that fails if they drift.
 */
(function (global) {
  'use strict';

  /* Face-on, right-handed. Normalised 0-1 in the box; x grows right, y grows
     down. The golfer faces the camera, so their trail side is the viewer's
     left and the backswing carries the hands up to the LEFT. */
  /* The clubhead does NOT travel in straight lines between the obvious four
     positions. Going address -> top -> impact with three keyframes draws the
     downswing as a chord straight through the golfer's chest, which is both
     wrong and instantly readable as wrong. A real downswing comes back down
     the same side the backswing went up — the repo's own hero copy already
     says it: the backswing draws for three beats and the strike retraces it
     in one. So the loop needs its intermediate positions, and the ones on
     the way down are the load-bearing ones. */
  var POSES = {
    address: {
      head: [0.500, 0.150], neck: [0.500, 0.245],
      shoulderT: [0.405, 0.265], shoulderL: [0.595, 0.265],
      elbowT: [0.385, 0.375], elbowL: [0.615, 0.375],
      hands: [0.500, 0.470],
      hipT: [0.435, 0.505], hipL: [0.565, 0.505],
      kneeT: [0.420, 0.670], kneeL: [0.580, 0.670],
      ankleT: [0.430, 0.855], ankleL: [0.570, 0.855],
      club: [0.500, 0.865]
    },
    takeaway: {
      head: [0.500, 0.150], neck: [0.500, 0.245],
      shoulderT: [0.395, 0.255], shoulderL: [0.588, 0.278],
      elbowT: [0.360, 0.360], elbowL: [0.575, 0.400],
      hands: [0.418, 0.448],
      hipT: [0.432, 0.505], hipL: [0.566, 0.505],
      kneeT: [0.420, 0.670], kneeL: [0.580, 0.670],
      ankleT: [0.430, 0.855], ankleL: [0.570, 0.855],
      club: [0.310, 0.718]
    },
    midback: {
      head: [0.499, 0.150], neck: [0.500, 0.245],
      shoulderT: [0.408, 0.243], shoulderL: [0.570, 0.288],
      elbowT: [0.352, 0.318], elbowL: [0.508, 0.372],
      hands: [0.372, 0.352],
      hipT: [0.438, 0.503], hipL: [0.562, 0.505],
      kneeT: [0.422, 0.670], kneeL: [0.578, 0.670],
      ankleT: [0.430, 0.855], ankleL: [0.570, 0.855],
      club: [0.238, 0.402]
    },
    top: {
      head: [0.498, 0.152], neck: [0.500, 0.245],
      shoulderT: [0.425, 0.235], shoulderL: [0.552, 0.292],
      elbowT: [0.360, 0.300], elbowL: [0.470, 0.340],
      hands: [0.372, 0.222],
      hipT: [0.442, 0.500], hipL: [0.558, 0.505],
      kneeT: [0.424, 0.670], kneeL: [0.576, 0.670],
      ankleT: [0.430, 0.855], ankleL: [0.570, 0.855],
      club: [0.548, 0.104]
    },
    /* The retrace. Without this the strike is a chord through the chest. */
    downswing: {
      head: [0.495, 0.155], neck: [0.497, 0.247],
      shoulderT: [0.412, 0.250], shoulderL: [0.572, 0.280],
      elbowT: [0.372, 0.330], elbowL: [0.530, 0.368],
      hands: [0.408, 0.372],
      hipT: [0.452, 0.500], hipL: [0.566, 0.498],
      kneeT: [0.428, 0.670], kneeL: [0.575, 0.668],
      ankleT: [0.430, 0.855], ankleL: [0.570, 0.855],
      club: [0.286, 0.448]
    },
    impact: {
      head: [0.492, 0.158], neck: [0.495, 0.248],
      shoulderT: [0.418, 0.268], shoulderL: [0.592, 0.256],
      elbowT: [0.408, 0.378], elbowL: [0.606, 0.366],
      hands: [0.522, 0.466],
      hipT: [0.452, 0.498], hipL: [0.588, 0.488],
      kneeT: [0.432, 0.670], kneeL: [0.582, 0.665],
      ankleT: [0.430, 0.855], ankleL: [0.570, 0.853],
      club: [0.500, 0.865]
    },
    release: {
      head: [0.487, 0.158], neck: [0.492, 0.248],
      shoulderT: [0.428, 0.278], shoulderL: [0.605, 0.244],
      elbowT: [0.478, 0.362], elbowL: [0.648, 0.318],
      hands: [0.618, 0.398],
      hipT: [0.462, 0.494], hipL: [0.598, 0.478],
      kneeT: [0.448, 0.668], kneeL: [0.586, 0.660],
      ankleT: [0.436, 0.855], ankleL: [0.568, 0.850],
      club: [0.748, 0.602]
    },
    extension: {
      head: [0.482, 0.155], neck: [0.489, 0.245],
      shoulderT: [0.440, 0.276], shoulderL: [0.612, 0.232],
      elbowT: [0.512, 0.330], elbowL: [0.664, 0.268],
      hands: [0.664, 0.300],
      hipT: [0.472, 0.488], hipL: [0.602, 0.468],
      kneeT: [0.470, 0.664], kneeL: [0.582, 0.652],
      ankleT: [0.462, 0.855], ankleL: [0.566, 0.845],
      club: [0.802, 0.282]
    },
    finish: {
      head: [0.476, 0.148], neck: [0.485, 0.240],
      shoulderT: [0.452, 0.272], shoulderL: [0.608, 0.222],
      elbowT: [0.528, 0.296], elbowL: [0.640, 0.230],
      hands: [0.628, 0.196],
      hipT: [0.482, 0.480], hipL: [0.598, 0.460],
      kneeT: [0.502, 0.658], kneeL: [0.578, 0.642],
      ankleT: [0.512, 0.855], ankleL: [0.562, 0.840],
      club: [0.452, 0.116]
    }
  };

  /* The cycle, in normalised time. The 3:1 ratio lives in the gap between
     TOP and IMPACT against ADDRESS and TOP: 0.42 of backswing against the
     downswing's 0.14. The finish holds so the eye can rest before the reset.
     The intermediate stages divide those two spans; they do not extend them,
     so the ratio the animation claims stays the ratio it shows. */
  var STAGES = [
    { at: 0.00, pose: 'address',   phase: 'address' },
    { at: 0.13, pose: 'takeaway',  phase: 'takeaway' },
    { at: 0.27, pose: 'midback',   phase: 'backswing' },
    { at: 0.42, pose: 'top',       phase: 'top' },
    { at: 0.50, pose: 'downswing', phase: 'downswing' },
    { at: 0.56, pose: 'impact',    phase: 'impact' },
    { at: 0.66, pose: 'release',   phase: 'release' },
    { at: 0.72, pose: 'extension', phase: 'extension' },
    { at: 0.80, pose: 'finish',    phase: 'finish' },
    { at: 1.00, pose: 'finish',    phase: 'finish' }
  ];

  var BONES = [
    ['head', 'neck'],
    ['neck', 'shoulderT'], ['neck', 'shoulderL'],
    ['shoulderT', 'elbowT'], ['elbowT', 'hands'],
    ['shoulderL', 'elbowL'], ['elbowL', 'hands'],
    ['shoulderT', 'hipT'], ['shoulderL', 'hipL'],
    ['hipT', 'hipL'], ['shoulderT', 'shoulderL'],
    ['hipT', 'kneeT'], ['kneeT', 'ankleT'],
    ['hipL', 'kneeL'], ['kneeL', 'ankleL']
  ];

  /* The landmarks that "resolve" — a crosshair blinks in as each is reached,
     the way the estimator locks a joint. */
  var LANDMARKS = [
    { at: 0.42, key: 'hands', label: 'TOP' },
    { at: 0.56, key: 'club',  label: 'IMPACT' },
    { at: 0.80, key: 'hands', label: 'FINISH' }
  ];

  var CYCLE_MS = 3400;
  var TRAIL = 130;

  function lerp(a, b, t) { return a + (b - a) * t; }

  /* easeInOutCubic between stages keeps the skeleton from ticking between
     keyframes; the downswing gets a sharper curve because a real one
     accelerates rather than easing out of the top. */
  function ease(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }
  function easeIn(t) { return t * t; }

  /* The clubhead is splined, not lerped. Interpolating it linearly between
     keyframes draws the arc as a polygon — the corners are visible at every
     stage boundary, and an arc with corners in it is the one thing this
     graphic cannot have. Catmull-Rom through the same keyframe positions
     passes through every one of them exactly while curving between, so the
     shape stays honest to the poses and stops looking folded. */
  var CLUB_KEYS = (function () {
    var out = [];
    for (var i = 0; i < STAGES.length; i++) {
      out.push({ at: STAGES[i].at, p: POSES[STAGES[i].pose].club });
    }
    return out;
  }());

  function catmull(p0, p1, p2, p3, u) {
    var u2 = u * u;
    var u3 = u2 * u;
    return 0.5 * (
      (2 * p1) +
      (-p0 + p2) * u +
      (2 * p0 - 5 * p1 + 4 * p2 - p3) * u2 +
      (-p0 + 3 * p1 - 3 * p2 + p3) * u3
    );
  }

  function clubAt(t, eased, index) {
    var k = CLUB_KEYS;
    var p0 = k[Math.max(0, index - 1)].p;
    var p1 = k[index].p;
    var p2 = k[Math.min(k.length - 1, index + 1)].p;
    var p3 = k[Math.min(k.length - 1, index + 2)].p;
    return [
      catmull(p0[0], p1[0], p2[0], p3[0], eased),
      catmull(p0[1], p1[1], p2[1], p3[1], eased)
    ];
  }

  function poseAt(t) {
    var i = 0;
    for (var s = 0; s < STAGES.length - 1; s++) {
      if (t >= STAGES[s].at) { i = s; }
    }
    var a = STAGES[i];
    var b = STAGES[Math.min(i + 1, STAGES.length - 1)];
    var span = b.at - a.at;
    var local = span > 0 ? (t - a.at) / span : 0;
    if (local < 0) { local = 0; } else if (local > 1) { local = 1; }
    /* the downswing is the one span that accelerates into its endpoint —
       a real one does not ease out of the top, it falls */
    var accelerating = a.phase === 'top' || a.phase === 'downswing';
    var eased = accelerating ? easeIn(local) : ease(local);
    var from = POSES[a.pose];
    var to = POSES[b.pose];
    var out = {};
    for (var key in from) {
      if (!Object.prototype.hasOwnProperty.call(from, key)) { continue; }
      out[key] = [
        lerp(from[key][0], to[key][0], eased),
        lerp(from[key][1], to[key][1], eased)
      ];
    }
    /* the body lerps between keyframes; the clubhead curves through them */
    out.club = clubAt(t, eased, i);
    return { joints: out, phase: a.phase };
  }

  /* The bounding box of everything this animation ever draws, measured by
     walking the whole cycle once rather than hand-written. The figure used to
     be laid out against the raw 0-1 pose space, but nothing actually reaches
     those edges — the body spans about x 0.36-0.66 and the clubhead swings
     out to 0.24-0.80 — so a third of the box was permanent empty margin and
     the skeleton read as a tiny doll in a large grid.
     Measuring means a pose edit re-fits automatically instead of silently
     re-introducing the same problem. */
  /* How much of the swing ARC is allowed to run past the edge, 0..1.
     0 keeps every millimetre of the arc inside the box, which sounds right
     and is why the figure ended up tiny: the clubhead sweeps a loop roughly
     three times the golfer's own height, so fitting the loop shrinks the
     golfer to a doll. 1 fits the BODY and lets the arc bleed out entirely.
     The container clips, and a trace running off the edge reads as motion
     rather than as a mistake — but the impact end of the arc must stay
     visible, because that is where the ball is. */
  var ARC_BLEED = 0.62;

  var CONTENT = (function () {
    var all = { minX: 1, maxX: 0, minY: 1, maxY: 0 };
    var body = { minX: 1, maxX: 0, minY: 1, maxY: 0 };
    for (var t = 0; t <= 1; t += 0.004) {
      var joints = poseAt(t).joints;
      for (var key in joints) {
        if (!Object.prototype.hasOwnProperty.call(joints, key)) { continue; }
        var p = joints[key];
        if (p[0] < all.minX) { all.minX = p[0]; }
        if (p[0] > all.maxX) { all.maxX = p[0]; }
        if (p[1] < all.minY) { all.minY = p[1]; }
        if (p[1] > all.maxY) { all.maxY = p[1]; }
        if (key === 'club') { continue; }
        if (p[0] < body.minX) { body.minX = p[0]; }
        if (p[0] > body.maxX) { body.maxX = p[0]; }
        if (p[1] < body.minY) { body.minY = p[1]; }
        if (p[1] > body.maxY) { body.maxY = p[1]; }
      }
    }
    function mix(a, b) { return a + (b - a) * ARC_BLEED; }
    var box = {
      minX: mix(all.minX, body.minX),
      maxX: mix(all.maxX, body.maxX),
      minY: mix(all.minY, body.minY),
      /* the bottom is NOT mixed: address, impact and the pulse all live on
         that line, and cropping it would cut the ball off the graphic */
      maxY: all.maxY
    };
    /* the skull sits outside its joint, and the impact pulse expands past
       the clubhead — without a bleed both clip against a filled box */
    var pad = 0.05;
    box.minX -= pad; box.maxX += pad;
    box.minY -= pad; box.maxY += pad;
    return box;
  }());

  function SwingTrace(canvas, options) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.opts = options || {};
    this.trail = [];
    this.raf = null;
    this.start = 0;
    this.phase = null;
    this.running = false;
    this.reduced = false;
  }

  SwingTrace.prototype.resize = function () {
    var rect = this.canvas.getBoundingClientRect();
    var dpr = Math.min(global.devicePixelRatio || 1, 2);
    if (!rect.width || !rect.height) { return false; }
    this.canvas.width = Math.round(rect.width * dpr);
    this.canvas.height = Math.round(rect.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = rect.width;
    this.h = rect.height;
    return true;
  };

  /* Fit CONTENT to the canvas and centre it, so the figure is as large as the
     box allows at every viewport instead of floating inside a fixed margin.

     ONE scale for both axes, deliberately: f.w and f.h are pixels-per-unit,
     and letting them differ (as the old frame did, via a 1.15 ratio against
     the height) stretches the skeleton — a golfer who gets wider on a wide
     hero and narrower on a phone. Uniform scale keeps the proportions the
     poses were drawn with, and the leftover space becomes even margin. */
  SwingTrace.prototype.frame = function () {
    var pad = Math.min(this.w, this.h) * 0.04;
    var availableW = Math.max(1, this.w - pad * 2);
    var availableH = Math.max(1, this.h - pad * 2);
    var contentW = CONTENT.maxX - CONTENT.minX;
    var contentH = CONTENT.maxY - CONTENT.minY;
    var scale = Math.min(availableW / contentW, availableH / contentH);
    return {
      x: pad + (availableW - contentW * scale) / 2 - CONTENT.minX * scale,
      y: pad + (availableH - contentH * scale) / 2 - CONTENT.minY * scale,
      w: scale,
      h: scale
    };
  };

  SwingTrace.prototype.pt = function (j, f) {
    return [f.x + j[0] * f.w, f.y + j[1] * f.h];
  };

  SwingTrace.prototype.draw = function (t) {
    var ctx = this.ctx;
    var f = this.frame();
    var state = poseAt(t);
    var j = state.joints;
    ctx.clearRect(0, 0, this.w, this.h);

    /* This canvas only ever runs on the FIELD — the hero's reversed ground —
       so all three are field-side values.

       `signal` is a misleading name kept for its call sites: what it draws is
       the landmark crosshairs and the impact pulse, which are events in a LIVE
       readout, not values the engine measured. It used to be amber, which
       spent the measured-value colour on an animation. Both it and `trace` are
       now steel, one lit and one dim, so they stay distinguishable from each
       other without either pretending to be a measurement. */
    var ink = this.opts.ink || 'rgba(242,242,243,';
    var trace = this.opts.trace || 'rgba(148,188,227,';
    var signal = this.opts.signal || 'rgba(89,128,166,';

    /* the clubhead trail — the arc, sampled from the motion itself */
    var head = this.pt(j.club, f);
    this.trail.push([head[0], head[1], t]);
    if (this.trail.length > TRAIL) { this.trail.shift(); }

    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (var i = 1; i < this.trail.length; i++) {
      var age = i / this.trail.length;
      ctx.beginPath();
      ctx.moveTo(this.trail[i - 1][0], this.trail[i - 1][1]);
      ctx.lineTo(this.trail[i][0], this.trail[i][1]);
      /* The whole arc stays trace-cyan. An earlier pass painted everything
         after impact amber, which looked striking and broke the one rule the
         palette has: amber marks a value the engine measured, and a path is
         not a value. The strike is distinguished by weight and brightness
         instead, leaving amber to the landmarks and the impact pulse — which
         ARE measurements. */
      var struck = this.trail[i][2] > 0.56;
      ctx.strokeStyle = trace + (age * (struck ? 0.95 : 0.66)).toFixed(3) + ')';
      ctx.lineWidth = (struck ? 1.5 : 1) + age * 2.1;
      ctx.stroke();
    }

    /* the skeleton. The head-to-neck bone stops at the skull's edge instead
       of running to its centre, so the circle sits ON the spine rather than
       floating above it with a line through it. */
    var hd = this.pt(j.head, f);
    var r = Math.max(6, f.w * 0.030);
    ctx.strokeStyle = ink + '0.5)';
    ctx.lineWidth = 1.6;
    for (var b = 0; b < BONES.length; b++) {
      var p = this.pt(j[BONES[b][0]], f);
      var q = this.pt(j[BONES[b][1]], f);
      if (BONES[b][0] === 'head') {
        var dx = q[0] - p[0];
        var dy = q[1] - p[1];
        var len = Math.sqrt(dx * dx + dy * dy) || 1;
        p = [p[0] + (dx / len) * r, p[1] + (dy / len) * r];
      }
      ctx.beginPath();
      ctx.moveTo(p[0], p[1]);
      ctx.lineTo(q[0], q[1]);
      ctx.stroke();
    }

    /* the shaft */
    var hands = this.pt(j.hands, f);
    ctx.strokeStyle = ink + '0.72)';
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(hands[0], hands[1]);
    ctx.lineTo(head[0], head[1]);
    ctx.stroke();

    /* the skull */
    ctx.strokeStyle = ink + '0.6)';
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.arc(hd[0], hd[1], r, 0, Math.PI * 2);
    ctx.stroke();

    /* joints */
    ctx.fillStyle = ink + '0.85)';
    for (var key in j) {
      if (key === 'club' || key === 'head') { continue; }
      var c = this.pt(j[key], f);
      ctx.beginPath();
      ctx.arc(c[0], c[1], 2, 0, Math.PI * 2);
      ctx.fill();
    }

    /* landmark crosshairs blink in as each is reached and hold after */
    for (var l = 0; l < LANDMARKS.length; l++) {
      var lm = LANDMARKS[l];
      if (t < lm.at) { continue; }
      var alpha = Math.min(1, (t - lm.at) * 14);
      /* the crosshair marks WHERE the landmark resolved, so it is pinned to
         the pose at that instant rather than dragged along by the figure */
      var pinned = this.pt(poseAt(lm.at).joints[lm.key], f);
      ctx.strokeStyle = signal + (alpha * 0.9).toFixed(3) + ')';
      ctx.lineWidth = 1;
      var s = 7;
      ctx.beginPath();
      ctx.moveTo(pinned[0] - s, pinned[1]); ctx.lineTo(pinned[0] + s, pinned[1]);
      ctx.moveTo(pinned[0], pinned[1] - s); ctx.lineTo(pinned[0], pinned[1] + s);
      ctx.stroke();
    }

    /* impact pulse */
    if (t > 0.56 && t < 0.70) {
      var g = (t - 0.56) / 0.14;
      var pin = this.pt(poseAt(0.56).joints.club, f);
      ctx.strokeStyle = signal + ((1 - g) * 0.8).toFixed(3) + ')';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(pin[0], pin[1], 6 + g * 34, 0, Math.PI * 2);
      ctx.stroke();
    }

    if (state.phase !== this.phase) {
      this.phase = state.phase;
      if (typeof this.opts.onPhase === 'function') { this.opts.onPhase(state.phase); }
    }
  };

  SwingTrace.prototype.still = function () {
    /* one complete frame: the finish, with the whole arc already laid down */
    if (!this.resize()) { return; }
    this.trail = [];
    for (var t = 0; t <= 0.80; t += 0.003) { this.draw(t); }
    this.draw(0.80);
  };

  SwingTrace.prototype.tick = function (now) {
    if (!this.running) { return; }
    if (!this.start) { this.start = now; }
    var t = ((now - this.start) % CYCLE_MS) / CYCLE_MS;
    if (t < 0.01 && this.trail.length > TRAIL / 2) { this.trail = []; }
    this.draw(t);
    this.raf = global.requestAnimationFrame(this.tick.bind(this));
  };

  SwingTrace.prototype.play = function () {
    if (this.running || this.reduced) { return; }
    this.running = true;
    this.start = 0;
    this.raf = global.requestAnimationFrame(this.tick.bind(this));
  };

  SwingTrace.prototype.pause = function () {
    this.running = false;
    if (this.raf) { global.cancelAnimationFrame(this.raf); this.raf = null; }
  };

  SwingTrace.prototype.init = function () {
    if (!this.ctx || !this.resize()) { return false; }
    var self = this;
    var mq = global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)');
    this.reduced = !!(mq && mq.matches);

    if (this.reduced) {
      this.still();
    } else {
      /* only animate while on screen — a hero loop running behind three
         screens of scroll is pure battery */
      if ('IntersectionObserver' in global) {
        /* Paint one frame NOW, before the observer has said anything. The
           caller hides the SVG still the moment init() succeeds, and a canvas
           that has only been sized draws nothing — so a trace initialised
           while below the fold left a blank box where the still used to be,
           until the visitor happened to scroll it into view. One static frame
           costs nothing and means the canvas is never emptier than the thing
           it replaced. */
        this.still();
        new global.IntersectionObserver(function (entries) {
          for (var i = 0; i < entries.length; i++) {
            if (entries[i].isIntersecting) { self.play(); } else { self.pause(); }
          }
        }, { threshold: 0.05 }).observe(this.canvas);
      } else {
        this.play();
      }
    }

    var resizeTimer = null;
    global.addEventListener('resize', function () {
      global.clearTimeout(resizeTimer);
      resizeTimer = global.setTimeout(function () {
        if (!self.resize()) { return; }
        if (self.reduced) { self.still(); } else { self.trail = []; }
      }, 160);
    });

    if (mq && typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', function (e) {
        self.reduced = e.matches;
        if (self.reduced) { self.pause(); self.still(); } else { self.play(); }
      });
    }
    return true;
  };

  global.SwingTrace = SwingTrace;

  global.initSwingTrace = function (root) {
    var nodes = (root || global.document).querySelectorAll('[data-swing-trace]:not([data-swing-trace-ready])');
    for (var i = 0; i < nodes.length; i++) {
      (function (canvas) {
        var host = canvas.closest('[data-swing-readout]') || global.document;
        var label = host.querySelector('[data-swing-phase]');
        var inst = new SwingTrace(canvas, {
          onPhase: function (phase) {
            if (label) { label.textContent = phase.toUpperCase(); }
          }
        });
        /* INIT RACES LAYOUT, AND USED TO LOSE SILENTLY.
           init() calls resize(), which returns false for a zero-sized box, and
           the box IS zero at DOMContentLoaded — the hero's trace is sized by an
           aspect-ratio on a grid child, which has no height until layout runs.
           So init() returned false, nothing retried, and every visitor got the
           static SVG still forever. The animation was never broken; it was
           never started.

           Hence: try, and if the box is not there yet, watch for it. A
           ResizeObserver fires the moment the element gains a size; 'load' is
           the fallback for browsers without one. Both are torn down on the
           first success so this can never run twice for one canvas. */
        var start = function () {
          if (canvas.hasAttribute('data-swing-trace-ready')) { return true; }
          if (!inst.init()) { return false; }
          canvas.setAttribute('data-swing-trace-ready', '');
          /* only now is the SVG still redundant */
          var fallback = host.querySelector('[data-swing-still]');
          if (fallback) { fallback.setAttribute('hidden', ''); }
          return true;
        };

        if (!start()) {
          var ro = null;
          var onLoad = function () { if (start() && ro) { ro.disconnect(); } };
          if ('ResizeObserver' in global) {
            ro = new global.ResizeObserver(function () {
              if (start()) { ro.disconnect(); }
            });
            ro.observe(canvas);
          }
          global.addEventListener('load', onLoad, { once: true });
        }
      }(nodes[i]));
    }
  };

  if (global.document) {
    if (global.document.readyState === 'loading') {
      global.document.addEventListener('DOMContentLoaded', function () { global.initSwingTrace(); });
    } else {
      global.initSwingTrace();
    }
    global.document.addEventListener('shopify:section:load', function (e) {
      global.initSwingTrace(e.target);
    });
  }
}(typeof window !== 'undefined' ? window : this));
