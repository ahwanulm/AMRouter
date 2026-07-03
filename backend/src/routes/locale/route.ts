
import { LOCALE_COOKIE, normalizeLocale, isSupportedLocale } from "../../i18n/config.js";

export async function POST_handler(req, res) {
  try {
    const { locale } = req.body;
    
    if (!locale || !isSupportedLocale(locale)) {
      return res.json(
        { error: "Invalid locale" },
        { status: 400 }
      );
    }

    const normalized = normalizeLocale(locale);
    const cookieStore = { get: (k) => ({ value: (req).cookies?.[k] }) };
    cookieStore.set(LOCALE_COOKIE, normalized, {
      path: "/",
      maxAge: 60 * 60 * 24 * 365, // 1 year
    });

    return res.json({ success: true, locale: normalized });
  } catch (error) {
    return res.json(
      { error: "Failed to set locale" },
      { status: 500 }
    );
  }
}
