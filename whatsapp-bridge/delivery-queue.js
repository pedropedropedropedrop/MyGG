const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

// This Node process cannot import Python constants; keep its bounded limits here.
const DEFAULTS = Object.freeze({
  maxMessages: 10000, maxBytes: 64 * 1024 * 1024, maxMessageBytes: 64 * 1024,
  retryBaseMs: 1000, retryMaxMs: 60000, pollMs: 1000, timeoutMs: 10000,
});
const MAX_RETRY_EXPONENT = 16;
const PENDING_FILENAME = /^[a-f0-9]{64}\.json(?:\.tmp)?$/;

function getKey(data) {
  assert(data && typeof data.whatsapp_message_id === "string" && data.whatsapp_message_id,
    "A stable WhatsApp message ID is required");
  return crypto.createHash("sha256").update(data.whatsapp_message_id).digest("hex");
}

function validateRecord(record, maxMessageBytes) {
  assert(record && Number.isFinite(record.createdAt), "Invalid queue timestamp");
  const data = record.data;
  const key = getKey(data);
  for (const field of ["chat_jid", "sender_jid", "timestamp"]) {
    assert(typeof data[field] === "string" && data[field], `Missing incoming ${field}`);
  }
  assert(Buffer.byteLength(JSON.stringify(data)) <= maxMessageBytes, "Incoming message exceeds HTTP limit");
  return key;
}

class DeliveryQueue {
  constructor(options) {
    assert(options && typeof options.directory === "string" && typeof options.deliver === "function");
    this.options = { ...DEFAULTS, ...options };
    for (const name of Object.keys(DEFAULTS)) {
      assert(Number.isSafeInteger(this.options[name]) && this.options[name] > 0, `Invalid ${name}`);
    }
    this.directory = options.directory;
    this.deliver = options.deliver;
    this.logger = options.logger || console;
    this.now = options.now || Date.now;
    // One event loop owns entries; the running guard serializes asynchronous drains.
    this.entries = new Map();
    this.bytes = 0;
    this.running = false;
    this.timer = null;
    fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 });
    this.load();
  }

  get size() { return this.entries.size; }

  load() {
    assert(this.entries.size === 0, "Queue can only be loaded once");
    const files = fs.readdirSync(this.directory, { withFileTypes: true });
    assert(files.length <= this.options.maxMessages, "Pending queue exceeds capacity");
    for (const file of files) {
      try {
        assert(file.isFile() && PENDING_FILENAME.test(file.name), "Unexpected queue file");
        const filename = path.join(this.directory, file.name);
        const bytes = fs.statSync(filename).size;
        assert(this.bytes + bytes <= this.options.maxBytes, "Pending queue exceeds capacity");
        const record = JSON.parse(fs.readFileSync(filename, "utf8"));
        const key = validateRecord(record, this.options.maxMessageBytes);
        assert(file.name.startsWith(`${key}.json`) && !this.entries.has(key), "Queue ID mismatch or duplicate");
        this.entries.set(key, { ...record, filename, bytes, attempts: 0, nextAt: 0 });
        this.bytes += bytes;
      } catch (error) {
        // Leave every file intact for review, including incomplete temporary writes.
        throw new Error(`Invalid pending delivery file ${file.name}; preserved for review (${error.code || error.name})`);
      }
    }
    this.entries = new Map([...this.entries].sort((a, b) => a[1].createdAt - b[1].createdAt));
  }

  enqueue(data) {
    const key = getKey(data);
    if (this.entries.has(key)) return false;
    const record = { createdAt: this.now(), data };
    validateRecord(record, this.options.maxMessageBytes);
    const body = JSON.stringify(record);
    const bytes = Buffer.byteLength(body);
    assert(this.entries.size < this.options.maxMessages && this.bytes + bytes <= this.options.maxBytes,
      "Pending delivery queue reached capacity; existing messages preserved");
    const filename = path.join(this.directory, `${key}.json`);
    const temporary = `${filename}.tmp`;
    const fd = fs.openSync(temporary, "wx", 0o600);
    try {
      fs.writeFileSync(fd, body);
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    fs.renameSync(temporary, filename);
    this.entries.set(key, { ...JSON.parse(body), filename, bytes, attempts: 0, nextAt: 0 });
    this.bytes += bytes;
    return true;
  }

  async drain() {
    assert(typeof this.deliver === "function");
    if (this.running) return;
    this.running = true;
    try {
      for (const [key, entry] of [...this.entries]) {
        if (entry.nextAt > this.now()) continue;
        try {
          await this.deliver(entry.data);
        } catch (error) {
          const exponent = Math.min(entry.attempts, MAX_RETRY_EXPONENT);
          entry.attempts = exponent + 1;
          entry.nextAt = this.now() + Math.min(this.options.retryMaxMs, this.options.retryBaseMs * 2 ** exponent);
          this.logger.warn({ err: error.message, pending: this.size }, "Incoming delivery failed; kept for retry");
          continue;
        }
        // A crash before deletion only causes a replay; GG deduplicates stable IDs.
        fs.unlinkSync(entry.filename);
        this.entries.delete(key);
        this.bytes -= entry.bytes;
      }
    } finally {
      this.running = false;
    }
  }

  start(onFatal) {
    assert(typeof onFatal === "function", "A visible failure handler is required");
    if (this.timer) return;
    const run = () => this.drain().catch((error) => { this.stop(); onFatal(error); });
    this.timer = setInterval(run, this.options.pollMs);
    run();
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
  }
}

function postJson(data, options) {
  assert(options && typeof options.url === "string");
  const body = JSON.stringify(data);
  const headers = { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) };
  assert(typeof options.secret === "string" && options.secret.trim(), "Missing bridge secret");
  headers.Authorization = `Bearer ${options.secret}`;
  return new Promise((resolve, reject) => {
    let timer;
    let settled = false;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) { req.destroy(); reject(error); } else resolve();
    };
    const req = http.request(options.url, { method: "POST", headers }, (res) => {
      res.once("error", finish);
      res.once("aborted", () => finish(new Error("Incoming delivery response interrupted")));
      res.once("end", () => finish(res.statusCode >= 200 && res.statusCode < 300
        ? null : new Error(`HTTP ${res.statusCode}`)));
      // Only the status is needed; do not buffer or log message/response bodies.
      res.resume();
    });
    req.once("error", finish);
    timer = setTimeout(() => finish(new Error("Incoming delivery HTTP timeout")), options.timeoutMs || DEFAULTS.timeoutMs);
    req.end(body);
  });
}

module.exports = { DeliveryQueue, postJson };
