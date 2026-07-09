import http from "node:http";
import { getThreadsPostBySlug, logClick } from "./threadsRepository.service";

function getClientIp(req: http.IncomingMessage): string {
  const forwardedFor = req.headers["x-forwarded-for"];

  if (typeof forwardedFor === "string" && forwardedFor.trim()) {
    return forwardedFor.split(",")[0].trim();
  }

  return req.socket.remoteAddress || "";
}

export function startTrackingServer(): http.Server {
  const port = Number(process.env.PORT || process.env.TRACKING_PORT || 8000);

  const server = http.createServer((req, res) => {
    try {
      const host = req.headers.host || `localhost:${port}`;
      const url = new URL(req.url || "/", `http://${host}`);
      const match = url.pathname.match(/^\/go\/([^/]+)$/);

      if (!match) {
        res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
        res.end(JSON.stringify({ ok: true, service: "pod-bot-tracking" }));
        return;
      }

      const slug = decodeURIComponent(match[1]);
      const post = getThreadsPostBySlug(slug);

      if (!post?.affiliate_url || post.status === "deleted") {
        res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
        res.end("Tracking link not found.");
        return;
      }

      logClick({
        postId: post.id,
        slug,
        referrer: req.headers.referer,
        userAgent: req.headers["user-agent"],
        ip: getClientIp(req),
      });

      res.writeHead(302, {
        Location: post.affiliate_url,
        "Cache-Control": "no-store",
      });
      res.end();
    } catch (error) {
      console.error("Tracking server error:", error instanceof Error ? error.message : error);
      res.writeHead(500, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("Internal server error.");
    }
  });

  server.listen(port, () => {
    console.log(`Tracking server running on http://localhost:${port}`);
  });

  return server;
}
