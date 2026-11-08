const crypto = require("node:crypto");
const http = require("node:http");

const JID = /^(?:[0-9]+(?:-[0-9]+)?@g\.us|[0-9]+@(?:s\.whatsapp\.net|lid))$/;
const MAX_BODY = 32 * 1024;

function makeControlServer({ secret, status, send }) {
  const expected = Buffer.from(`Bearer ${secret}`);
  return http.createServer(async (req, res) => {
    const answer = (code, data) => {
      res.writeHead(code, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      res.end(JSON.stringify(data));
    };
    const supplied = Buffer.from(req.headers.authorization || "");
    if (!secret || expected.length !== supplied.length || !crypto.timingSafeEqual(expected, supplied)) {
      answer(401, { error: "Unauthorized" });
      return;
    }
    if (req.method === "GET" && req.url === "/health") {
      answer(200, status());
      return;
    }
    if (req.method !== "POST" || req.url !== "/send") {
      answer(404, { error: "Unknown endpoint" });
      return;
    }
    let size = 0;
    const chunks = [];
    let payload;
    try {
      for await (const chunk of req) {
        size += chunk.length;
        if (size > MAX_BODY) { answer(413, { error: "Message too large" }); return; }
        chunks.push(chunk);
      }
      payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch {
      answer(400, { error: "Invalid JSON" });
      return;
    }
    const { jid, text } = payload || {};
    if (typeof jid !== "string" || !JID.test(jid) || typeof text !== "string" || !text.trim() || [...text].length > 4096) {
      answer(400, { error: "Invalid recipient or message" });
      return;
    }
    if (status().connection !== "open") { answer(503, { error: "WhatsApp disconnected" }); return; }
    try {
      const sent = await send(jid, { text });
      if (!sent?.key?.id) throw new Error("No receipt");
      answer(200, { id: sent.key.id });
    } catch {
      answer(502, { error: "Delivery unconfirmed" });
    }
  });
}

module.exports = { makeControlServer };
