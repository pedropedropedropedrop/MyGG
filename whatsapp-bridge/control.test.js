const assert = require("node:assert/strict");
const { test } = require("node:test");
const { makeControlServer } = require("./control");

test("bridge reports connection and sends exactly the approved text to the supplied JID", async (t) => {
  let connection = "connecting";
  const sent = [];
  const server = makeControlServer({
    secret: "test-only", status: () => ({ connection, pending_incoming: 2 }),
    send: async (jid, data) => { sent.push([jid, data]); return {key: {id: "receipt1"}}; },
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const url = `http://127.0.0.1:${server.address().port}`;
  const headers = {Authorization: "Bearer test-only", "Content-Type": "application/json"};
  const post = (jid, text) => fetch(url + "/send", { method: "POST", headers, body: JSON.stringify({jid, text}) });
  assert.equal((await fetch(url + "/health")).status, 401);
  assert.equal((await fetch(url + "/send", { method: "POST" })).status, 401);
  assert.deepEqual(await (await fetch(url + "/health", {headers})).json(), {connection, pending_incoming: 2});
  assert.equal((await post("123@lid", "Hello")).status, 503);
  connection = "open";
  assert.equal((await post("status@broadcast", "Hello")).status, 400);
  assert.equal((await post("123@lid", "")).status, 400);
  assert.equal((await post("123@lid", "x".repeat(4097))).status, 400);
  const reply = await post("123-456@g.us", "Hello team");
  assert.equal(reply.status, 200);
  assert.deepEqual(await reply.json(), {id: "receipt1"});
  assert.deepEqual(sent, [["123-456@g.us", {text: "Hello team"}]]);
});
