const wpp = require("@wppconnect-team/wppconnect");
const WebSocket = require("ws");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

// Load environment variables
if (fs.existsSync(path.join(__dirname, ".env"))) {
  require("dotenv").config();
}

const OWNER_NUMBER = "919370449266";

const config = {
  sessionName: "picobot",
  autoClose: false,
  maxMessageLength: 8192,
  port: parseInt(process.env.WS_PORT || "3001"),
  token: process.env.BRIDGE_TOKEN || "",
  headless: false, // User needs to see this to scan
  useChrome: false,
};

const logsDir = path.join(__dirname, "logs");
const mediaDir = path.join(__dirname, "media");
if (!fs.existsSync(logsDir)) fs.mkdirSync(logsDir, { recursive: true });
if (!fs.existsSync(mediaDir)) fs.mkdirSync(mediaDir, { recursive: true });

function sanitizePhoneNumber(phone) {
  if (!phone) return "";
  let clean = phone.replace(/\D/g, "");
  if (clean.length === 10) clean = "91" + clean;
  return clean;
}

function isOwner(senderId) {
  if (senderId === "status@broadcast") return false;
  const clean = sanitizePhoneNumber(senderId);
  return clean === OWNER_NUMBER;
}

function log(type, data) {
  const timestamp = new Date().toISOString();
  const logEntry = `[${timestamp}] [${type}] ${typeof data === "string" ? data : JSON.stringify(data)}\n`;
  process.stdout.write(logEntry);
  try {
    const logFile = path.join(
      logsDir,
      `whatsapp-${new Date().toISOString().split("T")[0]}.log`,
    );
    fs.appendFileSync(logFile, logEntry);
  } catch (e) {}
}

const wss = new WebSocket.Server({ port: config.port, host: "127.0.0.1" });
const clients = new Set();

wss.on("connection", (ws) => {
  log("INFO", "WebSocket client connected");
  clients.add(ws);
  ws.on("close", () => clients.delete(ws));
  ws.on("message", (data) => handleIncomingWS(data, ws));
});

function sendToClients(type, data) {
  const message = JSON.stringify({ type, ...data });
  clients.forEach((c) => {
    if (c.readyState === WebSocket.OPEN) c.send(message);
  });
}

let waClient = null;

async function handleIncomingWS(raw, ws) {
  log("WS_IN", `Received: ${raw.toString().substring(0, 200)}`);
  try {
    const msg = JSON.parse(raw.toString());
    log("WS_PARSE", `Parsed msg type: ${msg.type}`);
    if (msg.type === "send") {
      if (!waClient)
        return ws.send(
          JSON.stringify({ type: "error", message: "WA not ready" }),
        );

      const to = sanitizePhoneNumber(msg.to);
      if (to !== OWNER_NUMBER) {
        log(
          "SECURITY",
          `Blocked attempt to send to unauthorized number: ${to}`,
        );
        return ws.send(
          JSON.stringify({
            type: "error",
            message: "Restricted: Only owner can receive messages",
          }),
        );
      }

      let content = msg.text || msg.message || "";

      // Prefix all bot messages for clearer UX
      if (content && !content.startsWith("🤖")) {
        content = "🤖 *Picobot:*\n" + content;
      }

      try {
        const chatId = OWNER_NUMBER + "@c.us";

        // Stop typing indicator before sending
        try {
          if (typeof waClient.stopTyping === "function") {
            await waClient.stopTyping(chatId);
          }
        } catch (e) {}

        if (msg.media) {
          const mPath = path.resolve(msg.media.path || msg.media);
          if (fs.existsSync(mPath))
            await waClient.sendFile(chatId, mPath, "media", content);
        } else {
          await waClient.sendText(chatId, content);
        }
        ws.send(JSON.stringify({ type: "sent", success: true }));
      } catch (e) {
        log("ERROR", `Send failed: ${e.message}`);
      }
    }
  } catch (e) {
    log("ERROR", `WS Parse error: ${e.message}`);
  }
}

wpp
  .create({
    session: config.sessionName,
    autoClose: false,
    useChrome: true,
    puppeteerOptions: {
      headless: false,
      executablePath:
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      userDataDir: path.join(__dirname, "tokens", config.sessionName),
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
      ],
    },
    catchQR: (base64, ascii) => {
      log("QR", "New QR generated. Scan with 9370449266.");
      sendToClients("qr", { qr: base64 });
      // Save QR as base64 image file for easy scanning
      const qrFile = path.join(__dirname, "qrcode.txt");
      fs.writeFileSync(qrFile, base64);
      log("QR", `QR code saved to ${qrFile}`);
      // Also print ASCII version to console
      if (ascii) console.log(ascii);
    },
    statusFind: (status) => {
      log("STATUS", status);
      sendToClients("status", { status });
    },
  })
  .then(async (client) => {
    waClient = client;
    log("INFO", "✅ WhatsApp Connected as Personal Assistant");

    // Keep session alive - send presence update every 30 seconds
    setInterval(async () => {
      if (client && client.isConnected && client.isConnected()) {
        try {
          log("KEEPALIVE", "Session alive");
        } catch (e) {
          log("KEEPALIVE", `Error: ${e.message}`);
        }
      }
    }, 30000);

    client.onMessage(async (message) => {
      const rawFrom = message.from || "unknown";
      const rawTo = message.to || "unknown";
      log(
        "ONMSG",
        `From:${rawFrom} To:${rawTo} Body:${message.body?.substring(0, 30) || "[empty]"}`,
      );
      if (!message?.from) return;
      const sender = message.from.replace(/@c.us$/, "").replace(/@g.us$/, "");
      const to = message.to.replace(/@c.us$/, "").replace(/@g.us$/, "");

      // Accept: messages FROM owner, OR messages TO owner (sent to self)
      const isFromOwner = isOwner(sender);
      const isToOwner = isOwner(to);

      if (!isFromOwner && !isToOwner && sender !== "status@broadcast") {
        log("IGNORED", `Not owner. From:${sender} To:${to}`);
        return;
      }

      // Use the message content (body or caption)
      const content = message.body || message.caption || "";
      if (!content && !message.hasMedia) {
        log("IGNORED", "No content or media");
        return;
      }

      log(
        "MSG",
        `From:${sender} Content:${content.substring(0, 50) || "[media only]"}`,
      );

      let media = [];
      if (message.hasMedia) {
        try {
          const m = await client.downloadMedia(message);
          if (m) {
            const ext =
              message.type === "image"
                ? ".jpg"
                : message.type === "video"
                  ? ".mp4"
                  : ".bin";
            const f = path.join(mediaDir, `${message.id.id}${ext}`);
            fs.writeFileSync(f, Buffer.from(m, "base64"));
            media.push(f);
          }
        } catch (e) {
          log("ERROR", `Media download failed: ${e.message}`);
        }
      }

      sendToClients("message", {
        sender: sender,
        content: message.body || message.caption || "",
        id: message.id.id,
        media: media,
        timestamp: message.timestamp,
      });
    });

    // Also use onAnyMessage to catch all messages (onMessage doesn't work reliably)
    client.onAnyMessage(async (msg) => {
      const rawFrom = msg.from || "unknown";
      const rawTo = msg.to || "unknown";
      const content = msg.body || msg.caption || "";

      log(
        "ONANY",
        `From:${rawFrom} To:${rawTo} Body:${content.substring(0, 30) || "[empty]"} fromMe:${msg.fromMe}`,
      );

      // IMPORTANT: To prevent infinite loops when messaging yourself, we must ignore messages
      // generated by the bot. We identify these by the "🤖 *Picobot:*" prefix.
      if (msg.fromMe && content.startsWith("🤖 *Picobot:*")) {
        log("IGNORED", "Ignoring message generated by bot (matches prefix)");
        return;
      }

      if (!msg?.from) return;
      const sender = msg.from.replace(/@c.us$/, "").replace(/@g.us$/, "");
      const to = msg.to.replace(/@c.us$/, "").replace(/@g.us$/, "");

      // Accept: messages FROM owner, OR messages TO owner (sent to self)
      if (!isOwner(sender) && !isOwner(to) && sender !== "status@broadcast") {
        return;
      }

      if (!content && !msg.hasMedia) return;

      // Start typing indicator to show Picobot is processing
      try {
        if (typeof client.startTyping === "function") {
          const chatId = msg.from; // Always show typing in the chat it came from
          await client.startTyping(chatId);
        }
      } catch (e) {}

      let media = [];
      if (msg.hasMedia) {
        try {
          const m = await client.downloadMedia(msg);
          if (m) {
            const ext =
              msg.type === "image"
                ? ".jpg"
                : msg.type === "video"
                  ? ".mp4"
                  : ".bin";
            const f = path.join(mediaDir, `${msg.id.id}${ext}`);
            fs.writeFileSync(f, Buffer.from(m, "base64"));
            media.push(f);
          }
        } catch (e) {
          log("ERROR", `Media download failed: ${e.message}`);
        }
      }

      sendToClients("message", {
        sender: sender,
        content: content,
        id: msg.id.id,
        media: media,
        timestamp: msg.timestamp,
      });
    });
  })
  .catch((e) => log("ERROR", `Init failed: ${e.message}`));

process.on("SIGINT", () => process.exit(0));
