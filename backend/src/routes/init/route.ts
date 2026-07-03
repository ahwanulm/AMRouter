// This API route is called automatically to initialize app
export async function GET(req, res) {
  return new Response("Initialized", { status: 200 });
}
