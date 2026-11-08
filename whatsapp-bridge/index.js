const pino = require("pino");
const { DeliveryQueue, postJson } = require("./delivery-queue");
const path = require("path");

// ── Config ──────────────────────────────────────────────────────────────────

// Load .env from parent directory
require("dotenv").config({ path: path.join(__dirname, "..", ".env") });

const PYTHON_PORT = process.env.WHATSAPP_BRIDGE_PORT || "8787";
const PYTHON_URL = `http://127.0.0.1:${PYTHON_PORT}/whatsapp/incoming`;
const BRIDGE_SECRET = (process.env.WHATSAPP_BRIDGE_SECRET || "").trim();
const AUTH_DIR = path.join(__dirname, "auth_state");

const logger = pino({ level: "info" });
if (!BRIDGE_SECRET) {
  logger.error("Set WHATSAPP_BRIDGE_SECRET in the project .env before pairing WhatsApp");
  process.exit(1);
}

const deliveryQueue = new DeliveryQueue({
  directory: path.join(__dirname, "pending-delivery"),
  deliver: (data) => postJson(data, { url: PYTHON_URL, secret: BRIDGE_SECRET }),
  logger,
});

function stopForDeliveryError(error) {
  logger.fatal({ err: error.message }, "Delivery queue failed; existing pending messages are preserved");
  process.exit(1);
}

// ── Extract text from message ───────────────────────────────────────────────

function extractText(message) {
  if (message.message?.conversation) return { text: message.message.conversation, type: "text" };
  if (message.message?.extendedTextMessage?.text) return { text: message.message.extendedTextMessage.text, type: "text" };
  if (message.message?.imageMessage?.caption) return { text: message.message.imageMessage.caption, type: "image" };
  if (message.message?.videoMessage?.caption) return { text: message.message.videoMessage.caption, type: "video" };
  if (message.message?.documentMessage) return { text: message.message.documentMessage.fileName || "(document)", type: "document" };
  if (message.message?.imageMessage) return { text: null, type: "image" };
  if (message.message?.videoMessage) return { text: null, type: "video" };
  if (message.message?.audioMessage) return { text: null, type: "audio" };
  if (message.message?.stickerMessage) return { text: null, type: "sticker" };
  return { text: null, type: "unknown" };
}

// ── Main ────────────────────────────────────────────────────────────────────

async function startBridge() {
  const { makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestWaWebVersion } = await import("baileys");
  const { version } = await fetchLatestWaWebVersion({});
  logger.info({ version }, "Using WA Web version");

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const groupNameCache = {};

  const sock = makeWASocket({
    auth: state,
    version,
    logger: pino({ level: "silent" }), // quiet Baileys internal logs
    syncFullHistory: false,
  });

  // Save credentials on update
  sock.ev.on("creds.update", saveCreds);

  // Connection status + QR code handling
  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      const QRCode = require("qrcode");
      const qrPath = path.join(__dirname, "qr.png");
      QRCode.toFile(qrPath, qr, { scale: 4, margin: 2 }, (err) => {
        if (err) {
          logger.error({ err: err.message }, "Failed to generate QR image");
        } else {
          logger.info({ path: qrPath }, "Open this QR image locally and scan it to pair WhatsApp");
        }
      });
    }

    if (connection === "close") {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

      if (shouldReconnect) {
        logger.info("Connection closed, reconnecting in 3s...");
        setTimeout(() => startBridge().catch(err => {
          logger.error({ err: err.message }, "Reconnect failed, retrying in 10s...");
          setTimeout(() => startBridge().catch(err2 => {
            logger.error({ err: err2.message }, "Second reconnect failed — exiting");
            process.exit(1);
          }), 10000);
        }), 3000);
      } else {
        logger.error("Logged out from WhatsApp. Please delete auth_state/ and re-scan QR.");
      }
    } else if (connection === "open") {
      logger.info("Connected to WhatsApp");
    }
  });

  // Listen for new messages
  sock.ev.on("messages.upsert", async ({ type, messages }) => {
    if (type !== "notify") return; // skip offline sync

    for (const msg of messages) {
      const { text, type: msgType } = extractText(msg);

      // Skip messages with no text content (media without caption) for v1
      if (!text && msgType !== "text") {
        logger.debug({ type: msgType }, "Skipping non-text message");
        continue;
      }

      const chatJid = msg.key.remoteJid || "";
      const isGroup = chatJid.endsWith("@g.us");
      const senderJid = isGroup ? (msg.key.participant || chatJid) : chatJid;

      // Resolve group name (pushName is the SENDER's name, not the group name)
      let chatName = null;
      if (isGroup) {
        if (!groupNameCache[chatJid]) {
          try {
            const meta = await Promise.race([
              sock.groupMetadata(chatJid),
              new Promise((_, reject) => setTimeout(() => reject(new Error("groupMetadata timeout")), 5000)),
            ]);
            groupNameCache[chatJid] = meta.subject;
          } catch { groupNameCache[chatJid] = chatJid; }
        }
        chatName = groupNameCache[chatJid];
      } else {
        chatName = msg.pushName || null;
      }

      const data = {
        whatsapp_message_id: msg.key.id,
        chat_jid: chatJid,
        chat_name: chatName,
        sender_jid: senderJid,
        sender_name: msg.pushName || null,
        message_text: text,
        message_type: msgType,
        is_from_me: msg.key.fromMe || false,
        timestamp: new Date(Number(msg.messageTimestamp || 0) * 1000).toISOString(),
      };

      logger.debug({ type: msgType }, "New message");

      try {
        deliveryQueue.enqueue(data);
      } catch (error) {
        stopForDeliveryError(error);
      }
    }
  });
}

// ── Startup ─────────────────────────────────────────────────────────────────

logger.info("Starting GG WhatsApp Bridge...");
logger.info({ pythonUrl: PYTHON_URL }, "Will forward messages to Python");
deliveryQueue.start(stopForDeliveryError);
startBridge().catch(err => {
  logger.error({ err: err.message }, "Initial startup failed");
  process.exit(1);
});

// Graceful shutdown
process.on("SIGINT", () => {
  logger.info("Shutting down...");
  deliveryQueue.stop();
  process.exit(0);
});
process.on("SIGTERM", () => {
  logger.info("Shutting down...");
  deliveryQueue.stop();
  process.exit(0);
});
