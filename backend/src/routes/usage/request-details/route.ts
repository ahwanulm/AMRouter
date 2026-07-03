
import { getRequestDetails } from "../../../lib/usageDb.js";

/**
 * GET /api/usage/request-details
 * Query parameters: page, pageSize (1-100), provider, model, connectionId, status, startDate, endDate
 */
export async function GET_handler(req, res) {
  try {
    const { searchParams } = new URL('http://localhost' + req.originalUrl);
    
    const page = parseInt(searchParams.get("page")) || 1;
    const pageSize = parseInt(searchParams.get("pageSize")) || 20;
    const provider = searchParams.get("provider");
    const model = searchParams.get("model");
    const connectionId = searchParams.get("connectionId");
    const status = searchParams.get("status");
    const startDate = searchParams.get("startDate");
    const endDate = searchParams.get("endDate");
    
    if (page < 1) {
      return res.json(
        { error: "Page must be >= 1" },
        { status: 400 }
      );
    }
    
    if (pageSize < 1 || pageSize > 100) {
      return res.json(
        { error: "PageSize must be between 1 and 100" },
        { status: 400 }
      );
    }
    
    const filter = {
      page,
      pageSize
    };
    
    if (provider) filter.provider = provider;
    if (model) filter.model = model;
    if (connectionId) filter.connectionId = connectionId;
    if (status) filter.status = status;
    if (startDate) filter.startDate = startDate;
    if (endDate) filter.endDate = endDate;
    
    const result = await getRequestDetails(filter);
    
    return res.json(result);
  } catch (error) {
    console.error("[API] Failed to get request details:", error);
    return res.json(
      { error: "Failed to fetch request details" },
      { status: 500 }
    );
  }
}
