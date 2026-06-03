/**
 * Album Slideshow Card
 *
 * Client-side cross-fade (and friends) for `album_slideshow` cameras.
 * Server CPU cost per slide change: one JPEG encode. The transition
 * itself runs entirely in the browser via CSS/GPU compositing, so it
 * stays buttery smooth even on a Raspberry Pi class HA host with
 * multiple albums on screen.
 *
 * Usage:
 *   type: custom:album-slideshow-card
 *   entity: camera.album_slideshow_living_room
 *   transition: random      # random | none | fade | slide-left
 *                           #   | slide-right | slide-up | slide-down
 *                           #   | wipe-left | wipe-right | zoom
 *   duration: 600           # ms
 *   easing: ease-in-out     # any CSS easing
 *   aspect_ratio: 16/9      # CSS aspect-ratio value, e.g. 16/9, 4/3, auto
 *   fit: auto               # auto | cover | contain
 *                           # ``auto`` inherits from the camera's
 *                           # ``fill_mode`` attribute (cover / contain
 *                           # / blur). ``blur`` adds a blurred backdrop
 *                           # behind a contained image.
 *   background: ''          # CSS color shown behind contained images.
 *                           # Empty inherits theme card background.
 *   tap_action: none        # none | more-info
 */

const VERSION = "0.9.0";

const ANIMATED_TRANSITIONS = [
  "fade",
  "slide-left",
  "slide-right",
  "slide-up",
  "slide-down",
  "wipe-left",
  "wipe-right",
  "zoom",
];

// ``none`` short-circuits all animation: the new image replaces the old
// instantly. Useful on very-low-power displays or when the user wants
// the slideshow to feel like a static gallery cycling through frames.
const TRANSITIONS = new Set(["random", "none", ...ANIMATED_TRANSITIONS]);

const FIT_MODES = new Set(["auto", "cover", "contain"]);

/** Identify album_slideshow camera entities by their distinctive
 * ``frame_id`` attribute, which no other camera integration emits. */
function isAlbumSlideshowCamera(state) {
  return (
    state &&
    typeof state.entity_id === "string" &&
    state.entity_id.startsWith("camera.") &&
    state.attributes &&
    "frame_id" in state.attributes
  );
}

// The card class is built lazily by a factory so the base class can be
// resolved from the *live* ``window.HTMLElement`` at registration time.
// See ``defineAlbumSlideshowCards`` for why this matters with the
// scoped-custom-element-registry polyfill.
function createAlbumSlideshowCardClass(Base) {
  return class AlbumSlideshowCard extends Base {
  static getStubConfig(hass) {
    let entity = "";
    if (hass && hass.states) {
      for (const id of Object.keys(hass.states)) {
        if (isAlbumSlideshowCamera(hass.states[id])) {
          entity = id;
          break;
        }
      }
    }
    return {
      type: "custom:album-slideshow-card",
      entity,
      transition: "random",
      duration: 600,
    };
  }

  static getConfigElement() {
    return document.createElement("album-slideshow-card-editor");
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._showing = "a"; // which layer is on top
    this._lastFrameId = null;
    this._lastEntityPicture = null;
    this._lastRandomTransition = null;
    this._currentTransition = null; // class applied to .layer right now
    this._rendered = false;
    // Suspend visual swaps for a while after the user taps, so the
    // photo they're looking at in the more-info dialog stays put on
    // the card behind it. A new state update during the hold window
    // schedules a deferred swap that runs once the hold expires.
    this._holdSwapsUntil = 0;
    this._holdSwapTimer = null;
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("album-slideshow-card: 'entity' is required");
    }
    if (!config.entity.startsWith("camera.")) {
      throw new Error("album-slideshow-card: 'entity' must be a camera entity");
    }
    const transition = (config.transition || "random").toLowerCase();
    if (!TRANSITIONS.has(transition)) {
      throw new Error(
        `album-slideshow-card: unknown transition '${transition}'`,
      );
    }
    const fit = (config.fit || "auto").toLowerCase();
    if (!FIT_MODES.has(fit)) {
      throw new Error(`album-slideshow-card: unknown fit '${fit}'`);
    }
    this._config = {
      ...config,
      transition,
      duration: Number(config.duration ?? 600),
      easing: config.easing || "ease-in-out",
      aspect_ratio: config.aspect_ratio || "16/9",
      fit,
      // Empty/missing background means inherit theme.
      background: typeof config.background === "string" ? config.background : "",
      tap_action: config.tap_action === "more-info" ? "more-info" : "none",
      // Number of seconds the card freezes its visible slide after a
      // tap, so the more-info dialog can settle without the slideshow
      // marching forward beneath it. Set to 0 to disable.
      tap_pause_seconds:
        config.tap_pause_seconds === 0
          ? 0
          : Number(config.tap_pause_seconds ?? 8),
    };
    if (this._rendered) {
      // Config edited live; rebuild styles + reset state.
      this._renderShell();
      this._lastFrameId = null;
      this._lastEntityPicture = null;
      this._currentTransition = null;
      this._maybeSwap();
    }
  }

  getCardSize() {
    return 4;
  }

  connectedCallback() {
    if (!this._rendered) {
      this._renderShell();
      this._rendered = true;
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._rendered) return;
    this._maybeSwap();
  }

  _resolvedFit(attrs) {
    // ``auto`` inherits from the camera's fill_mode attribute. The camera
    // exposes cover / contain / blur. ``blur`` is rendered as ``contain``
    // plus a blurred backdrop layer.
    const cardFit = this._config.fit;
    if (cardFit !== "auto") {
      return { fit: cardFit, blurBackdrop: false };
    }
    const cameraFill = (attrs && attrs.fill_mode) || "cover";
    if (cameraFill === "contain") return { fit: "contain", blurBackdrop: false };
    if (cameraFill === "blur") return { fit: "contain", blurBackdrop: true };
    return { fit: "cover", blurBackdrop: false };
  }

  _renderShell() {
    const c = this._config;
    const aspect = c.aspect_ratio === "auto" ? "auto" : c.aspect_ratio;
    // When the user did not set ``background`` we fall through to the
    // theme's --ha-card-background, so the card naturally inherits the
    // dashboard theme. When set, the user's color wins.
    const stageBg = c.background
      ? c.background
      : "var(--ha-card-background, var(--card-background-color, transparent))";
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card {
          /* Inherit border, radius, shadow, background from theme. */
          overflow: hidden;
          ${aspect === "auto" ? "" : `aspect-ratio: ${aspect};`}
          ${c.background ? `background: ${c.background};` : ""}
          position: relative;
          padding: 0;
        }
        .stage {
          position: absolute;
          inset: 0;
          width: 100%;
          height: 100%;
          background: ${stageBg};
          border-radius: inherit;
          overflow: hidden;
        }
        .blur-bg {
          position: absolute;
          inset: -5%;
          width: 110%;
          height: 110%;
          object-fit: cover;
          filter: blur(24px) brightness(0.75);
          opacity: 0;
          transition: opacity ${c.duration}ms ${c.easing};
          pointer-events: none;
          user-select: none;
        }
        .blur-bg.show { opacity: 1; }
        .layer {
          position: absolute;
          inset: 0;
          width: 100%;
          height: 100%;
          object-fit: cover;
          opacity: 0;
          will-change: opacity, transform, clip-path;
          transition:
            opacity ${c.duration}ms ${c.easing},
            transform ${c.duration}ms ${c.easing},
            clip-path ${c.duration}ms ${c.easing};
          backface-visibility: hidden;
          transform: translateZ(0);
          pointer-events: none;
          user-select: none;
        }
        .layer.fit-cover { object-fit: cover; }
        .layer.fit-contain { object-fit: contain; }
        .layer.show { opacity: 1; }
        .placeholder {
          position: absolute;
          inset: 0;
          display: grid;
          place-items: center;
          color: var(--secondary-text-color, rgba(255, 255, 255, 0.5));
          font-size: 0.85rem;
          font-family: var(--paper-font-body1_-_font-family, sans-serif);
        }
        ${this._transitionStyles()}
      </style>
      <ha-card part="card">
        <div class="stage" id="stage">
          <img class="blur-bg" id="blur-a" alt="" />
          <img class="blur-bg" id="blur-b" alt="" />
          <img class="layer" id="a" alt="" />
          <img class="layer" id="b" alt="" />
          <div class="placeholder" id="placeholder">Waiting for first frame...</div>
        </div>
      </ha-card>
    `;
    const card = this.shadowRoot.querySelector("ha-card");
    if (this._config.tap_action === "more-info") {
      card.addEventListener("click", () => this._fireMoreInfo());
      card.style.cursor = "pointer";
    }
  }

  _transitionStyles() {
    // Every animated variant is emitted under a ``t-<name>`` modifier
    // class so a single shell can host any of them. ``_performSwap``
    // picks one (or a random one) and tags both layers per swap.
    return `
      .layer.t-none { transition: none !important; }
      .layer.t-none.enter { opacity: 0; }
      .layer.t-none.show { opacity: 1; }
      .layer.t-none.exit { opacity: 0; }

      .layer.t-fade.enter { opacity: 0; }
      .layer.t-fade.show { opacity: 1; }
      .layer.t-fade.exit { opacity: 0; }

      .layer.t-slide-left.enter { opacity: 1; transform: translateX(100%); }
      .layer.t-slide-left.show { opacity: 1; transform: translateX(0); }
      .layer.t-slide-left.exit { opacity: 1; transform: translateX(-100%); }

      .layer.t-slide-right.enter { opacity: 1; transform: translateX(-100%); }
      .layer.t-slide-right.show { opacity: 1; transform: translateX(0); }
      .layer.t-slide-right.exit { opacity: 1; transform: translateX(100%); }

      .layer.t-slide-up.enter { opacity: 1; transform: translateY(100%); }
      .layer.t-slide-up.show { opacity: 1; transform: translateY(0); }
      .layer.t-slide-up.exit { opacity: 1; transform: translateY(-100%); }

      .layer.t-slide-down.enter { opacity: 1; transform: translateY(-100%); }
      .layer.t-slide-down.show { opacity: 1; transform: translateY(0); }
      .layer.t-slide-down.exit { opacity: 1; transform: translateY(100%); }

      .layer.t-wipe-left.enter { opacity: 1; clip-path: inset(0 0 0 100%); }
      .layer.t-wipe-left.show { opacity: 1; clip-path: inset(0 0 0 0); }
      .layer.t-wipe-left.exit { opacity: 1; clip-path: inset(0 0 0 0); }

      .layer.t-wipe-right.enter { opacity: 1; clip-path: inset(0 100% 0 0); }
      .layer.t-wipe-right.show { opacity: 1; clip-path: inset(0 0 0 0); }
      .layer.t-wipe-right.exit { opacity: 1; clip-path: inset(0 0 0 0); }

      .layer.t-zoom.enter { opacity: 0; transform: scale(1.05); }
      .layer.t-zoom.show { opacity: 1; transform: scale(1); }
      .layer.t-zoom.exit { opacity: 0; transform: scale(1); }
    `;
  }

  _pickTransition() {
    const cfg = this._config.transition;
    if (cfg !== "random") return cfg;
    // Try not to repeat the previous random pick when more than one option
    // is available; users perceive the "random" effect more strongly when
    // consecutive slides differ.
    const pool = ANIMATED_TRANSITIONS.filter(
      (t) => t !== this._lastRandomTransition,
    );
    const choices = pool.length > 0 ? pool : ANIMATED_TRANSITIONS;
    const pick = choices[Math.floor(Math.random() * choices.length)];
    this._lastRandomTransition = pick;
    return pick;
  }

  _maybeSwap() {
    const hass = this._hass;
    if (!hass) return;
    const state = hass.states[this._config.entity];
    if (!state) {
      this._setPlaceholder(`Entity not found: ${this._config.entity}`);
      return;
    }
    // Hold visual swaps for the configured grace period after a tap.
    // The state cursor (`_lastFrameId`/`_lastEntityPicture`) is left
    // untouched during the hold; once the hold expires we re-enter
    // ``_maybeSwap`` and pick up whatever frame is currently latest.
    const now = Date.now();
    if (now < this._holdSwapsUntil) {
      if (!this._holdSwapTimer) {
        const wait = this._holdSwapsUntil - now + 50;
        this._holdSwapTimer = setTimeout(() => {
          this._holdSwapTimer = null;
          this._maybeSwap();
        }, wait);
      }
      return;
    }
    const attrs = state.attributes || {};
    // ``frame_id`` increments on every slide commit; that's our primary
    // "new frame ready" signal. The integration also embeds frame_id in
    // ``entity_picture`` so that HA core surfaces (more-info, picture
    // tiles) cache-bust naturally. We piggyback frame_id in our query
    // string here for older integration versions that don't yet do that.
    const frameId = attrs.frame_id ?? null;
    const entityPicture = state.attributes.entity_picture;
    if (
      frameId === this._lastFrameId &&
      entityPicture === this._lastEntityPicture
    ) {
      return;
    }
    this._lastFrameId = frameId;
    this._lastEntityPicture = entityPicture;
    if (!entityPicture) {
      this._setPlaceholder("Camera not ready");
      return;
    }
    let url = entityPicture;
    if (frameId !== null && !/[?&]frame=/.test(url)) {
      const sep = url.includes("?") ? "&" : "?";
      url = `${url}${sep}_frame=${frameId}`;
    }
    const { fit, blurBackdrop } = this._resolvedFit(attrs);
    this._loadAndSwap(url, fit, blurBackdrop);
  }

  _loadAndSwap(url, fit, blurBackdrop) {
    // Pre-decode the new image so the swap is instant.
    const next = new Image();
    next.decoding = "async";
    next.onload = () => this._performSwap(url, fit, blurBackdrop);
    next.onerror = () => this._setPlaceholder("Failed to load slide");
    next.src = url;
  }

  _performSwap(url, fit, blurBackdrop) {
    const root = this.shadowRoot;
    const placeholder = root.getElementById("placeholder");
    if (placeholder) placeholder.remove();

    const a = root.getElementById("a");
    const b = root.getElementById("b");
    const blurA = root.getElementById("blur-a");
    const blurB = root.getElementById("blur-b");
    const showing = this._showing === "a" ? a : b;
    const hidden = this._showing === "a" ? b : a;
    const showingBlur = this._showing === "a" ? blurA : blurB;
    const hiddenBlur = this._showing === "a" ? blurB : blurA;

    // Apply fit class to both layers (cheap; idempotent).
    for (const el of [a, b]) {
      el.classList.remove("fit-cover", "fit-contain");
      el.classList.add(fit === "contain" ? "fit-contain" : "fit-cover");
    }

    const transition = this._pickTransition();
    const transitionClass = `t-${transition}`;

    // First frame: no animation, just place the image and reveal.
    if (!showing.src) {
      showing.src = url;
      hidden.src = url;
      showing.classList.add(transitionClass, "show");
      hidden.classList.add(transitionClass);
      if (blurBackdrop) {
        showingBlur.src = url;
        hiddenBlur.src = url;
        showingBlur.classList.add("show");
      }
      this._currentTransition = transitionClass;
      return;
    }

    // Drop the previous transition class from both layers before applying
    // the new one. Keeps the class list bounded under "random" mode.
    if (this._currentTransition && this._currentTransition !== transitionClass) {
      a.classList.remove(this._currentTransition);
      b.classList.remove(this._currentTransition);
    }
    this._currentTransition = transitionClass;

    hidden.src = url;
    hidden.classList.remove("show", "exit", "enter");
    hidden.classList.add(transitionClass, "enter");
    // Force a layout flush so the browser sees the "enter" pose before
    // we transition to "show".
    // eslint-disable-next-line no-unused-expressions
    hidden.offsetWidth;
    hidden.classList.remove("enter");
    hidden.classList.add("show");

    showing.classList.remove("show", "enter");
    showing.classList.add(transitionClass, "exit");

    // Blurred backdrop layer (only used when fill_mode resolves to blur).
    if (blurBackdrop) {
      hiddenBlur.src = url;
      hiddenBlur.classList.add("show");
      showingBlur.classList.remove("show");
    } else {
      showingBlur.classList.remove("show");
      hiddenBlur.classList.remove("show");
    }

    this._showing = this._showing === "a" ? "b" : "a";

    // Cleanup the .exit class after the animation so it doesn't fight the
    // next swap. Slightly longer than the duration to be safe.
    const dur = this._config.duration + 50;
    setTimeout(() => {
      showing.classList.remove("exit");
    }, dur);
  }

  _setPlaceholder(text) {
    const root = this.shadowRoot;
    let placeholder = root.getElementById("placeholder");
    if (!placeholder) {
      placeholder = document.createElement("div");
      placeholder.id = "placeholder";
      placeholder.className = "placeholder";
      root.getElementById("stage").appendChild(placeholder);
    }
    placeholder.textContent = text;
  }

  _fireMoreInfo() {
    // Freeze the visible slide on the card while the user is in the
    // more-info dialog. Without this, the slideshow keeps marching
    // forward behind the modal and the user perceives the card and
    // dialog as showing different photos.
    const pauseSec = this._config.tap_pause_seconds;
    if (pauseSec > 0) {
      this._holdSwapsUntil = Date.now() + pauseSec * 1000;
    }
    const event = new Event("hass-more-info", {
      bubbles: true,
      composed: true,
    });
    event.detail = { entityId: this._config.entity };
    this.dispatchEvent(event);
  }
  };
}

/**
 * Visual editor.
 *
 * Mirrors the look-and-feel of ha-shopping-list-card: native HA form
 * controls (ha-entity-picker, ha-textfield, ha-select, ha-switch)
 * grouped inside ha-expansion-panel sections so the form scales without
 * becoming a wall.
 */
const TRANSITION_OPTIONS = [
  { value: "random", label: "Random (different per slide)" },
  { value: "none", label: "None (instant swap)" },
  { value: "fade", label: "Fade" },
  { value: "slide-left", label: "Slide left" },
  { value: "slide-right", label: "Slide right" },
  { value: "slide-up", label: "Slide up" },
  { value: "slide-down", label: "Slide down" },
  { value: "wipe-left", label: "Wipe left" },
  { value: "wipe-right", label: "Wipe right" },
  { value: "zoom", label: "Zoom" },
];

const FIT_OPTIONS = [
  { value: "auto", label: "Auto (inherit camera fill_mode)" },
  { value: "cover", label: "Cover" },
  { value: "contain", label: "Contain" },
];

const EASING_OPTIONS = [
  { value: "ease-in-out", label: "Ease in-out (smooth)" },
  { value: "ease", label: "Ease" },
  { value: "ease-in", label: "Ease in" },
  { value: "ease-out", label: "Ease out" },
  { value: "linear", label: "Linear" },
  { value: "cubic-bezier(0.4, 0, 0.2, 1)", label: "Material standard" },
  { value: "cubic-bezier(0.0, 0.0, 0.2, 1)", label: "Material decelerate" },
  { value: "cubic-bezier(0.4, 0.0, 1, 1)", label: "Material accelerate" },
];

const TAP_OPTIONS = [
  { value: "none", label: "None" },
  { value: "more-info", label: "Open more-info" },
];

const DEFAULTS = {
  transition: "random",
  duration: 600,
  easing: "ease-in-out",
  aspect_ratio: "16/9",
  fit: "auto",
  background: "",
  tap_action: "none",
  tap_pause_seconds: 8,
};

// Live integration settings the editor surfaces directly. Each maps to a
// sibling entity on the same device as the camera. We discover those
// siblings by their unique_id suffix (stable across renames), then read
// their current state for the form and write changes back through a
// service call. Buttons are handled separately (see LIVE_ACTIONS).
const LIVE_FIELDS = [
  "paused",
  "date_filter",
  "portrait_mode",
  "order_mode",
  "slide_interval",
  "pair_divider_px",
  "pair_divider_color",
];

const LIVE_SUFFIX = {
  paused: "_paused",
  date_filter: "_date_filter",
  portrait_mode: "_portrait_mode",
  order_mode: "_order_mode",
  slide_interval: "_interval",
  pair_divider_px: "_pair_divider_px",
  pair_divider_color: "_pair_divider_color",
  next_button: "_next_button",
  refresh_button: "_refresh_button",
};

const LIVE_LABELS = {
  live_paused: "Pause slideshow",
  live_date_filter: "Date filter",
  live_portrait_mode: "Orientation mismatch mode",
  live_order_mode: "Order mode",
  live_slide_interval: "Slide interval (seconds)",
  live_pair_divider_px: "Pair divider size (px)",
  live_pair_divider_color: "Pair divider color",
};

function humanizeOption(value) {
  return String(value)
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function createAlbumSlideshowCardEditorClass(Base) {
  return class AlbumSlideshowCardEditor extends Base {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._rendered = false;
    this._lastEntityCount = -1;
    // Live integration settings discovered from the camera's device.
    this._registryCache = null; // entity registry list, cached per editor
    this._siblings = null; // { field: entity_id } on the camera's device
    this._liveData = {}; // mirror of live_<field> values from entity states
    this._lastLiveSig = ""; // signature of surfaced entity states
  }

  setConfig(config) {
    this._config = { ...config };
    if (this._rendered) this._update();
  }

  set hass(hass) {
    const prev = this._hass;
    this._hass = hass;
    if (!this._rendered) {
      this._render();
      return;
    }
    // Forward hass to the form so selectors that need it (entity picker)
    // see entity state updates.
    const form = this.shadowRoot.querySelector("ha-form");
    if (form) form.hass = hass;
    // Re-run a full update when the camera set changes (warning box) or
    // when any surfaced integration entity changed state, so the live
    // controls stay in sync with the integration.
    if (
      !prev ||
      this._countSlideshowCameras() !== this._lastEntityCount ||
      this._liveSignature() !== this._lastLiveSig
    ) {
      this._update();
    }
  }

  _countSlideshowCameras() {
    if (!this._hass) return 0;
    let n = 0;
    for (const id of Object.keys(this._hass.states)) {
      if (isAlbumSlideshowCamera(this._hass.states[id])) n++;
    }
    return n;
  }

  /** Resolve the integration entities that live on the same device as the
   * selected camera. We match on unique_id suffix rather than entity_id,
   * because entity_id is derived from the (renameable) friendly name while
   * unique_id is stable. Requires one websocket call to the entity
   * registry, cached for the lifetime of the editor. */
  async _loadSiblings() {
    const camId = this._config && this._config.entity;
    this._siblings = null;
    if (!this._hass || !camId) return;
    const cam = this._hass.entities && this._hass.entities[camId];
    const deviceId = cam && cam.device_id;
    if (!deviceId) return;
    if (!this._registryCache) {
      try {
        this._registryCache = await this._hass.callWS({
          type: "config/entity_registry/list",
        });
      } catch (_) {
        return;
      }
    }
    const onDevice = this._registryCache.filter(
      (e) => e.device_id === deviceId,
    );
    const find = (suffix) => {
      const hit = onDevice.find(
        (e) => typeof e.unique_id === "string" && e.unique_id.endsWith(suffix),
      );
      return hit ? hit.entity_id : null;
    };
    const s = {};
    for (const key of Object.keys(LIVE_SUFFIX)) {
      s[key] = find(LIVE_SUFFIX[key]);
    }
    this._siblings = s;
  }

  _hasLiveControls() {
    if (!this._siblings) return false;
    return LIVE_FIELDS.some((f) => this._siblings[f]);
  }

  _hasActions() {
    return !!(
      this._siblings &&
      (this._siblings.next_button || this._siblings.refresh_button)
    );
  }

  /** Stable signature of the surfaced entity states, so a hass update only
   * triggers a refresh when something we display actually changed. */
  _liveSignature() {
    if (!this._siblings || !this._hass) return "";
    const parts = [];
    for (const f of LIVE_FIELDS) {
      const id = this._siblings[f];
      if (!id) continue;
      const st = this._hass.states[id];
      parts.push(`${f}=${st ? st.state : "?"}`);
    }
    return parts.join("|");
  }

  _liveSelectOptions(entityId) {
    const st = this._hass && this._hass.states[entityId];
    const options = (st && st.attributes && st.attributes.options) || [];
    return options.map((o) => ({ value: o, label: humanizeOption(o) }));
  }

  _liveNumberConfig(entityId, fallback) {
    const st = this._hass && this._hass.states[entityId];
    const a = (st && st.attributes) || {};
    return {
      min: a.min != null ? a.min : fallback.min,
      max: a.max != null ? a.max : fallback.max,
      step: a.step != null ? a.step : fallback.step,
      mode: "box",
      unit_of_measurement: fallback.unit,
    };
  }

  /** Schema for the live "Slideshow settings" section. Only includes
   * fields whose backing entity was found on the device. */
  _liveSchema() {
    const s = this._siblings || {};
    const items = [];
    if (s.paused) {
      items.push({ name: "live_paused", selector: { boolean: {} } });
    }
    for (const [field, id] of [
      ["date_filter", s.date_filter],
      ["portrait_mode", s.portrait_mode],
      ["order_mode", s.order_mode],
    ]) {
      if (id) {
        items.push({
          name: `live_${field}`,
          selector: {
            select: { mode: "dropdown", options: this._liveSelectOptions(id) },
          },
        });
      }
    }
    if (s.slide_interval) {
      items.push({
        name: "live_slide_interval",
        selector: {
          number: this._liveNumberConfig(s.slide_interval, {
            min: 3,
            max: 3600,
            step: 1,
            unit: "s",
          }),
        },
      });
    }
    if (s.pair_divider_px) {
      items.push({
        name: "live_pair_divider_px",
        selector: {
          number: this._liveNumberConfig(s.pair_divider_px, {
            min: 0,
            max: 64,
            step: 1,
            unit: "px",
          }),
        },
      });
    }
    if (s.pair_divider_color) {
      items.push({ name: "live_pair_divider_color", selector: { text: {} } });
    }
    return items;
  }

  /** ha-form schema. Card options are grouped into collapsible
   * ``expandable`` sections; a final section surfaces the integration's
   * own settings (date filter, orientation, pairing, ...) when the
   * backing entities are available. The whole form is delegated to
   * ``ha-form`` so each selector control lazy-loads itself. */
  _schema() {
    const schema = [
      {
        name: "entity",
        required: true,
        selector: {
          entity: {
            // Filter array form is what current HA expects. ``integration``
            // restricts to entities backed by the album_slideshow domain;
            // ``domain`` is a belt-and-braces fallback for older HA cores
            // that ignore ``integration``.
            filter: [{ integration: "album_slideshow", domain: "camera" }],
          },
        },
      },
      {
        type: "expandable",
        title: "Appearance",
        icon: "mdi:palette",
        expanded: true,
        schema: [
          {
            name: "transition",
            selector: {
              select: { mode: "dropdown", options: TRANSITION_OPTIONS },
            },
          },
          {
            type: "grid",
            name: "",
            schema: [
              {
                name: "duration",
                selector: {
                  number: {
                    min: 50,
                    max: 5000,
                    step: 50,
                    mode: "box",
                    unit_of_measurement: "ms",
                  },
                },
              },
              {
                name: "easing",
                selector: {
                  select: { mode: "dropdown", options: EASING_OPTIONS },
                },
              },
            ],
          },
          { name: "aspect_ratio", selector: { text: {} } },
          {
            name: "fit",
            selector: { select: { mode: "dropdown", options: FIT_OPTIONS } },
          },
          { name: "background", selector: { text: {} } },
        ],
      },
      {
        type: "expandable",
        title: "Interaction",
        icon: "mdi:gesture-tap",
        schema: [
          {
            name: "tap_action",
            selector: { select: { mode: "dropdown", options: TAP_OPTIONS } },
          },
          {
            name: "tap_pause_seconds",
            selector: {
              number: {
                min: 0,
                max: 120,
                step: 1,
                mode: "box",
                unit_of_measurement: "s",
              },
            },
          },
        ],
      },
    ];

    if (this._hasLiveControls()) {
      schema.push({
        type: "expandable",
        title: "Slideshow settings",
        icon: "mdi:tune",
        schema: this._liveSchema(),
      });
    }

    return schema;
  }

  /** Map config + live entity state to the flat data shape ha-form wants. */
  _data() {
    const c = this._config || {};
    return {
      entity: c.entity || "",
      transition: c.transition || DEFAULTS.transition,
      duration: c.duration != null ? Number(c.duration) : DEFAULTS.duration,
      easing: c.easing || DEFAULTS.easing,
      aspect_ratio: c.aspect_ratio || DEFAULTS.aspect_ratio,
      fit: c.fit || DEFAULTS.fit,
      background: c.background || "",
      tap_action: c.tap_action || DEFAULTS.tap_action,
      tap_pause_seconds:
        c.tap_pause_seconds != null
          ? Number(c.tap_pause_seconds)
          : DEFAULTS.tap_pause_seconds,
      ...this._liveDataFromStates(),
    };
  }

  /** Read the current value of each surfaced integration entity. */
  _liveDataFromStates() {
    const s = this._siblings;
    const out = {};
    if (!s || !this._hass) return out;
    const st = (id) => (id ? this._hass.states[id] : null);
    if (s.paused) {
      const e = st(s.paused);
      out.live_paused = !!e && e.state === "on";
    }
    for (const f of ["date_filter", "portrait_mode", "order_mode"]) {
      if (s[f]) {
        const e = st(s[f]);
        out[`live_${f}`] = e ? e.state : "";
      }
    }
    for (const f of ["slide_interval", "pair_divider_px"]) {
      if (s[f]) {
        const e = st(s[f]);
        out[`live_${f}`] = e ? Number(e.state) : null;
      }
    }
    if (s.pair_divider_color) {
      const e = st(s.pair_divider_color);
      out.live_pair_divider_color = e ? e.state : "";
    }
    return out;
  }

  _computeLabel = (s) => {
    const labels = {
      entity: "Album Slideshow camera",
      transition: "Transition",
      duration: "Duration (ms)",
      easing: "Easing",
      aspect_ratio: "Aspect ratio",
      fit: "Fit",
      background: "Background (optional)",
      tap_action: "Tap action",
      tap_pause_seconds: "Tap pause (seconds)",
      ...LIVE_LABELS,
    };
    return labels[s.name] || s.name;
  };

  _computeHelper = (s) => {
    const helpers = {
      background: "Leave blank to inherit the dashboard theme.",
      transition: "Random picks a different effect each slide.",
      tap_pause_seconds:
        "How long the card freezes its slide after a tap. 0 disables it.",
      live_paused:
        "These control the Album Slideshow integration directly and apply everywhere this album is shown, not only this card.",
    };
    return helpers[s.name] || "";
  };

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .card-config {
          display: flex;
          flex-direction: column;
          gap: 12px;
          padding: 4px 0;
        }
        ha-form { display: block; }
        .info-box {
          background: var(--warning-color);
          color: var(--primary-background-color);
          padding: 10px 14px; border-radius: 8px;
          font-size: 13px; line-height: 1.5;
        }
        .info-box strong { display: block; margin-bottom: 2px; }
        .actions {
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 8px;
          padding: 8px 12px 12px;
        }
        .actions-title {
          font-size: 13px; font-weight: 500;
          color: var(--secondary-text-color); margin-bottom: 8px;
        }
        .actions-row { display: flex; gap: 8px; flex-wrap: wrap; }
        .act {
          appearance: none; border: none; border-radius: 6px;
          padding: 8px 14px; font-size: 14px; cursor: pointer;
          background: var(--primary-color); color: var(--text-primary-color, #fff);
        }
        .act:hover { opacity: 0.9; }
      </style>
      <div class="card-config">
        <div class="info-slot"></div>
        <ha-form></ha-form>
        <div class="actions" hidden></div>
      </div>
    `;
    const form = this.shadowRoot.querySelector("ha-form");
    form.computeLabel = this._computeLabel;
    form.computeHelper = this._computeHelper;
    form.addEventListener("value-changed", (ev) => this._valueChanged(ev));
    this._rendered = true;
    this._update();
  }

  async _update() {
    if (!this._rendered) return;
    await this._loadSiblings();
    const form = this.shadowRoot.querySelector("ha-form");
    if (!form) return;
    this._liveData = this._liveDataFromStates();
    this._lastLiveSig = this._liveSignature();
    form.hass = this._hass;
    form.schema = this._schema();
    form.data = this._data();

    const count = this._countSlideshowCameras();
    this._lastEntityCount = count;
    const slot = this.shadowRoot.querySelector(".info-slot");
    if (count === 0) {
      slot.innerHTML = `
        <div class="info-box">
          <strong>No Album Slideshow cameras found.</strong>
          Add an Album Slideshow integration first; this card needs one of its camera entities.
        </div>
      `;
    } else {
      slot.innerHTML = "";
    }

    this._renderActions();
  }

  _renderActions() {
    const wrap = this.shadowRoot.querySelector(".actions");
    if (!wrap) return;
    if (!this._hasActions()) {
      wrap.hidden = true;
      wrap.innerHTML = "";
      return;
    }
    const s = this._siblings;
    wrap.hidden = false;
    wrap.innerHTML = `
      <div class="actions-title">Actions</div>
      <div class="actions-row">
        ${s.next_button ? `<button class="act" data-act="next">Next slide</button>` : ""}
        ${s.refresh_button ? `<button class="act" data-act="refresh">Refresh album</button>` : ""}
      </div>
    `;
    wrap.querySelectorAll("button.act").forEach((b) => {
      b.addEventListener("click", () => {
        const id = b.dataset.act === "next" ? s.next_button : s.refresh_button;
        if (id && this._hass) {
          this._hass.callService("button", "press", { entity_id: id });
        }
      });
    });
  }

  /** Apply a live settings change by calling the appropriate service on
   * the backing integration entity. */
  _applyLive(field, value) {
    const s = this._siblings;
    const hass = this._hass;
    if (!s || !hass) return;
    const id = s[field];
    if (!id) return;
    if (field === "paused") {
      hass.callService("switch", value ? "turn_on" : "turn_off", {
        entity_id: id,
      });
    } else if (
      field === "date_filter" ||
      field === "portrait_mode" ||
      field === "order_mode"
    ) {
      hass.callService("select", "select_option", {
        entity_id: id,
        option: value,
      });
    } else if (field === "slide_interval" || field === "pair_divider_px") {
      hass.callService("number", "set_value", {
        entity_id: id,
        value: Number(value),
      });
    } else if (field === "pair_divider_color") {
      hass.callService("text", "set_value", {
        entity_id: id,
        value: String(value),
      });
    }
  }

  _valueChanged(ev) {
    ev.stopPropagation();
    const data = ev?.detail?.value || {};

    // A changed live_* field maps to an integration entity, not card
    // config: route it to a service call and stop. Only one field changes
    // per event, so the first difference we find is the edit.
    if (this._siblings) {
      for (const field of LIVE_FIELDS) {
        const key = `live_${field}`;
        if (
          key in data &&
          this._siblings[field] &&
          data[key] !== this._liveData[key]
        ) {
          this._applyLive(field, data[key]);
          this._liveData = { ...this._liveData, [key]: data[key] };
          return;
        }
      }
    }

    const n = { type: "custom:album-slideshow-card" };

    if (data.entity) n.entity = data.entity;

    const t = data.transition || DEFAULTS.transition;
    if (t !== DEFAULTS.transition) n.transition = t;

    const dur = Number(data.duration);
    if (!isNaN(dur) && dur !== DEFAULTS.duration) n.duration = dur;

    const easing = data.easing || DEFAULTS.easing;
    if (easing !== DEFAULTS.easing) n.easing = easing;

    const aspect = (data.aspect_ratio || "").trim();
    if (aspect && aspect !== DEFAULTS.aspect_ratio) n.aspect_ratio = aspect;

    const fit = data.fit || DEFAULTS.fit;
    if (fit !== DEFAULTS.fit) n.fit = fit;

    const bg = (data.background || "").trim();
    if (bg) n.background = bg;

    const ta = data.tap_action || DEFAULTS.tap_action;
    if (ta !== DEFAULTS.tap_action) n.tap_action = ta;

    const tps = Number(data.tap_pause_seconds);
    if (!isNaN(tps) && tps !== DEFAULTS.tap_pause_seconds) {
      n.tap_pause_seconds = tps;
    }

    this._config = n;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: n },
        bubbles: true,
        composed: true,
      }),
    );
  }
  };
}

/**
 * Register both elements so they survive the
 * ``@webcomponents/scoped-custom-element-registry`` polyfill that
 * browser_mod and hui-element load. That polyfill replaces both
 * ``window.customElements`` and ``window.HTMLElement``. A custom element
 * only works if its class extends the *current* global ``HTMLElement``
 * and is registered in the *current* global registry:
 *
 *   - If we register against the native objects and the polyfill later
 *     swaps the globals, HA looks the element up in the new registry,
 *     finds nothing, and renders "Custom element doesn't exist"
 *     (or throws "Illegal constructor" when it tries to build it).
 *   - If the polyfill is already active and we extend the native
 *     ``HTMLElement`` instead of the polyfilled one, the polyfilled
 *     ``define`` silently refuses the registration.
 *
 * Building the classes from the live globals on every pass, and
 * re-running after the polyfill has had a chance to load, covers all
 * orderings. ``get()`` guards make repeat passes harmless no-ops.
 */
function defineAlbumSlideshowCards() {
  const reg = window.customElements;
  if (!reg) return;
  const Base = window.HTMLElement;
  if (!reg.get("album-slideshow-card")) {
    reg.define(
      "album-slideshow-card",
      createAlbumSlideshowCardClass(Base),
    );
  }
  if (!reg.get("album-slideshow-card-editor")) {
    reg.define(
      "album-slideshow-card-editor",
      createAlbumSlideshowCardEditorClass(Base),
    );
  }
}

defineAlbumSlideshowCards();
if (!window.__albumSlideshowCardScheduled) {
  window.__albumSlideshowCardScheduled = true;
  const retry = () => {
    try {
      defineAlbumSlideshowCards();
    } catch (_) {
      /* a concurrent registry swap is harmless; the next pass settles it */
    }
  };
  Promise.resolve().then(retry);
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(retry);
  }
  setTimeout(retry, 0);
  setTimeout(retry, 1000);
}

window.customCards = window.customCards || [];
if (!window.customCards.find((c) => c.type === "album-slideshow-card")) {
  window.customCards.push({
    type: "album-slideshow-card",
    name: "Album Slideshow",
    description:
      "Cross-fade slideshow for album_slideshow cameras (browser-side, GPU-composited)",
    preview: false,
    documentationURL:
      "https://github.com/eyalgal/album_slideshow#album-slideshow-card",
    // HA 2026.6+ "By entity" card picker (Community section). Only
    // suggest for cameras created by THIS integration, never for every
    // camera in the house - the dev blog warns an over-eager hook makes
    // the picker noisy. We gate on the entity's platform AND the camera
    // domain rather than matching the bare ``camera.*`` domain.
    getEntitySuggestion: (hass, entityId) => {
      if (typeof entityId !== "string" || !entityId.startsWith("camera.")) {
        return null;
      }
      const entry = hass && hass.entities && hass.entities[entityId];
      if (!entry || entry.platform !== "album_slideshow") {
        return null;
      }
      return {
        config: {
          type: "custom:album-slideshow-card",
          entity: entityId,
        },
      };
    },
  });
}

console.info(
  `%c album-slideshow-card %c v${VERSION} `,
  "color: white; background: #4a90e2; padding: 1px 4px; border-radius: 3px 0 0 3px;",
  "color: #4a90e2; background: white; padding: 1px 4px; border-radius: 0 3px 3px 0;",
);
