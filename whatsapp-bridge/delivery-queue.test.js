const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { test } = require("node:test");
const { DeliveryQueue, postJson } = require("./delivery-queue");

const message = (id = "test-id") => ({
  whatsapp_message_id: id, chat_jid: "test@s.whatsapp.net",
  sender_jid: "test@s.whatsapp.net", timestamp: "2026-09-28T12:00:00.000Z",
  message_text: "Synthetic queue test",
});

function makeDirectory(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "gg-queue-test-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

test("failed delivery survives restart and successful acknowledgment removes it", async (t) => {
  const directory = makeDirectory(t);
  const first = new DeliveryQueue({ directory, deliver: async () => { throw new Error("offline"); } });
  first.enqueue(message());
  await first.drain();
  assert.equal(first.size, 1);
  assert.equal(fs.readdirSync(directory).length, 1);
  const received = [];
  const restarted = new DeliveryQueue({ directory, deliver: async (data) => received.push(data) });
  await restarted.drain();
  assert.deepEqual(received, [message()]);
  assert.equal(restarted.size, 0);
  assert.deepEqual(fs.readdirSync(directory), []);
});

test("stable IDs deduplicate pending replay without replacing its content", async (t) => {
  const directory = makeDirectory(t);
  const received = [];
  const queue = new DeliveryQueue({ directory, deliver: async (data) => received.push(data) });
  assert.equal(queue.enqueue(message()), true);
  assert.equal(queue.enqueue({ ...message(), message_text: "Changed replay" }), false);
  const restarted = new DeliveryQueue({ directory, deliver: async (data) => received.push(data) });
  assert.equal(restarted.enqueue(message()), false);
  await restarted.drain();
  assert.deepEqual(received, [message()]);
});

test("one failing item does not block other messages and backoff is respected", async (t) => {
  let now = 1000;
  const attempts = [];
  const queue = new DeliveryQueue({
    directory: makeDirectory(t), now: () => now, retryBaseMs: 10, retryMaxMs: 20,
    deliver: async (data) => {
      attempts.push(data.whatsapp_message_id);
      if (data.whatsapp_message_id === "bad") throw new Error("HTTP 400");
    },
  });
  queue.enqueue(message("bad"));
  queue.enqueue(message("good"));
  await queue.drain();
  await queue.drain();
  assert.deepEqual(attempts, ["bad", "good"]);
  now += 10;
  await queue.drain();
  assert.deepEqual(attempts, ["bad", "good", "bad"]);
  assert.equal(queue.size, 1);
});

test("overlapping drains cannot send the same pending item concurrently", async (t) => {
  let release;
  let calls = 0;
  const queue = new DeliveryQueue({ directory: makeDirectory(t), deliver: () => {
    calls += 1;
    return new Promise((resolve) => { release = resolve; });
  } });
  queue.enqueue(message());
  const running = queue.drain();
  await queue.drain();
  assert.equal(calls, 1);
  assert.equal(queue.size, 1);
  release();
  await running;
  assert.equal(queue.size, 0);
});

test("queue limits fail visibly and preserve existing messages", (t) => {
  const directory = makeDirectory(t);
  const queue = new DeliveryQueue({ directory, deliver: async () => {}, maxMessages: 1 });
  queue.enqueue(message());
  assert.throws(() => queue.enqueue(message("overflow")), /capacity/i);
  assert.equal(queue.size, 1);
  assert.equal(fs.readdirSync(directory).length, 1);
  const bytesQueue = new DeliveryQueue({ directory: makeDirectory(t), deliver: async () => {}, maxBytes: 10 });
  assert.throws(() => bytesQueue.enqueue(message()), /capacity/i);
  assert.equal(bytesQueue.size, 0);
});

test("corrupt queue fails startup without overwriting or removing evidence", (t) => {
  const directory = makeDirectory(t);
  const filename = path.join(directory, `${"a".repeat(64)}.json`);
  fs.writeFileSync(filename, '{"incomplete":');
  assert.throws(() => new DeliveryQueue({ directory, deliver: async () => {} }), /invalid pending/i);
  assert.equal(fs.readFileSync(filename, "utf8"), '{"incomplete":');
});

test("a complete temporary record left by a crash is replayed on startup", async (t) => {
  const directory = makeDirectory(t);
  const queue = new DeliveryQueue({ directory, deliver: async () => {} });
  queue.enqueue(message());
  const filename = path.join(directory, fs.readdirSync(directory)[0]);
  fs.renameSync(filename, `${filename}.tmp`);
  const received = [];
  const restarted = new DeliveryQueue({ directory, deliver: async (data) => received.push(data) });
  await restarted.drain();
  assert.deepEqual(received, [message()]);
  assert.deepEqual(fs.readdirSync(directory), []);
});

test("daemon retries pending delivery until the receiver recovers", async (t) => {
  let calls = 0;
  let complete;
  const done = new Promise((resolve) => { complete = resolve; });
  const queue = new DeliveryQueue({
    directory: makeDirectory(t), pollMs: 5, retryBaseMs: 5, retryMaxMs: 10,
    deliver: async () => { calls += 1; if (calls < 3) throw new Error("offline"); complete(); },
  });
  queue.enqueue(message());
  t.after(() => queue.stop());
  queue.start((error) => assert.fail(error.message));
  await Promise.race([done, new Promise((_, reject) => {
    const timer = setTimeout(() => reject(new Error("retry did not run")), 1000);
    timer.unref();
  })]);
  // The successful deliver promise must resolve before drain removes the file.
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls, 3);
  assert.equal(queue.size, 0);
});

async function startTestServer(t, handler) {
  const server = http.createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  return `http://127.0.0.1:${server.address().port}/test`;
}

test("HTTP helper accepts 2xx only and rejects connection loss", async (t) => {
  let mode = "ok";
  const url = await startTestServer(t, (req, res) => {
    assert.equal(req.headers.authorization, "Bearer test-only");
    if (mode === "disconnect") { res.destroy(); return; }
    res.writeHead(mode === "ok" ? 201 : 503);
    res.end();
  });
  await postJson(message(), { url, secret: "test-only", timeoutMs: 1000 });
  mode = "offline";
  await assert.rejects(postJson(message(), { url, secret: "test-only", timeoutMs: 1000 }), /HTTP 503/);
  mode = "disconnect";
  await assert.rejects(postJson(message(), { url, secret: "test-only", timeoutMs: 1000 }));
});

test("HTTP helper has an absolute deadline even while response bytes arrive", async (t) => {
  const url = await startTestServer(t, (_req, res) => {
    res.writeHead(200);
    const timer = setInterval(() => res.write("."), 5);
    res.on("close", () => clearInterval(timer));
  });
  await assert.rejects(postJson(message(), { url, secret: "test-only", timeoutMs: 40 }), /timeout/i);
});
