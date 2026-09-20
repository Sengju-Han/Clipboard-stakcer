// Microsoft's voice service, near enough to be wrong against.
//
// The real one is unreachable from CI and reverse-engineered besides, so what
// is checked here is the half that is ours: that the request carries the token,
// the Origin a page cannot set, and the two frames in the right shape - and
// that the audio is reassembled out of the binary frames that come back.
//
// The binary frame layout is the one the real service uses: two bytes of
// big-endian header length, then that many bytes of headers ending in a blank
// line, then the audio. Getting this wrong in the fake and wrong in the same
// way in the client would make both agree and neither work, so it is written
// from the format rather than from src/say.js.

import { createServer } from "node:http";
import { WebSocketServer } from "ws";

function binaryFrame(path, contentType, audio) {
  const headers = Buffer.from(
    `X-RequestId:fake\r\nContent-Type:${contentType}\r\nPath:${path}\r\n`, "utf8",
  );
  const length = Buffer.alloc(2);
  length.writeUInt16BE(headers.length, 0);
  return Buffer.concat([length, headers, Buffer.from(audio)]);
}

function textFrame(path, body = "") {
  return `X-RequestId:fake\r\nContent-Type:application/json; charset=utf-8\r\nPath:${path}\r\n\r\n${body}`;
}

/**
 * Start a stand-in for the voice service.
 *
 * `status` refuses the upgrade with that HTTP status instead, which is what a
 * retired client token or a stale Sec-MS-GEC looks like from the outside.
 */
export async function fakeEdge({ audio = ["first-", "second"], status = 0, silent = false } = {}) {
  const seen = { query: null, origin: "", frames: [], connections: 0 };

  const http = createServer((req, res) => { res.writeHead(426); res.end("upgrade required"); });
  const wss = new WebSocketServer({ noServer: true });

  http.on("upgrade", (req, socket, head) => {
    seen.connections += 1;
    seen.query = new URL(req.url, "http://fake").searchParams;
    seen.origin = req.headers.origin || "";
    seen.agent = req.headers["user-agent"] || "";
    if (status) {
      socket.write(`HTTP/1.1 ${status} Refused\r\nConnection: close\r\n\r\n`);
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => {
      ws.on("message", (data, isBinary) => {
        if (isBinary) return;
        const frame = data.toString("utf8");
        seen.frames.push(frame);
        // The second frame is the one carrying the words; everything the
        // service says back follows it.
        if (!/Path:ssml/.test(frame)) return;
        ws.send(textFrame("turn.start", "{}"));
        if (!silent) {
          for (const part of audio) ws.send(binaryFrame("audio", "audio/mpeg", part));
        }
        ws.send(textFrame("turn.end", "{}"));
      });
    });
  });

  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const { port } = http.address();
  return {
    url: `ws://127.0.0.1:${port}/edge/v1`,
    seen,
    async close() {
      wss.close();
      await new Promise((resolve) => http.close(resolve));
    },
  };
}
