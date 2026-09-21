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
