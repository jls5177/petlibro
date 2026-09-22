const CARD_TYPE = "petlibro-meal-card";
const FULL_CARD_TYPE = `custom:${CARD_TYPE}`;
const PETLIBRO_PLATFORM = "petlibro";
const HISTORY_DAYS = 7;
const MAX_TIMELINE_ROWS = 12;
const FULL_WIDTH_GRID_OPTIONS = Object.freeze({
  columns: "full",
  rows: "auto",
});
const PHOTO_GRID_OPTIONS = Object.freeze({
  columns: 9,
  rows: "auto",
});
const MEAL_KEYS = Object.freeze({
  image: "latest_event_thumbnail",
  time: "last_meal_time",
  intake: "last_meal_intake",
  duration: "last_meal_duration",
  pets: "last_meal_pets",
});
const SUPPORTED_KEYS = new Set(Object.values(MEAL_KEYS));

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function entityEntry(hass, entityId) {
  return hass?.entities?.[entityId];
}

function isMealEntity(hass, entityId) {
  const entry = entityEntry(hass, entityId);
  return (
    entry?.platform === PETLIBRO_PLATFORM &&
    entry.device_id &&
    SUPPORTED_KEYS.has(entry.translation_key)
  );
}

function resolveMealEntities(hass, config) {
  const selected = entityEntry(hass, config.entity);
  const deviceId =
    selected?.platform === PETLIBRO_PLATFORM && selected.device_id
      ? selected.device_id
      : config.device_id || selected?.device_id;
  if (!deviceId) {
    return {};
  }

  const resolved = {};
  for (const entry of Object.values(hass.entities || {})) {
    if (
      entry.device_id !== deviceId ||
      entry.platform !== PETLIBRO_PLATFORM ||
      !SUPPORTED_KEYS.has(entry.translation_key)
    ) {
      continue;
    }
    const key = Object.entries(MEAL_KEYS).find(
      ([, translationKey]) => translationKey === entry.translation_key,
    )?.[0];
    if (key) {
      resolved[key] = entry.entity_id;
    }
  }
  return resolved;
}

function suggestionsForEntity(hass, entityId) {
  if (!isMealEntity(hass, entityId)) {
    return null;
  }
  const entry = entityEntry(hass, entityId);
  const siblings = resolveMealEntities(hass, {
    entity: entityId,
    device_id: entry.device_id,
  });
  if (!siblings.time) {
    return null;
  }
  return [
    {
      label: "Compact",
      config: {
        type: FULL_CARD_TYPE,
        entity: entityId,
        device_id: entry.device_id,
        variant: "compact",
        grid_options: { ...FULL_WIDTH_GRID_OPTIONS },
      },
    },
    {
      label: "Photo",
      config: {
        type: FULL_CARD_TYPE,
        entity: entityId,
        device_id: entry.device_id,
        variant: "photo",
        grid_options: { ...PHOTO_GRID_OPTIONS },
      },
    },
    {
      label: "Timeline",
      config: {
        type: FULL_CARD_TYPE,
        entity: entityId,
        device_id: entry.device_id,
        variant: "timeline",
        grid_options: { ...FULL_WIDTH_GRID_OPTIONS },
      },
    },
  ];
}

class PetLibroMealCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._timeline = [];
    this._timelineError = false;
    this._timelineLoading = false;
    this._timelineKey = "";
    this._timelineGeneration = 0;
  }

  static getConfigForm() {
    return {
      schema: [
        {
          name: "entity",
          required: true,
          selector: {
            entity: {
              filter: [
                { integration: PETLIBRO_PLATFORM, domain: "image" },
                { integration: PETLIBRO_PLATFORM, domain: "sensor" },
              ],
            },
          },
        },
        {
          name: "variant",
          required: true,
          selector: {
            select: {
              mode: "dropdown",
              options: [
                { value: "compact", label: "Compact" },
                { value: "photo", label: "Photo" },
                { value: "timeline", label: "Timeline" },
              ],
            },
          },
        },
        {
          name: "title",
          selector: { text: {} },
        },
      ],
      assertConfig: (config) => {
        if (
          config.variant &&
          !["compact", "photo", "timeline"].includes(config.variant)
        ) {
          throw new Error("Unsupported PETLIBRO meal card variant");
        }
      },
    };
  }

  static getStubConfig(hass, entities = [], entitiesFallback = []) {
    const candidates = [
      ...entities,
      ...entitiesFallback,
      ...Object.keys(hass?.entities || {}),
    ];
    return {
      entity:
        candidates.find((entityId) => isMealEntity(hass, entityId)) || "",
      variant: "compact",
    };
  }

  setConfig(config) {
    this._config = {
      variant: "compact",
      ...config,
    };
    this._timeline = [];
    this._timelineKey = "";
    this._timelineGeneration += 1;
    this._renderToken = "";
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const renderToken = this._computeRenderToken();
    if (renderToken !== this._renderToken) {
      this._renderToken = renderToken;
      this._render();
    }
    if (this._config?.variant === "timeline") {
      this._scheduleTimelineLoad();
    }
  }

  getCardSize() {
    if (this._config?.variant === "photo") {
      return 6;
    }
    if (this._config?.variant === "timeline") {
      return 5;
    }
    return 3;
  }

  getGridOptions() {
    if (this._config?.variant === "photo") {
      return { ...PHOTO_GRID_OPTIONS, min_columns: 6 };
    }
    return { ...FULL_WIDTH_GRID_OPTIONS };
  }

  _states() {
    const entities = resolveMealEntities(this._hass, this._config || {});
    const states = {};
    for (const [key, entityId] of Object.entries(entities)) {
      states[key] = this._hass?.states?.[entityId];
    }
    return { entities, states };
  }

  _computeRenderToken() {
    if (!this._hass || !this._config) {
      return "";
    }
    const { entities, states } = this._states();
    return JSON.stringify({
      variant: this._config.variant,
      title: this._config.title,
      locale: this._hass.locale?.language,
      entities,
      states: Object.fromEntries(
        Object.entries(states).map(([key, state]) => [
          key,
          state
            ? [
                state.state,
                state.last_updated,
                state.attributes?.entity_picture,
              ]
            : null,
        ]),
      ),
    });
  }

  _format(state) {
    if (!state || ["unknown", "unavailable"].includes(state.state)) {
      return "Unavailable";
    }
    return this._hass?.formatEntityState
      ? this._hass.formatEntityState(state)
      : state.state;
  }

  _imageURL(state) {
    const picture = state?.attributes?.entity_picture;
    if (typeof picture !== "string" || !picture.startsWith("/api/")) {
      return null;
    }
    return this._hass?.hassUrl ? this._hass.hassUrl(picture) : picture;
  }

  _title(states) {
    if (this._config?.title) {
      return this._config.title;
    }
    const imageName = states.image?.attributes?.friendly_name;
    if (imageName) {
      return imageName.replace(/\s+Latest Event$/i, "");
    }
    return "Latest PETLIBRO Meal";
  }

  _detailRows(states) {
    const rows = [
      ["mdi:paw", "Cat", this._format(states.pets)],
      ["mdi:scale", "Intake", this._format(states.intake)],
      ["mdi:timer-outline", "Duration", this._format(states.duration)],
      [
        "mdi:clock-outline",
        "Meal",
        this._format(states.time),
      ],
    ];
    return rows
      .filter(([, , value]) => value !== "Unavailable")
      .map(
        ([icon, label, value]) => `
          <div class="detail">
            <ha-icon icon="${icon}" aria-hidden="true"></ha-icon>
            <span class="label">${escapeHTML(label)}</span>
            <span class="value">${escapeHTML(value)}</span>
          </div>`,
      )
      .join("");
  }

  _renderImage(state, className) {
    const url = this._imageURL(state);
    if (!url) {
      return `
        <div class="${className} placeholder" role="img" aria-label="No meal photo available">
          <ha-icon icon="mdi:image-off-outline"></ha-icon>
        </div>`;
    }
    return `<img class="${className}" src="${escapeHTML(url)}" alt="Latest PETLIBRO meal" />`;
  }

  _renderCompact(states) {
    return `
      <div class="compact">
        ${this._renderImage(states.image, "compact-image")}
        <div class="compact-content">
          <h2>${escapeHTML(this._title(states))}</h2>
          ${this._detailRows(states) || '<div class="empty">No meal details available</div>'}
        </div>
      </div>`;
  }

  _renderPhoto(states) {
    return `
      <div class="photo">
        ${this._renderImage(states.image, "photo-image")}
        <div class="photo-content">
          <h2>${escapeHTML(this._title(states))}</h2>
          <div class="details">
            ${this._detailRows(states) || '<div class="empty">No meal details available</div>'}
          </div>
        </div>
      </div>`;
  }

  _renderTimeline(states) {
    let content;
    if (this._timelineLoading) {
      content = '<div class="empty">Loading meal history…</div>';
    } else if (this._timeline.length) {
      content = this._timeline
        .map(
          (meal) => `
            <div class="timeline-row">
              <div class="timeline-time">${escapeHTML(meal.time)}</div>
              <div class="timeline-summary">
                <strong>${escapeHTML(meal.pets || "Meal")}</strong>
                <span>${escapeHTML([meal.intake, meal.duration].filter(Boolean).join(" · "))}</span>
              </div>
            </div>`,
        )
        .join("");
    } else {
      content = `<div class="empty">${
        this._timelineError
          ? "Meal history is unavailable; showing the current meal."
          : "No prior meal changes were recorded."
      }</div>`;
    }
    return `
      <div class="timeline">
        <div class="timeline-header">
          ${this._renderImage(states.image, "timeline-image")}
          <div>
            <h2>${escapeHTML(this._title(states))}</h2>
          <div class="current-meal">${escapeHTML(this._format(states.time))}</div>
          </div>
        </div>
        <div class="timeline-list">${content}</div>
      </div>`;
  }

  _render() {
    if (!this.shadowRoot || !this._config) {
      return;
    }
    if (!this._config.entity) {
      this.shadowRoot.innerHTML = this._styles(
        '<ha-card><div class="empty">Select a PETLIBRO meal entity in the card editor.</div></ha-card>',
      );
      return;
    }
    if (!this._hass) {
      this.shadowRoot.innerHTML = this._styles(
        '<ha-card><div class="empty">Loading PETLIBRO meal…</div></ha-card>',
      );
      return;
    }
    const { states } = this._states();
    let body;
    if (this._config.variant === "photo") {
      body = this._renderPhoto(states);
    } else if (this._config.variant === "timeline") {
      body = this._renderTimeline(states);
    } else {
      body = this._renderCompact(states);
    }
    this.shadowRoot.innerHTML = this._styles(
      `<ha-card tabindex="0" role="button" aria-label="${escapeHTML(this._title(states))}">
        ${body}
      </ha-card>`,
    );
    const openMoreInfo = () => {
      const event = new CustomEvent("hass-more-info", {
        bubbles: true,
        composed: true,
        detail: { entityId: this._config.entity },
      });
      this.dispatchEvent(event);
    };
    const card = this.shadowRoot.querySelector("ha-card");
    card?.addEventListener("click", openMoreInfo);
    card?.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openMoreInfo();
      }
    });
  }

  _styles(content) {
    return `
      <style>
        :host { display: block; height: 100%; }
        ha-card {
          height: 100%;
          overflow: hidden;
          cursor: pointer;
          color: var(--primary-text-color);
          background: var(--ha-card-background, var(--card-background-color));
        }
        ha-card:focus-visible {
          outline: 2px solid var(--primary-color);
          outline-offset: -2px;
        }
        h2 {
          margin: 0 0 10px;
          font-size: 1.1rem;
          line-height: 1.25;
        }
        .compact {
          display: grid;
          grid-template-columns: minmax(100px, 38%) 1fr;
          min-height: 168px;
          height: 100%;
        }
        .compact-image, .timeline-image {
          width: 100%;
          height: 100%;
          object-fit: cover;
        }
        .compact-content, .photo-content {
          padding: 16px;
          min-width: 0;
        }
        .detail {
          display: grid;
          grid-template-columns: 24px minmax(52px, auto) 1fr;
          gap: 7px;
          align-items: center;
          margin: 7px 0;
          font-size: 0.9rem;
        }
        .detail ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
        .label { color: var(--secondary-text-color); }
        .value { text-align: right; overflow-wrap: anywhere; }
        .photo-image {
          display: block;
          width: 100%;
          aspect-ratio: 16 / 9;
          object-fit: cover;
          background: var(--secondary-background-color);
        }
        .photo .details {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 0 16px;
        }
        .placeholder {
          display: grid;
          place-items: center;
          color: var(--secondary-text-color);
          background: var(--secondary-background-color);
        }
        .placeholder ha-icon { --mdc-icon-size: 40px; }
        .timeline { padding: 16px; }
        .timeline-header {
          display: grid;
          grid-template-columns: 72px 1fr;
          gap: 12px;
          align-items: center;
          margin-bottom: 12px;
        }
        .timeline-image {
          width: 72px;
          height: 72px;
          border-radius: 10px;
        }
        .current-meal, .empty { color: var(--secondary-text-color); }
        .timeline-list { display: grid; gap: 2px; }
        .timeline-row {
          display: grid;
          grid-template-columns: minmax(115px, auto) 1fr;
          gap: 12px;
          padding: 9px 0;
          border-top: 1px solid var(--divider-color);
        }
        .timeline-time { color: var(--secondary-text-color); font-size: 0.88rem; }
        .timeline-summary { display: flex; justify-content: space-between; gap: 8px; }
        .timeline-summary span { color: var(--secondary-text-color); text-align: right; }
        .empty { padding: 20px; text-align: center; }
        @media (max-width: 440px) {
          .compact { grid-template-columns: 110px 1fr; }
          .compact-content { padding: 12px; }
          .detail { grid-template-columns: 20px 1fr; }
          .detail .label { display: none; }
          .timeline-row { grid-template-columns: 1fr; gap: 3px; }
        }
      </style>
      ${content}`;
  }

  _scheduleTimelineLoad() {
    const { entities, states } = this._states();
    if (!entities.time) {
      return;
    }
    const key = [
      entities.time,
      entities.pets,
      entities.intake,
      entities.duration,
      states.time?.state,
    ].join("|");
    if (this._timelineLoading || this._timelineKey === key) {
      return;
    }
    this._timelineKey = key;
    this._loadTimeline(entities, key, this._timelineGeneration);
  }

  async _loadTimeline(entities, requestKey, generation) {
    this._timelineLoading = true;
    this._timelineError = false;
    this._render();
    const entityIds = [
      entities.time,
      entities.pets,
      entities.intake,
      entities.duration,
    ].filter(Boolean);
    try {
      const end = new Date();
      const start = new Date(end.getTime() - HISTORY_DAYS * 86400000);
      const history = await this._hass.callWS({
        type: "history/history_during_period",
        start_time: start.toISOString(),
        end_time: end.toISOString(),
        entity_ids: entityIds,
        include_start_time_state: true,
        significant_changes_only: false,
        minimal_response: false,
        no_attributes: true,
      });
      if (
        generation !== this._timelineGeneration ||
        requestKey !== this._timelineKey
      ) {
        return;
      }
      this._timeline = this._buildTimeline(
        history,
        entities,
        start.getTime(),
        end.getTime(),
      );
    } catch (_error) {
      if (
        generation === this._timelineGeneration &&
        requestKey === this._timelineKey
      ) {
        this._timeline = [];
        this._timelineError = true;
      }
    } finally {
      if (
        generation === this._timelineGeneration &&
        requestKey === this._timelineKey
      ) {
        this._timelineLoading = false;
        this._render();
      } else {
        this._timelineLoading = false;
        this._scheduleTimelineLoad();
      }
    }
  }

  _buildTimeline(history, entities, startTime = -Infinity, endTime = Infinity) {
    const timeStates = history?.[entities.time] || [];
    const recordState = (record) => record?.s ?? record?.state;
    const recordTime = (record) => {
      const compressed = record?.lu ?? record?.lc;
      if (typeof compressed === "number") {
        return compressed * 1000;
      }
      return Date.parse(record?.last_updated || record?.last_changed);
    };
    const valueAt = (entityId, changedAt) => {
      if (!entityId) {
        return "";
      }
      const candidates = history?.[entityId] || [];
      let best;
      let bestDistance = Number.POSITIVE_INFINITY;
      for (const state of candidates) {
        const stateTime = recordTime(state);
        const distance = changedAt - stateTime;
        if (
          Number.isFinite(stateTime) &&
          stateTime <= changedAt + 30000 &&
          Math.abs(distance) < bestDistance
        ) {
          best = state;
          bestDistance = Math.abs(distance);
        }
      }
      const value = recordState(best);
      if (!best || ["unknown", "unavailable"].includes(value)) {
        return "";
      }
      const current = this._hass.states[entityId];
      return current
        ? this._hass.formatEntityState({ ...current, state: value })
        : value;
    };

    const seen = new Set();
    const rows = [];
    for (const state of [...timeStates].reverse()) {
      const value = recordState(state);
      if (
        !value ||
        ["unknown", "unavailable"].includes(value) ||
        seen.has(value)
      ) {
        continue;
      }
      const mealDate = new Date(value);
      const mealTime = mealDate.getTime();
      if (
        Number.isNaN(mealTime) ||
        mealTime < startTime ||
        mealTime > endTime
      ) {
        continue;
      }
      seen.add(value);
      const changedAt = recordTime(state);
      if (!Number.isFinite(changedAt)) {
        continue;
      }
      rows.push({
        time: new Intl.DateTimeFormat(
          this._hass?.locale?.language || undefined,
          { dateStyle: "short", timeStyle: "short" },
        ).format(mealDate),
        pets: valueAt(entities.pets, changedAt),
        intake: valueAt(entities.intake, changedAt),
        duration: valueAt(entities.duration, changedAt),
      });
      if (rows.length >= MAX_TIMELINE_ROWS) {
        break;
      }
    }
    return rows;
  }
}

if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, PetLibroMealCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TYPE)) {
  window.customCards.push({
    type: CARD_TYPE,
    name: "PETLIBRO Meal",
    description: "Latest PETLIBRO meal photo and details",
    preview: true,
    documentationURL:
      "https://github.com/jjjonesjr33/petlibro#petlibro-meal-dashboard-card",
    getEntitySuggestion: suggestionsForEntity,
  });
}
