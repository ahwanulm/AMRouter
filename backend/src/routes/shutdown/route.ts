

export async function POST(req, res) {
  if (process.env.NODE_ENV === "production") {
    return res.status(403).json({ success: false, message: "Not allowed in production" });
  }

  const secret = process.env.SHUTDOWN_SECRET;
  const authorization = headers().get("authorization");

  if (!secret || authorization !== `Bearer ${secret}`) {
    return res.status(401).json({ success: false, message: "Unauthorized" });
  }

  const response = res.json({ success: true, message: "Shutting down..." });

  setTimeout(() => {
    process.exit(0);
  }, 500);

  return response;
}

