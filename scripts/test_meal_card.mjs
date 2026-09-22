import fs from "node:fs";
import vm from "node:vm";

let CardClass;
const context = {
  window: { customCards: [] },
  HTMLElement: class {},
  customElements: {
    get: () => undefined,
    define: (_name, cardClass) => {
      CardClass = cardClass;
    },
  },
  CustomEvent: class {},
  Intl,
  Date,
  console,
};

vm.createContext(context);
vm.runInContext(
  fs.readFileSync(
    "custom_components/petlibro/frontend/petlibro-meal-card.js",
    "utf8",
  ),
  context,
);

const metadata = context.window.customCards.find(
  (item) => item.type === "petlibro-meal-card",
);
if (!metadata || !CardClass) {
  throw new Error("PETLIBRO meal card registration is missing");
}

const entities = {
  "image.kitchen_latest_event": {
    entity_id: "image.kitchen_latest_event",
    device_id: "device-1",
    platform: "petlibro",
    translation_key: "latest_event_thumbnail",
  },
  "sensor.kitchen_last_meal_time": {
    entity_id: "sensor.kitchen_last_meal_time",
    device_id: "device-1",
    platform: "petlibro",
    translation_key: "last_meal_time",
  },
  "sensor.kitchen_last_meal_intake": {
    entity_id: "sensor.kitchen_last_meal_intake",
    device_id: "device-1",
    platform: "petlibro",
    translation_key: "last_meal_intake",
  },
  "sensor.kitchen_last_meal_duration": {
    entity_id: "sensor.kitchen_last_meal_duration",
    device_id: "device-1",
    platform: "petlibro",
    translation_key: "last_meal_duration",
  },
  "sensor.kitchen_last_meal_pets": {
    entity_id: "sensor.kitchen_last_meal_pets",
    device_id: "device-1",
    platform: "petlibro",
    translation_key: "last_meal_pets",
  },
  "image.office_latest_event": {
    entity_id: "image.office_latest_event",
    device_id: "device-2",
    platform: "petlibro",
    translation_key: "latest_event_thumbnail",
  },
  "sensor.office_last_meal_time": {
    entity_id: "sensor.office_last_meal_time",
    device_id: "device-2",
    platform: "petlibro",
    translation_key: "last_meal_time",
  },
};
const hass = {
  entities,
  states: {},
  locale: { language: "en" },
  formatEntityState: (state) => state.state,
};

const suggestions = metadata.getEntitySuggestion(
  hass,
  "image.kitchen_latest_event",
);
if (
  !Array.isArray(suggestions) ||
  suggestions.map((item) => item.label).join(",") !==
    "Compact,Photo,Timeline"
) {
  throw new Error("Expected Compact, Photo, and Timeline suggestions");
}
for (const label of ["Compact", "Timeline"]) {
  const suggestion = suggestions.find((item) => item.label === label);
  if (
    suggestion.config.grid_options?.columns !== "full" ||
    suggestion.config.grid_options?.rows !== "auto"
  ) {
    throw new Error(`${label} suggestion did not request a full-width grid`);
  }
}
const photoSuggestion = suggestions.find((item) => item.label === "Photo");
if (
  photoSuggestion.config.grid_options?.columns !== 9 ||
  photoSuggestion.config.grid_options?.rows !== "auto"
) {
  throw new Error("Photo suggestion did not request a nine-column grid");
}
if (
  CardClass.getStubConfig(hass, [], []).entity !==
  "image.kitchen_latest_event"
) {
  throw new Error("Card stub did not select a PETLIBRO meal entity");
}

const instance = Object.create(CardClass.prototype);
instance._config = { variant: "compact" };
if (
  instance.getGridOptions().columns !== "full" ||
  instance.getGridOptions().rows !== "auto"
) {
  throw new Error("Compact card grid defaults are not full width and auto height");
}
instance._config = { variant: "timeline" };
if (
  instance.getGridOptions().columns !== "full" ||
  instance.getGridOptions().rows !== "auto"
) {
  throw new Error(
    "Timeline card grid defaults are not full width and auto height",
  );
}
instance._config = { variant: "photo" };
const photoGridOptions = instance.getGridOptions();
if (
  photoGridOptions.columns !== 9 ||
  photoGridOptions.rows !== "auto" ||
  photoGridOptions.min_columns !== 6
) {
  throw new Error("Photo card grid sizing does not enforce its minimum width");
}
instance._hass = {
  ...hass,
  states: {
    "sensor.kitchen_last_meal_intake": {
      state: "20",
      attributes: { unit_of_measurement: "g" },
    },
    "sensor.kitchen_last_meal_duration": {
      state: "30",
      attributes: { unit_of_measurement: "s" },
    },
    "sensor.kitchen_last_meal_pets": {
      state: "Milo",
      attributes: {},
    },
  },
};
instance._config = {
  entity: "image.office_latest_event",
  device_id: "device-1",
  variant: "compact",
};
if (
  instance._states().entities.time !== "sensor.office_last_meal_time" ||
  instance._states().entities.image !== "image.office_latest_event"
) {
  throw new Error("Edited entity did not replace the stored device association");
}
const history = {
  "sensor.kitchen_last_meal_time": [
    { s: "2026-09-01T12:00:00Z", lu: 100 },
    { s: "2026-09-20T12:00:00Z", lu: 200 },
    { s: "2026-09-21T12:00:00Z", lu: 300 },
  ],
  "sensor.kitchen_last_meal_pets": [{ s: "Milo", lu: 50 }],
  "sensor.kitchen_last_meal_intake": [
    { s: "16", lu: 199 },
    { s: "20", lu: 299 },
  ],
  "sensor.kitchen_last_meal_duration": [{ s: "30", lu: 198 }],
};
const rows = instance._buildTimeline(
  history,
  {
    time: "sensor.kitchen_last_meal_time",
    pets: "sensor.kitchen_last_meal_pets",
    intake: "sensor.kitchen_last_meal_intake",
    duration: "sensor.kitchen_last_meal_duration",
  },
  Date.parse("2026-09-15T00:00:00Z"),
  Date.parse("2026-09-22T00:00:00Z"),
);
if (rows.length !== 2) {
  throw new Error(`Expected two in-window meals, got ${rows.length}`);
}
if (rows.some((row) => row.pets !== "Milo")) {
  throw new Error("Timeline did not carry the unchanged pet value forward");
}
if (rows[0].intake !== "20" || rows[1].intake !== "16") {
  throw new Error("Timeline did not correlate meal intake values");
}

const throttled = Object.create(CardClass.prototype);
throttled._config = {
  entity: "image.kitchen_latest_event",
  device_id: "device-1",
  variant: "compact",
};
throttled._renderToken = "";
throttled._timeline = [];
throttled._render = () => {
  throttled.renderCount = (throttled.renderCount || 0) + 1;
};
throttled._scheduleTimelineLoad = () => {};
const mealStates = {
  "image.kitchen_latest_event": {
    state: "2026-09-21",
    last_updated: "1",
    attributes: { entity_picture: "/api/image/1" },
  },
  "sensor.kitchen_last_meal_time": {
    state: "2026-09-21T12:00:00Z",
    last_updated: "1",
    attributes: {},
  },
};
throttled.hass = {
  ...hass,
  states: { ...mealStates, "sensor.unrelated": { state: "1" } },
};
throttled.hass = {
  ...hass,
  states: { ...mealStates, "sensor.unrelated": { state: "2" } },
};
if (throttled.renderCount !== 1) {
  throw new Error("Unrelated entity changes caused a card render");
}

console.log("PETLIBRO meal card checks passed");
