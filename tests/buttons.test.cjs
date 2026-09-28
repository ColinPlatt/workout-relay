const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync("workout_relay/static/app.js", "utf8");
const busy = source.match(/function setBusy\(button, busy\) \{[\s\S]*?\n\}/)[0];

for (const id of ["validate-plan", "upload-plan", "create-key"]) {
  for (const fails of [false, true]) {
    test(`${id} restores its button after ${fails ? "failure" : "success"}`, async () => {
      const button = { dataset: {}, textContent: "Original label", disabled: false };
      let handler, resolve, reject;
      const response = new Promise((yes, no) => { resolve = yes; reject = no; });
      const start = source.indexOf(`$("#${id}").addEventListener`);
      const end = source.indexOf("\n});", start) + 4;
      const context = {
        $: (selector) => selector === `#${id}`
          ? { addEventListener: (_, callback) => { handler = callback; } }
          : { value: "Test", classList: { remove() {} } },
        api: () => response,
        parsePlan: () => ({}), t: (key) => key,
        showPlanResult() {}, showToast() {}, errorText: () => "Error",
        loadHistory: async () => {}, loadKeys: async () => {},
        waitForSubmission: async () => ({ result: { counts: {} } }),
      };
      vm.runInNewContext(`${busy}\n${source.slice(start, end)}`, context);
      const event = { currentTarget: button };
      const result = handler(event);
      assert.equal(button.disabled, true);
      // Browsers clear currentTarget immediately after synchronous dispatch.
      event.currentTarget = null;
      if (fails) reject(new Error("network failure"));
      else resolve({ id: "submission", workout_count: 1, token: "test-token" });
      await result;
      assert.equal(button.disabled, false);
      assert.equal(button.textContent, "Original label");
    });
  }
}

for (const consented of [false, true]) {
  test(`activity key scope requires explicit checkbox consent: ${consented}`, async () => {
    let handler, sent;
    const checkbox = { checked: consented };
    const start = source.indexOf('$("#create-key").addEventListener');
    const end = source.indexOf("\n});", start) + 4;
    const context = {
      $: (selector) => selector === "#create-key"
        ? { addEventListener: (_, callback) => { handler = callback; } }
        : selector === "#key-activities" ? checkbox
        : { value: "Test", classList: { remove() {} } },
      api: async (_, options) => { sent = JSON.parse(options.body); return { token: "test" }; },
      t: (key) => key, showToast() {}, errorText: () => "Error", loadKeys: async () => {},
    };
    vm.runInNewContext(`${busy}\n${source.slice(start, end)}`, context);
    await handler({ currentTarget: { dataset: {}, textContent: "Create", disabled: false } });
    assert.equal(sent.scopes.includes("activities:read"), consented);
    assert.equal(checkbox.checked, false);
  });
}

// Deleting a workout is irreversible in Garmin, so the row's button must not
// send anything until the person has confirmed, and a past date has to be
// asked for explicitly rather than smuggled through.
function loadWorkoutsContext({ items, agree }) {
  const calls = { posted: [], confirmed: [] };
  const list = {
    innerHTML: "",
    querySelectorAll() {
      const ids = [...String(list.innerHTML).matchAll(/data-workout-id="([^"]+)"/g)].map((m) => m[1]);
      return ids.map((id) => ({
        dataset: { workoutId: id },
        textContent: "Delete",
        disabled: false,
        addEventListener: (_, callback) => { this.handlers = (this.handlers || []).concat(callback); },
      }));
    },
  };
  const context = {
    $: () => list,
    api: async (path, options) => {
      if (options?.method === "POST") { calls.posted.push(JSON.parse(options.body)); return {}; }
      return { items };
    },
    t: (key, params) => { if (key.startsWith("confirm")) calls.confirmed.push({ key, params }); return key; },
    confirm: (message) => { calls.message = message; return agree; },
    escapeHtml: (value) => String(value),
    setBusy() {}, showToast() {}, errorText: () => "Error",
    pollDeletion: async () => {}, loadHistory: async () => {},
    Promise, Date, window: { setTimeout },
  };
  return { context, list, calls };
}

for (const agree of [false, true]) {
  test(`a workout is deleted only after confirmation: ${agree}`, async () => {
    const source = fs.readFileSync("workout_relay/static/app.js", "utf8");
    const fn = source.match(/async function loadWorkouts\(\) \{[\s\S]*?\n\}/)[0];
    const items = [{ workout_id: "w1", title: "Easy 30", scheduled_date: "2099-01-01" }];
    const { context, list, calls } = loadWorkoutsContext({ items, agree });
    vm.runInNewContext(`${fn}\nglobalThis.__run = loadWorkouts;`, context);
    await context.__run();
    const buttons = list.querySelectorAll("button");
    assert.equal(buttons.length, 1);
    // Re-attach: querySelectorAll builds fresh objects, so drive the real one.
    let handler;
    list.querySelectorAll = () => [{
      dataset: { workoutId: "w1" }, textContent: "Delete", disabled: false,
      addEventListener: (_, callback) => { handler = callback; },
    }];
    await context.__run();
    await handler();
    assert.equal(calls.posted.length, agree ? 1 : 0);
    if (agree) assert.deepEqual(calls.posted[0], { workout_ids: ["w1"], include_past: false });
  });
}

test("a past workout is deleted with include_past, under its own warning", async () => {
  const source = fs.readFileSync("workout_relay/static/app.js", "utf8");
  const fn = source.match(/async function loadWorkouts\(\) \{[\s\S]*?\n\}/)[0];
  const items = [{ workout_id: "old", title: "Long run", scheduled_date: "2020-01-01" }];
  const { context, list, calls } = loadWorkoutsContext({ items, agree: true });
  vm.runInNewContext(`${fn}\nglobalThis.__run = loadWorkouts;`, context);
  let handler;
  list.querySelectorAll = () => [{
    dataset: { workoutId: "old" }, textContent: "Delete", disabled: false,
    addEventListener: (_, callback) => { handler = callback; },
  }];
  await context.__run();
  await handler();
  assert.deepEqual(calls.posted[0], { workout_ids: ["old"], include_past: true });
  assert.equal(calls.confirmed.at(-1).key, "confirmDeletePast");
});

const pollDeletionSource = source.match(/async function pollDeletion\(jobId\) \{[\s\S]*?\n\}/)[0];

async function runDeletionPoll(job) {
  const notices = [];
  const paths = [];
  const context = {
    api: async (path) => { paths.push(path); return job; },
    t: (key, params = {}) => `${key}:${JSON.stringify(params)}`,
    errorText: ({ code }) => `error:${code || "default"}`,
    showToast: (message) => notices.push(message),
    window: { setTimeout: (callback) => callback() },
    Promise,
  };
  vm.runInNewContext(`${pollDeletionSource}\nglobalThis.__poll = pollDeletion;`, context);
  await context.__poll("delete/job 1");
  return { notices, paths };
}

test("deletion polling follows the exact accepted job", async () => {
  const { notices, paths } = await runDeletionPoll({
    status: "completed",
    result: { counts: { deleted: 1, already_gone: 0, refused: 0 }, workouts: [] },
  });
  assert.deepEqual(paths, ["/api/v1/plans/delete%2Fjob%201"]);
  assert.equal(notices.at(-1), "workoutDeleted:{}");
});

test("a refused deletion is never announced as deleted", async () => {
  const { notices } = await runDeletionPoll({
    status: "completed",
    result: {
      counts: { deleted: 0, already_gone: 0, refused: 1 },
      workouts: [{ action: "refused", code: "garmin_prior_upload_unresolved" }],
    },
  });
  assert.match(notices.at(-1), /^workoutDeletePartial:/);
  assert.match(notices.at(-1), /error:garmin_prior_upload_unresolved/);
  assert.ok(!notices.some((message) => message.startsWith("workoutDeleted:")));
});

test("an empty completed deletion is reported as an error", async () => {
  const { notices } = await runDeletionPoll({
    status: "completed",
    result: { counts: { deleted: 0, already_gone: 0, refused: 0 }, workouts: [] },
  });
  assert.equal(notices.at(-1), "error:internal_delete_error");
});
