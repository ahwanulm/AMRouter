import json
import logging
import time
import requests
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import camoufox.ip
    _orig_proxy_init = camoufox.ip.Proxy.__init__
    def _patched_proxy_init(self, server, username=None, password=None, **kwargs):
        _orig_proxy_init(self, server, username=username, password=password)
    camoufox.ip.Proxy.__init__ = _patched_proxy_init
except Exception:
    pass

logger = logging.getLogger(__name__)

# Cache: account_id -> (token_str, expires_at)
_TOKEN_CACHE: Dict[int, Tuple[str, float]] = {}

class WeavyService:
    def __init__(self, store: Any, profiles_root: Path) -> None:
        self.store = store
        self.profiles_root = Path(profiles_root)
        self._current_profile_dir: Optional[str] = None  # cached after get_auth_token for browser CF bypass

    def get_model_credits_cost(self, model_type: str, model: str) -> float:
        model_type = (model_type or "").lower()
        model = (model or "").lower().replace("-", "_")
        
        if model_type == "image":
            costs = {
                "flux_pro": 25.0,
                "imagen": 15.0,
                "imagen_4": 6.0,
                "gpt_image_2": 25.0,
                "ideogram_4": 25.0,
                "gemini_nano": 5.0,
                "nano_banana_2": 5.0,
                "nano_banana_pro": 10.0,
                "rodin_3d": 50.0,
                "reve": 4.0,
                "higgsfield_image": 21.0,
                "gpt_image_1": 8.0,
                "imagen_3": 6.0,
                "imagen_3_fast": 3.0,
                "flux_2_pro": 5.0,
                "flux_2_flex": 14.0,
                "flux_2_dev_lora": 4.0,
                "flux_1_1_ultra": 7.0,
                "flux_pro_1_1": 5.0,
                "flux_fast": 0.4,
                "flux_dev_lora": 4.0,
                "recraft_v3": 5.0,
                "mystic": 12.0,
                "ideogram_v3": 4.0,
                "ideogram_v3_character": 15.0,
                "stable_diffusion_3_5": 8.0,
                "minimax_image_01": 1.0,
                "bria": 6.0,
                "dalle_3": 5.0,
                "luma_photon": 2.0,
                "nvidia_sana": 0.2,
                "nvidia_consistory": 5.0
            }
            return costs.get(model, 25.0)
        elif model_type == "video":
            costs = {
                "runway": 140.0,
                "kling": 140.0,
                "kling_turbo": 60.0,
                "kling_custom": 200.0,
                "seedance": 74.0,
                "seedance_fast": 81.0,
                "seedance_custom": 257.0,
                "wan": 110.0,
                "wan_2_7": 86.0,
                "veo": 132.0,
                "veo_fast": 50.0,
                "ltx_2": 70.0,
                "pixverse": 44.0,
                "seedance_2_0": 145.0,
                "seedance_2_0_reference": 145.0,
                "kling_3": 60.0,
                "grok_imagine_video": 36.0,
                "sora_2": 96.0,
                "wan_2_2": 66.0,
                "moonvalley": 165.0,
                "veo_3_1_text_to_image": 120.0,
                "veo_3_1_image_to_video": 120.0,
                "veo_3_text_to_image": 120.0,
                "veo_3_image_to_video": 120.0,
                "veo_2": 300.0,
                "seedance_v1_0": 18.0,
                "runway_gen_4_turbo": 30.0,
                "runway_gen_4": 70.0,
                "runway_gen_3": 30.0,
                "kling_1_6": 18.0,
                "kling_o1_first_last_frame": 56.0,
                "kling_2_5_first_last_frame": 35.0,
                "kling_2_1_first_last_frame": 45.0,
                "luma_ray_2": 108.0,
                "luma_ray_2_flash": 36.0,
                "minimax_video_director": 60.0,
                "minimax_video_01": 60.0,
                "hunyuan": 60.0,
                "skyreels": 36.0,
                "wan_video": 24.0,
                "higgsfield_video": 14.0
            }
            return costs.get(model, 140.0) + 10.0
        return 150.0

    def get_account_balance(self, account_id: int) -> float:
        """Fetch fresh credits balance from the Weavy API and update the database."""
        token = self.get_auth_token(account_id)
        url = "https://api.weavy.ai/api/v1/users"
        headers = {
            "x-weavy-auth-provider": "firebase",
            "x-app-version": "4.1.489",
            "authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        resp = self._request("GET", url, headers=headers, timeout=15)
        if resp.status_code == 401:
            logger.info("[weavy_service] Token expired for account_id=%d. Refreshing...", account_id)
            token = self.get_auth_token(account_id, force_refresh=True)
            headers["authorization"] = f"Bearer {token}"
            resp = self._request("GET", url, headers=headers, timeout=15)
            
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch user profile for account {account_id}: {resp.text}")
        data = resp.json()
        credits = data.get("credits", 0.0)
        try:
            credits = float(credits)
        except Exception:
            credits = 0.0
        
        self.store.update_weavy_account_credits(account_id, credits)
        return credits

    def _get_weavy_proxy(self) -> Optional[Dict[str, str]]:
        """Fetch proxy settings (Camoufox/Playwright format) — only from explicit weavy_proxy_enabled setting.

        NOTE: codebuddy_proxy_pool is NOT used here to avoid routing all Python
        HTTP requests (including Camoufox browser) through the pool proxy on local
        dev machines that don't need it. Pool proxy is only applied in
        duplicate_recipe() when Cloudflare blocks the plain request (403/503).
        """
        if (self.store.get_setting("weavy_proxy_enabled", "0") or "0") != "1":
            return None
        raw = (self.store.get_setting("weavy_proxy_server", "") or "").strip()
        if not raw:
            return None
        user = (self.store.get_setting("weavy_proxy_username", "") or "").strip()
        pwd = (self.store.get_setting("weavy_proxy_password", "") or "").strip()

        server = raw
        if "://" in server:
            scheme, rest = server.split("://", 1)
        else:
            scheme, rest = "http", server
        if "@" in rest:
            cred, host = rest.rsplit("@", 1)
            if ":" in cred:
                inline_user, inline_pwd = cred.split(":", 1)
                user = inline_user or user
                pwd = inline_pwd or pwd
            else:
                user = cred or user
            server = f"{scheme}://{host}"

        proxy_dict = {"server": server}
        if user:
            proxy_dict["username"] = user
        if pwd:
            proxy_dict["password"] = pwd
        proxy_dict["bypass"] = ".arkoselabs.com, .figma.com, client-api.arkoselabs.com, figma.com"
        return proxy_dict

    def _get_pool_proxies(self) -> Optional[Dict[str, str]]:
        """Return a random proxy from codebuddy_proxy_pool in requests format.

        Used ONLY for Cloudflare bypass in duplicate_recipe() — not for general
        requests or Camoufox, to avoid breaking local dev that doesn't need a proxy.
        """
        pool_raw = (self.store.get_setting("codebuddy_proxy_pool", "") or "").strip()
        if not pool_raw:
            return None
        try:
            import json as _json, random as _random
            pool = _json.loads(pool_raw)
            if not pool:
                return None
            proxy_url = _random.choice(pool)
            logger.info("[weavy_service] CF bypass: using pool proxy %s", proxy_url.split("@")[-1])
            # Build requests-compatible proxy dict
            if "://" in proxy_url:
                scheme, rest = proxy_url.split("://", 1)
            else:
                scheme, rest = "http", proxy_url
            user, pwd = "", ""
            if "@" in rest:
                cred, host = rest.rsplit("@", 1)
                if ":" in cred:
                    user, pwd = cred.split(":", 1)
                rest = host
            if user and pwd:
                proxy_str = f"{scheme}://{user}:{pwd}@{rest}"
            else:
                proxy_str = f"{scheme}://{rest}"
            return {"http": proxy_str, "https": proxy_str}
        except Exception as e:
            logger.warning("[weavy_service] Failed to parse codebuddy_proxy_pool: %s", e)
            return None


    def _get_requests_proxies(self) -> Optional[Dict[str, str]]:
        """Fetch proxy settings and build a dictionary compatible with requests."""
        proxy = self._get_weavy_proxy()
        if not proxy:
            return None
        server = proxy["server"]
        user = proxy.get("username")
        pwd = proxy.get("password")
        if user and pwd:
            if "://" in server:
                scheme, host = server.split("://", 1)
                proxy_str = f"{scheme}://{user}:{pwd}@{host}"
            else:
                proxy_str = f"http://{user}:{pwd}@{server}"
        else:
            proxy_str = server
        return {
            "http": proxy_str,
            "https": proxy_str
        }

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Wrapper around requests.request that handles proxy, retries, and Cloudflare bypass.

        On receiving a Cloudflare challenge/block (403/503 or HTML body), automatically
        escalates to cloudscraper (JS challenge solver) then curl_cffi (TLS fingerprint)
        with the pool proxy. This covers ALL api.weavy.ai calls on VPS without needing
        to patch each call site individually.
        """
        proxies = self._get_requests_proxies()
        if proxies and "proxies" not in kwargs:
            kwargs["proxies"] = proxies

        # Set default timeout
        if "timeout" not in kwargs:
            kwargs["timeout"] = 15

        headers = kwargs.setdefault("headers", {})
        if "User-Agent" not in headers and "user-agent" not in headers:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

        max_retries = 3
        last_exc = None
        for attempt in range(max_retries):
            try:
                resp = requests.request(method, url, **kwargs)
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                logger.warning("[weavy_service] Connection/Timeout error on %s (attempt %d/%d): %s", url, attempt + 1, max_retries, e)
                last_exc = e
                time.sleep(1.5 * (attempt + 1))
        else:
            raise last_exc

        # --- Cloudflare bypass ---
        # Detect CF challenge: 403/503 status OR HTML response body (JS challenge page)
        is_cf_block = resp.status_code in (403, 503)
        is_cf_html = (not is_cf_block) and resp.text[:100].lstrip().startswith("<!DOCTYPE")
        if is_cf_block or is_cf_html:
            logger.info("[weavy_service] CF challenge detected on %s %s (status=%d). Escalating...", method, url, resp.status_code)
            cf_proxies = self._get_pool_proxies() or proxies

            # Tier 1: cloudscraper (JS challenge solver)
            try:
                import cloudscraper
                scraper = cloudscraper.create_scraper(
                    browser={"browser": "chrome", "platform": "windows", "mobile": False}
                )
                if cf_proxies:
                    scraper.proxies.update(cf_proxies)
                # Build request kwargs for cloudscraper (no 'proxies' kwarg — set via scraper.proxies)
                cs_kwargs = {k: v for k, v in kwargs.items() if k != "proxies"}
                resp = scraper.request(method, url, **cs_kwargs)
                logger.info("[weavy_service] cloudscraper response: %d", resp.status_code)
            except Exception as e:
                logger.warning("[weavy_service] cloudscraper failed: %s", e)

            # Tier 2: curl_cffi (TLS fingerprint, Chrome impersonation)
            if resp.status_code in (403, 503) or resp.text[:100].lstrip().startswith("<!DOCTYPE"):
                try:
                    from curl_cffi import requests as cffi_requests
                    cffi_kwargs = {k: v for k, v in kwargs.items() if k not in ("proxies",)}
                    resp = cffi_requests.request(
                        method, url,
                        proxies=cf_proxies or {},
                        impersonate="chrome131",
                        **cffi_kwargs,
                    )
                    logger.info("[weavy_service] curl_cffi response: %d", resp.status_code)
                except Exception as e:
                    logger.warning("[weavy_service] curl_cffi failed: %s", e)

        return resp

    def get_auth_token(self, account_id: int, force_refresh: bool = False) -> str:
        """Get a cached Firebase ID token or launch a browser to capture a new one."""
        now = time.time()
        
        # Check cache
        if not force_refresh and account_id in _TOKEN_CACHE:
            tok, expires_at = _TOKEN_CACHE[account_id]
            if now < expires_at:
                logger.info("[weavy_service] Using cached token for account_id=%d", account_id)
                return tok

        # Fetch account from db to get profile dir
        acc = self.store.get_weavy_account(account_id)
        if not acc:
            raise ValueError(f"Weavy account ID {account_id} not found in database")
        
        email = acc["email"]
        profile_dir = self.profiles_root / email.replace("@", "_at_")
        if not profile_dir.exists():
            # Fallback to default naming or profile_dir in db
            db_profile_dir = acc.get("profile_dir")
            if db_profile_dir:
                profile_dir = Path(db_profile_dir)
        
        logger.info("[weavy_service] Captured profile dir for token extraction: %s", profile_dir)

        # Import camoufox and default addons dynamically
        try:
            from camoufox.sync_api import Camoufox
            from camoufox.addons import DefaultAddons
        except Exception as exc:
            raise RuntimeError(
                "Camoufox not installed. Run: uv sync && uv run python -m camoufox fetch"
            ) from exc

        token = None
        logger.info("[weavy_service] Launching Camoufox to capture fresh Firebase token for %s...", email)
        
        launch_kwargs = dict(
            headless=True,
            persistent_context=True, no_viewport=True,
            user_data_dir=str(profile_dir.resolve()),
            humanize=True,
            geoip=True,
            locale="en-US",
            os=("windows", "macos", "linux"),
            exclude_addons=[DefaultAddons.UBO],
            firefox_user_prefs={
                "network.trr.mode": 5,
            }
        )
        proxy = self._get_weavy_proxy()
        if proxy:
            launch_kwargs["proxy"] = proxy

        logger.info("[weavy_service] launch_kwargs: %s", launch_kwargs)
        with Camoufox(**launch_kwargs) as browser:
            context = getattr(browser, "context", None) or browser
            page = context.new_page()

            def on_request(request):
                nonlocal token
                url = request.url
                if "api.weavy.ai" in url:
                    try:
                        headers = request.all_headers()
                        auth = headers.get("authorization")
                        if auth and auth.startswith("Bearer "):
                            token = auth.split(" ", 1)[1]
                            logger.info("[weavy_service] Captured Firebase JWT auth token!")
                    except Exception:
                        pass

            page.on("request", on_request)
            try:
                page.goto("https://app.weavy.ai/", wait_until="commit", timeout=20000)
            except Exception as e:
                logger.warning("[weavy_service] page.goto app.weavy.ai warning/timeout: %s", e)
            
            # Close dialogs or overlays
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

            # Wait up to 35 seconds for token capture
            deadline = time.time() + 35.0
            while time.time() < deadline:
                if token:
                    break
                time.sleep(0.5)

        if not token:
            raise RuntimeError(f"Failed to capture Weavy Firebase ID Token for {email} (Timeout)")

        # Cache the token (valid for 50 minutes to be safe)
        _TOKEN_CACHE[account_id] = (token, now + 50 * 60)
        # Cache profile_dir so execute_flow can use it for CF browser fallback
        self._current_profile_dir = str(profile_dir)
        return token

    def duplicate_recipe(self, token: str, template_id: str) -> str:
        """Clone a public template recipe to the user's workspace.

        CF bypass: _request() catches 403/503 automatically. However, CF on some VPS
        IPs silently strips/modifies the Authorization header instead of blocking — the
        request reaches Weavy but without valid auth, and Weavy returns 404/1003
        ("Entity not found") instead of 401. We detect that case here and retry
        through curl_cffi (TLS fingerprint) with pool proxy before giving up.
        """
        url = f"https://api.weavy.ai/api/v1/recipes/{template_id}/duplicate"
        headers = {
            "x-weavy-auth-provider": "firebase",
            "x-app-version": "4.1.489",
            "authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        logger.info("[weavy_service] POST duplicate_recipe %s", url)
        resp = self._request("POST", url, headers=headers, timeout=25)
        logger.info("[weavy_service] duplicate_recipe response: %d", resp.status_code)

        # Detect CF header-stripping: 404 + internalErrorCode:1003 means Weavy got
        # the request but without valid auth (auth header was stripped by CF WAF).
        # Retry with curl_cffi TLS fingerprint + pool proxy to bypass CF inspection.
        if resp.status_code == 404:
            try:
                body = resp.json()
            except Exception:
                body = {}
            if body.get("internalErrorCode") == 1003:
                logger.warning(
                    "[weavy_service] duplicate_recipe got 404/1003 — possible CF header "
                    "stripping on VPS. Retrying via curl_cffi..."
                )
                cf_proxies = self._get_pool_proxies()
                try:
                    from curl_cffi import requests as cffi_req
                    cffi_kwargs: dict = {
                        "headers": headers,
                        "timeout": 25,
                        "impersonate": "chrome131",
                    }
                    if cf_proxies:
                        cffi_kwargs["proxies"] = cf_proxies
                    resp = cffi_req.request("POST", url, **cffi_kwargs)
                    logger.info(
                        "[weavy_service] curl_cffi duplicate_recipe retry: %d", resp.status_code
                    )
                except Exception as e:
                    logger.warning("[weavy_service] curl_cffi retry failed: %s", e)

        if resp.status_code != 201:
            raise RuntimeError(f"Duplicate recipe failed (Status {resp.status_code}): {resp.text}")

        data = resp.json()
        recipe_id = data.get("id")
        if not recipe_id:
            raise RuntimeError("Duplicate recipe response did not return a recipe ID")

        logger.info("[weavy_service] Duplicated recipe successfully. New recipe ID: %s", recipe_id)
        return recipe_id

    def _upload_image_to_weavy(self, image_url: str, token: str) -> dict:
        """Download image from external URL and upload to Weavy CDN.
        
        Returns the Weavy file object with id, url, publicId, width, height,
        thumbnailUrl, viewUrl, originalUrl — ready to inject into node params.
        """
        from curl_cffi import requests as cffi_req
        logger.info("[weavy_service] Downloading image for Weavy upload: %s", image_url)
        try:
            img_resp = cffi_req.get(image_url, allow_redirects=True, timeout=30)
            img_resp.raise_for_status()
            img_data = img_resp.content
            content_type = img_resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        except Exception as e:
            raise RuntimeError(f"Failed to download image for Weavy upload: {e}")

        ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
        ext = ext_map.get(content_type, "jpg")

        upload_url = "https://api.weavy.ai/api/v1/assets/upload"
        upload_headers = {
            "x-weavy-auth-provider": "firebase",
            "x-app-version": "4.1.489",
            "authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        files = {"file": (f"image.{ext}", img_data, content_type)}
        logger.info("[weavy_service] Uploading %d bytes to Weavy CDN...", len(img_data))
        r = self._request("POST", upload_url, headers=upload_headers, files=files, timeout=60)
        if r.status_code == 201:
            result = r.json()
            logger.info("[weavy_service] Weavy upload success: %s", result.get("url", ""))
            return result
        raise RuntimeError(f"Weavy image upload failed ({r.status_code}): {r.text[:300]}")

    def _execute_via_browser(self, exec_url: str, req_headers: dict, exec_payload: dict) -> dict:
        """Bypass CF challenge on POST /execute by making the request from inside Camoufox browser.
        
        The browser uses the same persistent profile as get_auth_token, so it already has
        CF clearance cookies — allowing it to POST /execute without triggering a JS challenge.
        """
        profile_dir = self._current_profile_dir
        if not profile_dir:
            logger.warning("[weavy_service] _execute_via_browser: no cached profile_dir, cannot use browser fallback")
            return {"status": 0, "text": "No profile_dir cached"}

        from camoufox.sync_api import Camoufox
        from camoufox.addons import DefaultAddons

        logger.info("[weavy_service] CF bypass: launching Camoufox to POST /execute via browser fetch")
        launch_kwargs = dict(
            headless=True,
            persistent_context=True, no_viewport=True,
            user_data_dir=profile_dir,
            humanize=True,
            geoip=True,
            locale="en-US",
            os=("windows", "macos", "linux"),
            exclude_addons=[DefaultAddons.UBO],
            firefox_user_prefs={"network.trr.mode": 5}
        )
        proxy = self._get_weavy_proxy()
        if proxy:
            launch_kwargs["proxy"] = proxy

        result = {"status": 0, "text": ""}
        try:
            with Camoufox(**launch_kwargs) as browser:
                context = getattr(browser, "context", None) or browser
                page = context.new_page()
                # Visit app.weavy.ai to establish CF clearance in this session
                try:
                    page.goto("https://app.weavy.ai/", wait_until="commit", timeout=20000)
                except Exception as nav_err:
                    logger.warning("[weavy_service] browser navigate warning: %s", nav_err)

                # Use browser's fetch to POST /execute — bypasses CF since it's a real browser
                js_result = page.evaluate("""
                    async ([url, hdrs, payload]) => {
                        try {
                            const resp = await fetch(url, {
                                method: 'POST',
                                headers: hdrs,
                                body: JSON.stringify(payload)
                            });
                            const text = await resp.text();
                            return {status: resp.status, text: text};
                        } catch(e) {
                            return {status: 0, text: String(e)};
                        }
                    }
                """, [exec_url, req_headers, exec_payload])
                result["status"] = js_result.get("status", 0)
                result["text"] = js_result.get("text", "")
                logger.info("[weavy_service] Browser execute response status: %d", result["status"])
        except Exception as e:
            logger.error("[weavy_service] Browser execute failed: %s", e)
            result["text"] = str(e)
        return result




    def execute_flow(
        self,
        token: str,
        recipe_id: str,
        prompt: str,
        aspect_ratio: str,
        model_type: str,
        model: Optional[str] = None,
        duration: Optional[int] = None,
        image_url: Optional[str] = None,
        end_image_url: Optional[str] = None,
        video_url: Optional[str] = None,
        negative_prompt: Optional[str] = None
    ) -> str:
        """Configure nodes inside a duplicated recipe, save it, and trigger execute."""
        headers = {
            "x-weavy-auth-provider": "firebase",
            "x-app-version": "4.1.489",
            "authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        # 1. Fetch current flow structure
        get_url = f"https://api.weavy.ai/api/v1/recipes/{recipe_id}"
        logger.info("[weavy_service] GET %s", get_url)
        get_resp = self._request("GET", get_url, headers=headers, timeout=15)
        if get_resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch duplicated recipe structure: {get_resp.text}")
        
        recipe_data = get_resp.json()
        nodes = recipe_data.get("nodes", [])
        edges = recipe_data.get("edges", [])

        # 2. Modify nodes based on generation type (Image or Video)
        # Pre-initialize so topaz early-return can reference them after the if/else
        selected_model = ""
        _topaz_weavy_file = None

        if model_type.lower() == "video":

            prompt_node_ids = {"cc21a21f-83d2-4436-b4dd-55dd28756fe1", "ce243ec3-3c0a-47e7-bdee-f65d32ab7dd2"}
            
            selected_model = (model or "runway").lower().replace("-", "_")
            
            # Map duration for predefined video models
            mapped_duration = None
            if duration is not None:
                if selected_model in ("ltx_2", "ltx_2_video"):
                    options = [6, 8, 10]
                    mapped_duration = min(options, key=lambda x: abs(x - duration))
                elif selected_model in ("runway", "runway_gen_4_5"):
                    options = [5, 8, 10]
                    mapped_duration = min(options, key=lambda x: abs(x - duration))
                elif selected_model in ("kling", "kling_3"):
                    mapped_duration = max(3, min(15, duration))
                elif selected_model in ("seedance", "seedance_v1_5_pro"):
                    options = [4, 5, 6, 7, 8, 9, 10, 11, 12]
                    closest = min(options, key=lambda x: abs(x - duration))
                    mapped_duration = str(closest)
                elif selected_model in ("veo", "veo_fast", "veo_3_1_image_to_video"):
                    mapped_duration = "8s"
                elif selected_model in ("grok_imagine_video",):
                    mapped_duration = max(1, min(15, duration))
            
            PREDEFINED_VIDEO_NODES = {
                "runway": "36f3306e-ec29-434e-924f-658300690de6",
                "runway_gen_4_5": "36f3306e-ec29-434e-924f-658300690de6",
                "kling": "4c837b25-4ed4-4970-91aa-3688764df5a7",
                "kling_3": "4c837b25-4ed4-4970-91aa-3688764df5a7",
                "seedance": "15d600f7-965b-4801-a721-9862a86a083f",
                "seedance_v1_5_pro": "15d600f7-965b-4801-a721-9862a86a083f",
                "wan": "dd0b5f0b-9cdf-4fe9-b1e2-0cec030fcc69",
                "wan_2_5": "dd0b5f0b-9cdf-4fe9-b1e2-0cec030fcc69",
                "veo": "03ff70b9-cd2e-4768-9212-3b97fe8d415d",
                "veo_3_1_image_to_video": "03ff70b9-cd2e-4768-9212-3b97fe8d415d",
                "veo_fast": "03ff70b9-cd2e-4768-9212-3b97fe8d415d",
                "grok_imagine_video": "4f49016b-b0e3-40e1-a425-d5ab80082d06",
                "ltx_2": "bdae3ede-f4f4-4bfb-b397-308c62f5b882",
                "ltx_2_video": "bdae3ede-f4f4-4bfb-b397-308c62f5b882",
                "higgsfield_video": "a34a9b29-55cd-425a-8c9d-228ac41f4158",
            }
            
            if selected_model in PREDEFINED_VIDEO_NODES:
                original_target_node_id = PREDEFINED_VIDEO_NODES[selected_model]
            else:
                original_target_node_id = "36f3306e-ec29-434e-924f-658300690de6" # default Runway Gen-4.5
                
            is_custom = selected_model not in PREDEFINED_VIDEO_NODES
            if is_custom:
                import uuid
                custom_node_id = str(uuid.uuid4())
                target_node_id = custom_node_id
            else:
                target_node_id = original_target_node_id
 
            # Update nodes list
            # Update prompt nodes and convert imported Flux 2 Max to predefined Flux Pro 1.1 Ultra
            # to bypass the free-tier "imported models require a paid plan" restriction.
            for node in nodes:
                if node.get("id") in prompt_node_ids:
                    node_data = node.setdefault("data", {})
                    node_data["prompt"] = prompt
                    node_data.setdefault("output", {})["prompt"] = prompt
                    node_data.setdefault("result", {})["prompt"] = prompt
                elif node.get("id") == "33730285-f58d-42a5-9635-f45f75286c3c":
                    node_data = node.setdefault("data", {})
                    node_data["name"] = "Flux Pro 1.1 Ultra"
                    
                    model_obj = node_data.setdefault("model", {})
                    model_obj["name"] = "black-forest-labs/flux-1.1-pro-ultra"
                    model_obj["service"] = "replicate"
                    model_obj["version"] = "black-forest-labs/flux-1.1-pro-ultra"
                    if "text2ImgVersion" in model_obj:
                        del model_obj["text2ImgVersion"]
                    
                    kind = node_data.setdefault("kind", {})
                    kind_model = kind.setdefault("model", {})
                    kind_model["type"] = "predefined"
                    kind_model["name"] = "black-forest-labs/flux-1.1-pro-ultra"
                    kind_model["service"] = "replicate"
                    kind_model["version"] = "black-forest-labs/flux-1.1-pro-ultra"
                    node_data["isWildCard"] = False
                    
                    # Fix safety_tolerance parameter type (string to integer) to satisfy Replicate requirements
                    params = node_data.setdefault("params", {})
                    if "safety_tolerance" in params:
                        try:
                            params["safety_tolerance"] = int(params["safety_tolerance"])
                        except Exception:
                            params["safety_tolerance"] = 6
                    else:
                        params["safety_tolerance"] = 6
 
            model_inputs_map = {}  # always defined; predefined branch populates it below
            if is_custom:
                # Remove original target template node and inject new custom node
                nodes = [n for n in nodes if n.get("id") != original_target_node_id]
                
                WILDCARD_VIDEO_MODELS = {
                    "seedance_2_0": "fal-ai/bytedance/seedance/v2.0/pro/image-to-video",
                    "seedance_2_0_reference": "fal-ai/bytedance/seedance/v2.0/pro/reference-to-video",
                    "seedance_fast": "bytedance/seedance-2.0/image-to-video",
                    "seedance_v1_0": "fal-ai/bytedance/seedance/v1.0/image-to-video",
                    "kling_turbo": "fal-ai/kling-video/v3/turbo/pro/text-to-video",
                    "kling_1_6": "fal-ai/kling-video/v1.6/text-to-video",
                    "kling_o1_first_last_frame": "fal-ai/kling-video/v2.1/first-last-frame",
                    "kling_2_5_first_last_frame": "fal-ai/kling-video/v2.5/first-last-frame",
                    "kling_2_1_first_last_frame": "fal-ai/kling-video/v2.1/first-last-frame",
                    "sora_2": "fal-ai/sora/image-to-video",
                    "wan_2_2": "fal-ai/wan/v2.2/image-to-video",
                    "wan_2_7": "fal-ai/wan/v2.7/image-to-video",
                    "moonvalley": "fal-ai/moonvalley/image-to-video",
                    "veo_3_1_text_to_image": "fal-ai/veo/v3.1/text-to-image",
                    "veo_3_text_to_image": "fal-ai/veo/v3/text-to-image",
                    "veo_3_image_to_video": "fal-ai/veo/v3/image-to-video",
                    "veo_2": "fal-ai/veo/v2/image-to-video",
                    "runway_gen_4_turbo": "fal-ai/runway/gen-4/turbo",
                    "runway_gen_4": "fal-ai/runway/gen-4",
                    "runway_gen_3": "fal-ai/runway/gen-3",
                    "luma_ray_2": "fal-ai/luma-ray/v2/image-to-video",
                    "luma_ray_2_flash": "fal-ai/luma-ray/v2/flash/image-to-video",
                    "minimax_video_director": "fal-ai/minimax/video-director",
                    "minimax_video_01": "fal-ai/minimax/video-01",
                    "hunyuan": "fal-ai/hunyuan/video",
                    "skyreels": "fal-ai/skyreels/video",
                    "wan_video": "fal-ai/wan/video",
                    "pixverse": "fal-ai/pixverse/v4.5/image-to-video/fast",
                    "grok_imagine_video": "fal-ai/grok-imagine-video",
                }
                
                if selected_model == "kling_custom":
                    custom_node = self._create_kling_custom_node(custom_node_id, prompt)
                elif selected_model == "seedance_custom":
                    custom_node = self._create_seedance_custom_node(custom_node_id, prompt)
                else:
                    model_path = WILDCARD_VIDEO_MODELS.get(selected_model)
                    if not model_path:
                        model_path = f"fal-ai/{selected_model.replace('_', '-')}"
                    custom_node = self._create_wildcard_video_node(custom_node_id, model_path, "fal_imported", prompt)
                nodes.append(custom_node)

                # Inject image_url and end_image_url into wildcard video node params
                if image_url:
                    custom_node["data"]["params"]["image_url"] = image_url
                if end_image_url:
                    # FAL first-last-frame models use "tail_image_url" for last frame
                    if "first_last_frame" in selected_model:
                        custom_node["data"]["params"]["tail_image_url"] = end_image_url
                    else:
                        custom_node["data"]["params"]["end_image_url"] = end_image_url
 
                # Remap edges targeting the original node to target the new custom node
                for edge in edges:
                    if edge.get("target") == original_target_node_id:
                        edge["target"] = custom_node_id
                        orig_handle = edge.get("targetHandle", "")
                        if "prompt" in orig_handle:
                            edge["targetHandle"] = f"{custom_node_id}-input-prompt"
                        elif "image" in orig_handle:
                            edge["targetHandle"] = f"{custom_node_id}-input-image"
            else:
                # Update standard predefined video model node if selected
                for node in nodes:
                    if node.get("id") == target_node_id:
                        node_data = node.setdefault("data", {})
                        kind = node_data.setdefault("kind", {})
                        params = kind.setdefault("parameters", [])
                        
                        # Apply mapped duration if available
                        if mapped_duration is not None:
                            for pair in params:
                                if isinstance(pair, list) and len(pair) > 0:
                                    param_def = pair[0]
                                    if isinstance(param_def, dict) and param_def.get("id") == "duration":
                                        if len(pair) > 1:
                                            pair[1].setdefault("data", {})["value"] = mapped_duration
                            if "params" in node_data and isinstance(node_data["params"], dict):
                                node_data["params"]["duration"] = mapped_duration

                        # Apply aspect ratio if available
                        if aspect_ratio:
                            for pair in params:
                                if isinstance(pair, list) and len(pair) > 0:
                                    param_def = pair[0]
                                    if isinstance(param_def, dict) and param_def.get("id") == "aspect_ratio":
                                        if len(pair) > 1:
                                            pair[1].setdefault("data", {})["value"] = aspect_ratio
                                    elif isinstance(param_def, dict) and param_def.get("id") == "ratio":
                                        ratio_map = {
                                            "1:1": "960:960",
                                            "16:9": "1280:720",
                                            "9:16": "720:1280",
                                            "4:3": "960:960"
                                        }
                                        mapped_ratio = ratio_map.get(aspect_ratio, "1280:720")
                                        if len(pair) > 1:
                                            pair[1].setdefault("data", {})["value"] = mapped_ratio
                            
                            if "params" in node_data and isinstance(node_data["params"], dict):
                                if "aspect_ratio" in node_data["params"]:
                                    node_data["params"]["aspect_ratio"] = aspect_ratio
                                elif "ratio" in node_data["params"]:
                                    ratio_map = {
                                        "1:1": "960:960",
                                        "16:9": "1280:720",
                                        "9:16": "720:1280",
                                        "4:3": "960:960"
                                    }
                                    node_data["params"]["ratio"] = ratio_map.get(aspect_ratio, "1280:720")

                        if selected_model in ("runway", "runway_gen_4_5"):
                            # Update prompt input in inputs list
                            inputs = kind.setdefault("inputs", [])
                            for pair in inputs:
                                if isinstance(pair, list) and len(pair) > 0:
                                    inp_def = pair[0]
                                    if isinstance(inp_def, dict) and inp_def.get("id") == "prompt":
                                        if len(pair) > 1:
                                            pair[1]["string"] = prompt
                            
                            # Force random seed to prevent duplicate generation cached outputs
                            for pair in params:
                                if isinstance(pair, list) and len(pair) > 0:
                                    param_def = pair[0]
                                    if isinstance(param_def, dict) and param_def.get("id") == "seed":
                                        if len(pair) > 1:
                                            pair[1].setdefault("data", {}).setdefault("value", {})["isRandom"] = True
                        elif selected_model in ("veo", "veo_fast", "veo_3_1_image_to_video"):
                            model_val = "Fast" if selected_model == "veo_fast" else "Standard"
                            for pair in params:
                                if isinstance(pair, list) and len(pair) > 0:
                                    param_def = pair[0]
                                    if isinstance(param_def, dict) and param_def.get("id") == "model":
                                        if len(pair) > 1:
                                            pair[1].setdefault("data", {})["value"] = model_val
                        # Update reference image / video inputs for predefined models
                        model_inputs_map = {
                            "runway": ("start_frame", None, None),
                            "runway_gen_4_5": ("start_frame", None, None),
                            "veo": ("image", "last_frame_url", None),
                            "veo_fast": ("image", "last_frame_url", None),
                            "veo_3_1_image_to_video": ("image", "last_frame_url", None),
                            "kling": ("image", "end_image_url", None),
                            "kling_3": ("image", "end_image_url", None),
                            "seedance": ("image_url", "end_image_url", None),
                            "seedance_v1_5_pro": ("image_url", "end_image_url", None),
                            "grok_imagine_video": ("image_url", None, None),
                            "wan": ("image_url", None, None),
                            "wan_2_5": ("image_url", None, None),
                            "ltx_2": ("image_uri", None, None),
                            "ltx_2_video": ("image_uri", None, None),
                            "higgsfield_video": ("image", None, None),
                        }

                        if selected_model in model_inputs_map:
                            img_param, end_param, vid_param = model_inputs_map[selected_model]
                            inputs = kind.setdefault("inputs", [])
                            
                            # 1. Update inputs connection (convert edge-sourced inputs to static files)
                            if image_url and img_param:
                                for pair in inputs:
                                    if isinstance(pair, list) and len(pair) > 0:
                                        inp_def = pair[0]
                                        if isinstance(inp_def, dict) and inp_def.get("id") == img_param:
                                            val_obj = pair[1] if len(pair) > 1 else {}
                                            val_obj.clear()
                                            val_obj["file"] = {
                                                "url": image_url,
                                                "type": "image"
                                            }
                            
                            if end_image_url and end_param:
                                for pair in inputs:
                                    if isinstance(pair, list) and len(pair) > 0:
                                        inp_def = pair[0]
                                        if isinstance(inp_def, dict) and inp_def.get("id") == end_param:
                                            val_obj = pair[1] if len(pair) > 1 else {}
                                            val_obj.clear()
                                            val_obj["file"] = {
                                                "url": end_image_url,
                                                "type": "image"
                                            }

                            if video_url and vid_param:
                                for pair in inputs:
                                    if isinstance(pair, list) and len(pair) > 0:
                                        inp_def = pair[0]
                                        if isinstance(inp_def, dict) and inp_def.get("id") == vid_param:
                                            val_obj = pair[1] if len(pair) > 1 else {}
                                            val_obj.clear()
                                            val_obj["file"] = {
                                                "url": video_url,
                                                "type": "video"
                                            }

                            # 2. Update flat params dict
                            if "params" in node_data and isinstance(node_data["params"], dict):
                                if image_url and img_param:
                                    node_data["params"][img_param] = image_url
                                if end_image_url and end_param:
                                    node_data["params"][end_param] = end_image_url
                                if video_url and vid_param:
                                    node_data["params"][vid_param] = video_url
                                if negative_prompt:
                                    node_data["params"]["negative_prompt"] = negative_prompt

                            # 3. Update negative prompt in inputs list
                            if negative_prompt:
                                for pair in inputs:
                                    if isinstance(pair, list) and len(pair) > 0:
                                        inp_def = pair[0]
                                        if isinstance(inp_def, dict) and inp_def.get("id") == "negative_prompt":
                                            val_obj = pair[1] if len(pair) > 1 else {}
                                            val_obj.clear()
                                            val_obj["string"] = negative_prompt
  
            # Prune graph: keep only selected video model-related nodes and edges
            kept_nodes = {
                "ce243ec3-3c0a-47e7-bdee-f65d32ab7dd2",
                "1e8e7f8f-7ad5-4c66-8e3d-d6da1a0dd066",
                target_node_id
            }
            if not image_url:
                kept_nodes.update({
                    "cc21a21f-83d2-4436-b4dd-55dd28756fe1",
                    "abcba9c1-641f-4f40-ac75-75445c0501a8",
                    "33730285-f58d-42a5-9635-f45f75286c3c",
                    "e8628d7d-3879-422a-bb50-bf74dda72efb"
                })
            nodes = [n for n in nodes if n.get("id") in kept_nodes]
            
            # Remove edges that connect Ideogram V3 generator to the image reference input of the target node
            # if a custom image/video reference is provided.
            if selected_model in model_inputs_map:
                img_param, end_param, vid_param = model_inputs_map[selected_model]
                edges_to_remove = set()
                if image_url and img_param:
                    edges_to_remove.add(f"{target_node_id}-input-{img_param}")
                if end_image_url and end_param:
                    edges_to_remove.add(f"{target_node_id}-input-{end_param}")
                if video_url and vid_param:
                    edges_to_remove.add(f"{target_node_id}-input-{vid_param}")
                
                if edges_to_remove:
                    edges = [e for e in edges if e.get("targetHandle") not in edges_to_remove]

            edges = [e for e in edges if e.get("source") in kept_nodes and e.get("target") in kept_nodes]
 
        else: # Image generation
            selected_model = (model or "flux_pro").lower().replace("-", "_")
            
            WILDCARD_IMAGE_MODELS = {
                "reve": "fal-ai/reve/text-to-image",
                "higgsfield_image": "fal-ai/higgsfield/text-to-image",
                "gpt_image_1": "openai/gpt-image-1",
                "imagen_3_fast": "google/imagen-3-fast",
                "flux_2_pro": "fal-ai/flux-2-pro",
                "flux_2_flex": "fal-ai/flux-2-flex",
                "flux_2_dev_lora": "fal-ai/flux-2-dev-lora",
                "flux_1_1_ultra": "fal-ai/flux-pro/v1.1-ultra",
                "flux_pro_1_1": "fal-ai/flux-pro/v1.1",
                "flux_fast": "fal-ai/flux/schnell",
                "flux_dev_lora": "fal-ai/flux/dev/lora",
                "recraft_v3": "fal-ai/recraft-v3",
                "mystic": "fal-ai/mystic",
                "ideogram_v3": "fal-ai/ideogram/v3",
                "ideogram_v3_character": "fal-ai/ideogram/v3/character",
                "stable_diffusion_3_5": "fal-ai/stable-diffusion-3.5",
                "minimax_image_01": "fal-ai/minimax-image-01",
                "bria": "fal-ai/bria",
                "dalle_3": "openai/dalle-3",
                "luma_photon": "fal-ai/luma-photon",
                "nvidia_sana": "fal-ai/nvidia-sana",
                "nvidia_consistory": "fal-ai/nvidia-consistory",
                "ideogram_4": "ideogram/v4",
            }
 
            # Models that inject a native verified node (replacing the default Flux Pro node)
            # These use known Weavy node IDs that are pre-verified on the backend
            NATIVE_NODE_IDS = {
                "gpt_image_2": "zeSQQxxjcaVdWWunD60J1",
                "imagen_4": "iKH4HnqWscrhz5Haxoz3",
                "nano_banana_2": "bebebed5-50c1-4701-98b3-86929db21585",
                "nano_banana_pro": "af5a6789-c7af-4c46-9a42-70222654e55b",
            }
            
            for m_key in WILDCARD_IMAGE_MODELS:
                NATIVE_NODE_IDS[m_key] = None
                
            NATIVE_INJECTED_MODELS = tuple(NATIVE_NODE_IDS.keys())
            
            target_node_id = "0461c91d-28a2-42c2-a289-195696bfef5b" # Flux Pro 1.1 Ultra
            prompt_node_id = "1b9fb36d-40a5-4c30-a7ec-b26765d37516"
            
            if selected_model in ("imagen", "imagen_3"):
                target_node_id = "922e6e8f-9e54-4a6d-a952-1a99776b2f10" # Google Imagen 3
                prompt_node_id = "8607c761-7f3a-45e2-b1b5-ba4c99acd6be"
            elif selected_model == "gemini_nano":
                target_node_id = "6e127a9d-932a-4a8f-a4f5-c10652cbeee2" # Gemini 2.5 Flash (Nano Banana)
                prompt_node_id = "851c4645-67e3-4bc1-a2ff-c8acbc686fb1"
            elif selected_model == "rodin_3d":
                target_node_id = "9f5f74ef-4c63-4d32-a836-fc444a3fa5e9" # Rodin 3D
                prompt_node_id = "8607c761-7f3a-45e2-b1b5-ba4c99acd6be"
            elif selected_model == "sd_outpaint":
                target_node_id = "d969b907-85f1-4117-b99f-6609c38f9220" # SD3 Outpaint
                prompt_node_id = "1b9fb36d-40a5-4c30-a7ec-b26765d37516"
            elif selected_model == "topaz_enhance":
                # Topaz upscaling: target the topaz node directly.
                # pair[1] in its inputs has nodeId pointing to sd_outpaint — we clear that
                # and replace with our direct file URL, making it standalone.
                target_node_id = "d42600b0-684e-41fd-afba-fa84d5eeec28"  # Topaz Upscale (custommodelV2)
                prompt_node_id = None
                # Pre-upload input image to Weavy CDN so topaz gets a valid media.weavy.ai URL.
                # External URLs (imgur etc.) cause internalErrorCode 1076.
                if image_url:
                    try:
                        _topaz_weavy_file = self._upload_image_to_weavy(image_url, token)
                    except Exception as _upload_err:
                        logger.warning("[weavy_service] Topaz image upload failed, will use raw URL: %s", _upload_err)
                        _topaz_weavy_file = None
                else:
                    _topaz_weavy_file = None

            elif selected_model in NATIVE_INJECTED_MODELS:
                # Use the known native node ID if available, otherwise generate random UUID
                native_id = NATIVE_NODE_IDS.get(selected_model)
                if native_id:
                    custom_node_id = native_id
                else:
                    import uuid
                    custom_node_id = str(uuid.uuid4())
                target_node_id = custom_node_id
 
            # Remove original target node and inject native verified node
            if selected_model in NATIVE_INJECTED_MODELS:
                original_target_node_id = "0461c91d-28a2-42c2-a289-195696bfef5b"
                nodes = [n for n in nodes if n.get("id") != original_target_node_id]
                if selected_model == "gpt_image_2":
                    custom_node = self._create_gpt_image_2_node(custom_node_id, prompt, aspect_ratio)
                elif selected_model == "imagen_4":
                    custom_node = self._create_imagen_4_node(custom_node_id, prompt, aspect_ratio)
                elif selected_model == "nano_banana_2":
                    custom_node = self._create_nano_banana_2_node(custom_node_id, prompt, aspect_ratio, image_url)
                elif selected_model == "nano_banana_pro":
                    custom_node = self._create_nano_banana_pro_node(custom_node_id, prompt, aspect_ratio, image_url)
                elif selected_model in WILDCARD_IMAGE_MODELS:
                    model_path = WILDCARD_IMAGE_MODELS[selected_model]
                    service = "openai" if "openai" in model_path or "dalle" in selected_model else "fal_imported"
                    custom_node = self._create_wildcard_image_node(
                        custom_node_id,
                        model_path,
                        service,
                        prompt,
                        negative_prompt=negative_prompt,
                        image_url=image_url,
                        aspect_ratio=aspect_ratio
                    )
                else: # fallback
                    custom_node = self._create_wildcard_image_node(
                        custom_node_id,
                        "ideogram/v4",
                        "fal_imported",
                        prompt,
                        negative_prompt=negative_prompt,
                        image_url=image_url,
                        aspect_ratio=aspect_ratio
                    )
                nodes.append(custom_node)
 
                # Remap edges from original Flux Pro target to new node
                for edge in edges:
                    if edge.get("target") == original_target_node_id:
                        edge["target"] = custom_node_id
                        edge["targetHandle"] = f"{custom_node_id}-input-prompt"
 
            for node in nodes:
                if node.get("id") == prompt_node_id:
                    node_data = node.setdefault("data", {})
                    node_data["prompt"] = prompt
                    node_data.setdefault("output", {})["prompt"] = prompt
                    node_data.setdefault("result", {})["prompt"] = prompt
                
                # Update parameters if node matches selected model
                elif node.get("id") == target_node_id:
                    node_data = node.setdefault("data", {})
                    kind = node_data.setdefault("kind", {})

                    if selected_model in ("flux_pro", "imagen", "imagen_3", "gemini_nano"):
                        kind.setdefault("prompt", {})["string"] = prompt
                        # Normalise to ratio string for Replicate/Google native nodes.
                        # Accepts both "9:16" (new standard) and "portrait_16_9" (legacy FAL).
                        FAL_TO_RATIO = {
                            "square_hd": "1:1", "square": "1:1",
                            "portrait_4_3": "3:4", "portrait_16_9": "9:16",
                            "landscape_4_3": "4:3", "landscape_16_9": "16:9",
                        }
                        ratio_val = FAL_TO_RATIO.get(aspect_ratio, aspect_ratio)
                        # Whitelist — Flux Pro 1.1 Ultra (FAL) only accepts these exact strings.
                        # Any unrecognised value (e.g. "auto", "portrait", "1024x1024") falls
                        # back to "1:1" rather than being forwarded and rejected by FAL.
                        VALID_RATIOS = {"1:1", "9:16", "16:9", "3:4", "4:3", "21:9", "9:21", "3:2", "2:3"}
                        if ratio_val not in VALID_RATIOS:
                            logger.warning("[weavy_service] aspect_ratio %r not in whitelist → fallback to 1:1", ratio_val)
                            ratio_val = "1:1"
                        logger.info("[weavy_service] flux_pro aspectRatio: %r → %r", aspect_ratio, ratio_val)
                        # kind.aspectRatio.data.value — visual node display
                        ar = kind.setdefault("aspectRatio", {})
                        ar.setdefault("data", {})["value"] = ratio_val
                        # params.aspect_ratio — the actual API param (snake_case!)
                        node_data.setdefault("params", {})["aspect_ratio"] = ratio_val
                        kind.setdefault("seed", {}).setdefault("data", {}).setdefault("value", {})["isRandom"] = True

                        if selected_model in ("imagen", "imagen_3") and negative_prompt:
                            node_data.setdefault("params", {})["negative_prompt"] = negative_prompt
                            inputs = kind.setdefault("inputs", [])
                            found = False
                            for pair in inputs:
                                if isinstance(pair, list) and len(pair) > 0:
                                    inp_def = pair[0]
                                    if isinstance(inp_def, dict) and inp_def.get("id") == "negative_prompt":
                                        val_obj = pair[1] if len(pair) > 1 else {}
                                        val_obj.clear()
                                        val_obj["string"] = negative_prompt
                                        found = True
                                        break
                            if not found:
                                inputs.append([
                                    {
                                        "id": "negative_prompt",
                                        "title": "negative_prompt",
                                        "required": False,
                                        "validTypes": ["text"]
                                    },
                                    {
                                        "string": negative_prompt
                                    }
                                ])

                        if selected_model == "flux_pro" and image_url:
                            node_data.setdefault("params", {})["image_prompt"] = image_url
                            inputs = kind.setdefault("inputs", [])
                            found = False
                            for pair in inputs:
                                if isinstance(pair, list) and len(pair) > 0:
                                    inp_def = pair[0]
                                    if isinstance(inp_def, dict) and inp_def.get("id") == "image_prompt":
                                        val_obj = pair[1] if len(pair) > 1 else {}
                                        val_obj.clear()
                                        val_obj["file"] = {
                                            "url": image_url,
                                            "type": "image"
                                        }
                                        found = True
                                        break
                            if not found:
                                inputs.append([
                                    {
                                        "id": "image_prompt",
                                        "title": "image_prompt",
                                        "required": False,
                                        "validTypes": ["image"]
                                    },
                                    {
                                        "file": {
                                            "url": image_url,
                                            "type": "image"
                                        }
                                    }
                                ])

                    elif selected_model == "sd_outpaint" and image_url:
                        # sd_outpaint: inject input image into node
                        node_data.setdefault("params", {})["image"] = image_url
                        inputs = kind.setdefault("inputs", [])
                        found = False
                        for pair in inputs:
                            if isinstance(pair, list) and len(pair) > 0:
                                inp_def = pair[0]
                                if isinstance(inp_def, dict) and inp_def.get("id") == "image":
                                    if len(pair) > 1 and isinstance(pair[1], dict):
                                        val_obj = pair[1]
                                        val_obj.clear()
                                    else:
                                        val_obj = {}
                                        if len(pair) > 1:
                                            pair[1] = val_obj
                                        else:
                                            pair.append(val_obj)
                                    val_obj["file"] = {
                                        "url": image_url,
                                        "type": "image"
                                    }
                                    found = True
                                    break
                        if not found:
                            inputs.append([
                                {
                                    "id": "image",
                                    "title": "image",
                                    "required": True,
                                    "validTypes": ["image"]
                                },
                                {
                                    "file": {
                                        "url": image_url,
                                        "type": "image"
                                    }
                                }
                            ])

                    elif selected_model == "topaz_enhance" and image_url:
                        # topaz-enhance (custommodelV2): inject uploaded Weavy file object.
                        # Weavy rejects external URLs (error 1076); must use media.weavy.ai CDN URLs.
                        weavy_file = _topaz_weavy_file  # pre-uploaded above; fallback to raw url if None
                        if weavy_file:
                            cdn_url = weavy_file.get("url", image_url)
                        else:
                            cdn_url = image_url  # fallback

                        params = node_data.setdefault("params", {})
                        if weavy_file:
                            params["image"] = {
                                "id": weavy_file.get("id"),
                                "url": weavy_file.get("url"),
                                "type": "image",
                                "width": weavy_file.get("width"),
                                "height": weavy_file.get("height"),
                                "publicId": weavy_file.get("publicId"),
                                "thumbnailUrl": weavy_file.get("thumbnailUrl"),
                                "viewUrl": weavy_file.get("viewUrl"),
                                "originalUrl": weavy_file.get("originalUrl"),
                            }
                        else:
                            params["image"] = {"url": cdn_url, "type": "image"}

                        params.setdefault("model", "Standard V2")
                        params.setdefault("upscale_factor", 4)
                        params.setdefault("output_format", "jpeg")
                        params.setdefault("face_enhancement", True)
                        params.setdefault("subject_detection", "All")
                        params.setdefault("face_enhancement_strength", 0.8)
                        params.setdefault("face_enhancement_creativity", 0)
                        params.setdefault("sharpen", 0)
                        params.setdefault("denoise", 0)
                        params.setdefault("fix_compression", 0)

                        # Build proper file object for inputs pair[1]
                        if weavy_file:
                            file_obj = {
                                "id": weavy_file.get("id"),
                                "url": weavy_file.get("url"),
                                "type": "image",
                                "width": weavy_file.get("width"),
                                "height": weavy_file.get("height"),
                                "publicId": weavy_file.get("publicId"),
                                "thumbnailUrl": weavy_file.get("thumbnailUrl"),
                            }
                        else:
                            file_obj = {"url": cdn_url, "type": "image"}

                        inputs = kind.setdefault("inputs", [])
                        found = False
                        for pair in inputs:
                            if isinstance(pair, list) and len(pair) > 0:
                                inp_def = pair[0]
                                if isinstance(inp_def, dict) and inp_def.get("id") == "image":
                                    # Replace pair[1]: remove nodeId/outputId, set direct file obj
                                    new_val = {"file": file_obj}
                                    if len(pair) > 1:
                                        pair[1] = new_val
                                    else:
                                        pair.append(new_val)
                                    found = True
                                    break
                        if not found:
                            inputs.append([
                                {
                                    "id": "image",
                                    "title": "",
                                    "required": True,
                                    "validTypes": ["image"],
                                    "description": "The image to upscale"
                                },
                                {"file": file_obj}
                            ])



            # Prune graph: keep only selected model node and its prompt
            kept_nodes = {target_node_id}
            if prompt_node_id:
                kept_nodes.add(prompt_node_id)
            if selected_model == "rodin_3d":
                # Rodin 3D requires Google Imagen 3 and its prompt
                kept_nodes.add("922e6e8f-9e54-4a6d-a952-1a99776b2f10")
                kept_nodes.add("8607c761-7f3a-45e2-b1b5-ba4c99acd6be")
            elif selected_model == "topaz_enhance":
                # Topaz: add output node so the graph has 2 connected nodes (topaz → output)
                kept_nodes.add("7af5e147-6f94-4bca-946f-97a82acb1e04")  # Output wildcard

                
            nodes = [n for n in nodes if n.get("id") in kept_nodes]
            edges = [e for e in edges if e.get("source") in kept_nodes and e.get("target") in kept_nodes]

        # ── Topaz-enhance fast path: use /v1/models/run (not batch execute) ──────
        # The batch execute endpoint returns internalErrorCode 1076 for custommodelV2
        # nodes. The canvas "Run selected" button uses /v1/models/run instead.
        if selected_model == "topaz_enhance" and image_url:
            weavy_cdn_url = _topaz_weavy_file.get("url", image_url) if _topaz_weavy_file else image_url
            recipe_version = recipe_data.get("version", 1)
            run_payload = {
                "model": {
                    "name": "topaz-enhance",
                    "type": "topaz-enhance",   # required — matches qe.TopazEnhance enum value
                    "description": "Enhance the resolution of images by up to 600%"
                },
                "input": {
                    "image": weavy_cdn_url,
                    "model": "Standard V2",
                    "upscale_factor": 4,
                    "output_format": "jpeg",
                    "face_enhancement": True,
                    "subject_detection": "All",
                    "face_enhancement_strength": 0.8,
                    "face_enhancement_creativity": 0,
                    "sharpen": 0,
                    "denoise": 0,
                    "fix_compression": 0,
                },
                "nodeId": "d42600b0-684e-41fd-afba-fa84d5eeec28",
                "recipeId": recipe_id,
                "recipeVersion": recipe_version,
            }
            logger.info("[weavy_service] Topaz path: POST /v1/models/run (recipeVersion=%s, image=%s)",
                        recipe_version, weavy_cdn_url[:80])
            run_resp = self._request("POST", "https://api.weavy.ai/api/v1/models/run",
                                     headers=headers, json=run_payload, timeout=30)
            if run_resp.status_code == 201:
                prediction_id = run_resp.json().get("predictionId", "")
                logger.info("[weavy_service] Topaz accepted. predictionId: %s", prediction_id)
                return f"TOPAZ_PRED:{prediction_id}"
            raise RuntimeError(
                f"Topaz models/run failed ({run_resp.status_code}): {run_resp.text[:300]}"
            )

        # 3. Save modified flow to Weavy backend
        save_url = f"https://api.weavy.ai/api/v1/recipes/{recipe_id}/save"
        save_payload = {"nodes": nodes, "edges": edges}
        logger.info("[weavy_service] POST %s", save_url)
        save_resp = self._request("POST", save_url, headers=headers, json=save_payload, timeout=15)
        if save_resp.status_code != 201:
            raise RuntimeError(f"Save modified recipe failed (Status {save_resp.status_code}): {save_resp.text}")

        # 4. Trigger flow execution batch run
        exec_url = f"https://api.weavy.ai/api/v1/batches/recipes/{recipe_id}/execute"
        exec_payload = {
            "numberOfRuns": 1,
            "nodes": nodes,
            "edges": edges
        }
        logger.info("[weavy_service] POST %s", exec_url)
        exec_resp = self._request("POST", exec_url, headers=headers, json=exec_payload, timeout=15)

        exec_data_raw = None
        if exec_resp.status_code == 403 and self._current_profile_dir:
            # CF challenge on execute — try Camoufox browser fallback
            logger.info("[weavy_service] CF 403 on execute, trying Camoufox browser fallback...")
            import json as _json
            # Build a plain-dict version of headers safe to pass to page.evaluate()
            plain_headers = {
                "x-weavy-auth-provider": "firebase",
                "x-app-version": "4.1.489",
                "authorization": headers.get("authorization", ""),
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            browser_result = self._execute_via_browser(exec_url, plain_headers, exec_payload)
            if browser_result["status"] == 201:
                try:
                    exec_data_raw = _json.loads(browser_result["text"])
                except Exception:
                    exec_data_raw = None
            if not exec_data_raw:
                raise RuntimeError(
                    f"Execute recipe batch failed (Status {exec_resp.status_code}, "
                    f"browser fallback status {browser_result['status']}): {browser_result['text'][:200]}"
                )
        elif exec_resp.status_code != 201:
            raise RuntimeError(f"Execute recipe batch failed (Status {exec_resp.status_code}): {exec_resp.text}")
        else:
            exec_data_raw = exec_resp.json()

        batch_id = exec_data_raw.get("batchId")
        if not batch_id:
            raise RuntimeError("Execute response did not contain batchId")
            
        logger.info("[weavy_service] Batch run triggered successfully. Batch ID: %s", batch_id)
        return batch_id

    def _poll_topaz_prediction(self, token: str, prediction_id: str) -> Dict[str, Any]:
        """Poll /v1/models/predict/{id}/status for a topaz-enhance prediction.
        
        Maps the Topaz prediction status to the same dict shape used by poll_batch_status:
          {"status": "completed", "urls": [...]} | {"status": "running"} | {"status": "failed", "error": "..."}
        """
        url = f"https://api.weavy.ai/api/v1/models/predict/{prediction_id}/status"
        headers = {
            "x-weavy-auth-provider": "firebase",
            "x-app-version": "4.1.489",
            "authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        resp = self._request("GET", url, headers=headers, timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to poll topaz prediction status ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        status = data.get("status", "")
        logger.info("[weavy_service] Topaz prediction %s status: %s", prediction_id[:8], status)
        if status == "succeeded":
            results = data.get("results") or []
            urls = [r["url"] for r in results if isinstance(r, dict) and r.get("url")]
            logger.info("[weavy_service] Topaz succeeded. URLs: %s", urls)
            return {"status": "completed", "urls": urls}
        elif status in ("failed", "error"):
            err = data.get("error") or "Topaz prediction failed"
            logger.error("[weavy_service] Topaz failed: %s", err)
            return {"status": "failed", "error": err}
        else:
            return {"status": "running"}

    def poll_batch_status(self, token: str, recipe_id: str, batch_id: str) -> Dict[str, Any]:
        """Poll the batch execution progress and return results."""
        # Topaz uses /v1/models/predict/{id}/status (different from batch endpoint)
        if batch_id.startswith("TOPAZ_PRED:"):
            return self._poll_topaz_prediction(token, batch_id[len("TOPAZ_PRED:"):])

        url = f"https://api.weavy.ai/api/v1/batches/recipes/{recipe_id}/batches/{batch_id}/status"
        headers = {
            "x-weavy-auth-provider": "firebase",
            "x-app-version": "4.1.489",
            "authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        resp = self._request("GET", url, headers=headers, timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to poll batch status (Status {resp.status_code}): {resp.text}")
        
        data = resp.json()
        recipe_runs = data.get("recipeRuns", [])
        
        # Determine status and extract results
        status = "queued"
        media_urls = []
        error_msg = ""

        def _extract_urls_recursive(obj, found=None):
            """Recursively walk any dict/list structure and collect all 'url' values."""
            if found is None:
                found = []
            if isinstance(obj, dict):
                val = obj.get("url")
                if val and isinstance(val, str) and val.startswith("http"):
                    found.append(val)
                for v in obj.values():
                    _extract_urls_recursive(v, found)
            elif isinstance(obj, list):
                for item in obj:
                    _extract_urls_recursive(item, found)
            return found
        
        if recipe_runs:
            active_statuses = {"RUNNING", "QUEUED", "PENDING"}
            has_running = any(r.get("status") in active_statuses for r in recipe_runs)
            
            if has_running:
                status = "running"
            else:
                has_failed = any(r.get("status") in ("FAILED", "CANCELLED", "ERROR") for r in recipe_runs)
                if has_failed:
                    status = "failed"
                    error_msg = next((r.get("error") for r in recipe_runs if r.get("status") in ("FAILED", "CANCELLED", "ERROR") and r.get("error")), "Flow execution failed")
                else:
                    has_completed = any(r.get("status") == "COMPLETED" for r in recipe_runs)
                    if has_completed:
                        status = "completed"
                        for run in recipe_runs:
                            if run.get("status") != "COMPLETED":
                                continue
                            node_runs = run.get("nodeRuns", [])
                            run_urls = []
                            
                            # Debug: log each nodeRun result shape so we can diagnose format issues
                            for idx, node_run in enumerate(node_runs):
                                res = node_run.get("result")
                                node_id = node_run.get("nodeId", "?")
                                logger.info(
                                    "[weavy_service] nodeRun[%d] nodeId=%s result type=%s snippet=%s",
                                    idx, node_id, type(res).__name__,
                                    str(res)[:300] if res is not None else "None"
                                )

                                if res:
                                    # Primary extraction: direct url key
                                    if isinstance(res, list):
                                        for item in res:
                                            if isinstance(item, dict) and item.get("url"):
                                                run_urls.append(item["url"])
                                    elif isinstance(res, dict) and res.get("url"):
                                        run_urls.append(res["url"])

                            # Fallback: if still empty, do a recursive deep-scan of all nodeRuns in this run
                            if not run_urls:
                                logger.info("[weavy_service] Primary extraction yielded no URLs for completed run — running recursive scan")
                                run_urls = _extract_urls_recursive(node_runs)
                                logger.info("[weavy_service] Recursive scan found %d URL(s): %s", len(run_urls), run_urls[:5])
                            
                            media_urls.extend(run_urls)
                        
                        # Deduplicate while preserving order
                        seen = set()
                        media_urls = [x for x in media_urls if not (x in seen or seen.add(x))]
                        
                        # Prioritize video URLs if any are found
                        def is_video(url: str) -> bool:
                            u_lower = url.lower()
                            return any(ext in u_lower for ext in (".mp4", ".webm", ".mkv", ".avi", ".mov", ".flv")) or "/video/" in u_lower
                            
                        video_urls = [u for u in media_urls if is_video(u)]
                        if video_urls:
                            logger.info("[weavy_service] Video URL(s) detected. Filtering out static images: %s", video_urls)
                            media_urls = video_urls
                    else:
                        status = "failed"
                        error_msg = next((r.get("error") for r in recipe_runs if r.get("error")), "Flow execution failed")
        
        return {
            "status": status,
            "urls": media_urls,
            "error": error_msg,
            "raw": data
        }

    def _create_gpt_image_2_node(self, node_id: str, prompt: str, aspect_ratio: str) -> Dict[str, Any]:
        """Create a native verified ChatGPT Images 2.0 node (custommodelV2 with gpt_image_1 kind)."""
        # Map size value to GPT Image 2 pixel dimensions.
        # Accepts FAL-style names (square_hd, portrait_16_9…), old ratio strings (1:1…),
        # or pixel strings (1024x1024…) from the frontend.
        size_map = {
            # FAL-style names (new frontend format)
            "square_hd": "1024x1024",
            "square": "1024x1024",
            "portrait_4_3": "1024x1365",
            "portrait_16_9": "1024x1536",
            "landscape_4_3": "1365x1024",
            "landscape_16_9": "1536x1024",
            # Old ratio strings (legacy / backward-compat)
            "1:1": "1024x1024",
            "16:9": "1536x1024",
            "9:16": "1024x1536",
            "4:3": "1365x1024",
            "3:4": "1024x1365",
        }
        # If already a pixel string, use it directly
        size = size_map.get(aspect_ratio, aspect_ratio if (aspect_ratio and "x" in aspect_ratio) else "1024x1024")
        return {
            "id": node_id,
            "dragHandle": ".node-header",
            "owner": None,
            "type": "custommodelV2",
            "visibility": "private",
            "isModel": True,
            "data": {
                "handles": {
                    "input": {
                        "prompt": {
                            "id": "470e45b3-3c3a-46f1-822e-715abc4610ab",
                            "type": "text",
                            "order": 0,
                            "format": "text",
                            "required": True,
                            "description": "Text prompt for image generation"
                        }
                    },
                    "output": {
                        "image": {
                            "id": "470e45b3-3c3a-46f1-822e-715abc4610ac",
                            "type": "image",
                            "label": "image",
                            "order": 0,
                            "format": "uri",
                            "description": "Image result"
                        }
                    }
                },
                "name": "ChatGPT Images 2.0",
                "description": "Generate images with OpenAI's GPT Image 2",
                "color": "Red",
                "label": None,
                "menu": {
                    "icon": "EmojiObjectsIcon",
                    "isModel": True,
                    "displayName": "GPT Image 1"
                },
                "model": {
                    "name": "gpt_image_1"
                },
                "params": {
                    "size": size,
                    "model": "GPT Image 2",
                    "quality": "medium",
                    "background": "opaque",
                    "output_format": "png",
                    "number_of_images": 1
                },
                "schema": {},
                "version": 3,
                "kind": {
                    "type": "gpt_image_1",
                    "inputs": [
                        [
                            {
                                "id": "prompt",
                                "title": "prompt",
                                "required": True,
                                "validTypes": ["text"],
                                "description": "Text prompt for image generation"
                            },
                            {
                                "nodeId": "1b9fb36d-40a5-4c30-a7ec-b26765d37516",
                                "string": prompt,
                                "outputId": "prompt"
                            }
                        ]
                    ],
                    "outputs": [
                        {
                            "id": "image",
                            "title": "image",
                            "dataType": "image"
                        }
                    ],
                    "parameters": [
                        [
                            {"id": "model", "title": "Model"},
                            {"data": {"type": "string", "value": "GPT Image 2"}, "type": "value"}
                        ],
                        [
                            {"id": "size", "title": "Resolution"},
                            {"data": {"type": "string", "value": size}, "type": "value"}
                        ],
                        [
                            {"id": "quality", "title": "Quality"},
                            {"data": {"type": "string", "value": "medium"}, "type": "value"}
                        ]
                    ]
                }
            }
        }

    def _create_imagen_4_node(self, node_id: str, prompt: str, aspect_ratio: str) -> Dict[str, Any]:
        """Create a native verified Google Imagen 4 node (custommodelV2 with imagen4 kind)."""
        return {
            "id": node_id,
            "dragHandle": ".node-header",
            "owner": None,
            "type": "custommodelV2",
            "visibility": "private",
            "isModel": True,
            "data": {
                "handles": {
                    "input": {
                        "prompt": {
                            "id": "470e45b3-3c3a-46f1-822e-715abc4610ab",
                            "type": "text",
                            "order": 0,
                            "format": "text",
                            "required": True,
                            "description": "Text prompt for image generation"
                        }
                    },
                    "output": {
                        "result": {
                            "id": "470e45b3-3c3a-46f1-822e-715abc4610ac",
                            "type": "image",
                            "label": "result",
                            "order": 0,
                            "format": "uri",
                            "description": "Result image"
                        }
                    }
                },
                "name": "Google Imagen 4",
                "description": "Google\u2019s highest quality image generation model",
                "color": "Red",
                "label": None,
                "menu": {
                    "icon": "EmojiObjectsIcon",
                    "isModel": True,
                    "displayName": "Google Imagen"
                },
                "model": {
                    "name": "imagen4"
                },
                "params": {
                    "model": "Standard",
                    "aspect_ratio": aspect_ratio
                },
                "schema": {},
                "version": 3,
                "kind": {
                    "type": "imagen4",
                    "prompt": {
                        "nodeId": "1b9fb36d-40a5-4c30-a7ec-b26765d37516",
                        "string": prompt,
                        "outputId": "prompt"
                    },
                    "aspectRatio": {
                        "data": {
                            "type": "string",
                            "value": aspect_ratio
                        },
                        "type": "value"
                    }
                }
            }
        }

    def _create_nano_banana_2_node(self, node_id: str, prompt: str, aspect_ratio: str = "Default", image_url: Optional[str] = None) -> Dict[str, Any]:
        """Create a native verified Gemini 3.1 Flash (Nano Banana 2) node."""
        return {
            "id": node_id,
            "dragHandle": ".node-header",
            "owner": None,
            "type": "custommodelV2",
            "visibility": None,
            "isModel": True,
            "data": {
                "handles": {
                    "input": {
                        "prompt": {
                            "id": "dowp4A1BWXIovGVDkD0o",
                            "type": "text",
                            "label": "prompt",
                            "order": 0,
                            "format": "text",
                            "required": True,
                            "description": "Description of the edits you want to make"
                        }
                    },
                    "output": {
                        "result": {
                            "id": "DAeb7gVmPkddYRJbt1EE",
                            "type": "image",
                            "label": "result",
                            "order": 0,
                            "format": "uri",
                            "description": "Result image"
                        }
                    }
                },
                "name": "Gemini 3.1 Flash (Nano Banana 2)",
                "description": "Google's state-of-the-art image generation and editing model",
                "color": "Red",
                "label": None,
                "menu": {
                    "icon": "EmojiObjectsIcon",
                    "isModel": True,
                    "displayName": "Gemini Edit"
                },
                "model": {
                    "name": "fal-ai/nano-banana-2/edit",
                    "service": "fal_imported",
                    "version": "fal-ai/nano-banana-2/edit"
                },
                "params": {
                    "seed": {"seed": 678228, "isRandom": True},
                    "resolution": "1K",
                    "aspect_ratio": aspect_ratio if aspect_ratio not in (None, "", "1:1") else "Default",
                    "output_format": "png",
                    "enable_web_search": False,
                    **({"image_url": image_url} if image_url else {})
                },
                "schema": {},
                "version": 3,
                "kind": {
                    "type": "wildcard",
                    "model": {
                        "type": "predefined",
                        "name": "fal-ai/nano-banana-2/edit",
                        "version": "fal-ai/nano-banana-2/edit",
                        "service": "fal_imported",
                        "originalImportSource": "fal-ai/nano-banana-2/edit",
                        "description": "Gemini 3.1 Flash (Nano Banana 2)"
                    },
                    "inputs": [
                        [
                            {
                                "id": "prompt",
                                "title": "prompt",
                                "required": True,
                                "validTypes": ["text"],
                                "description": "Description of the edits you want to make"
                            },
                            {
                                "nodeId": "1b9fb36d-40a5-4c30-a7ec-b26765d37516",
                                "string": prompt,
                                "outputId": "prompt"
                            }
                        ]
                    ],
                    "outputs": [
                        {"id": "result", "title": "result", "dataType": "image"}
                    ],
                    "parameters": [
                        [
                            {"id": "resolution", "title": "Resolution"},
                            {"data": {"type": "string", "value": "1K"}, "type": "value"}
                        ]
                    ]
                }
            }
        }

    def _create_nano_banana_pro_node(self, node_id: str, prompt: str, aspect_ratio: str = "auto", image_url: Optional[str] = None) -> Dict[str, Any]:
        """Create a native verified Gemini 3 (Nano Banana Pro) node."""
        return {
            "id": node_id,
            "dragHandle": ".node-header",
            "owner": None,
            "type": "custommodelV2",
            "visibility": None,
            "isModel": True,
            "data": {
                "handles": {
                    "input": {
                        "prompt": {
                            "id": "dowp4A1BWXIovGVDkD0o",
                            "type": "text",
                            "label": "prompt",
                            "order": 0,
                            "format": "text",
                            "required": True,
                            "description": "Description of the edits you want to make"
                        }
                    },
                    "output": {
                        "result": {
                            "id": "DAeb7gVmPkddYRJbt1EE",
                            "type": "image",
                            "label": "result",
                            "order": 0,
                            "format": "uri",
                            "description": "Result image"
                        }
                    }
                },
                "name": "Gemini 3 (Nano Banana Pro)",
                "description": "Google's state-of-the-art image generation and editing model",
                "color": "Red",
                "label": None,
                "menu": {
                    "icon": "EmojiObjectsIcon",
                    "isModel": True,
                    "displayName": "Gemini 3 Pro (with Nano Banana)"
                },
                "model": {
                    "name": "fal-ai/nano-banana-pro/edit",
                    "service": "fal_imported",
                    "version": "fal-ai/nano-banana-pro/edit"
                },
                "params": {
                    "seed": {"seed": 678228, "isRandom": True},
                    "prompt": prompt,
                    "resolution": "1K",
                    "aspect_ratio": aspect_ratio if aspect_ratio not in (None, "") else "auto",
                    "output_format": "png",
                    "enable_web_search": False,
                    **({"image_url": image_url} if image_url else {})
                },
                "schema": {},
                "version": 3,
                "kind": {
                    "type": "wildcard",
                    "model": {
                        "type": "predefined",
                        "name": "fal-ai/nano-banana-pro/edit",
                        "version": "fal-ai/nano-banana-pro/edit",
                        "service": "fal_imported",
                        "originalImportSource": "fal-ai/nano-banana-pro/edit",
                        "description": "Gemini 3 (Nano Banana Pro)"
                    },
                    "inputs": [
                        [
                            {
                                "id": "prompt",
                                "title": "prompt",
                                "required": True,
                                "validTypes": ["text"],
                                "description": "Description of the edits you want to make"
                            },
                            {
                                "nodeId": "1b9fb36d-40a5-4c30-a7ec-b26765d37516",
                                "string": prompt,
                                "outputId": "prompt"
                            }
                        ]
                    ],
                    "outputs": [
                        {"id": "result", "title": "result", "dataType": "image"}
                    ],
                    "parameters": [
                        [
                            {"id": "resolution", "title": "Resolution"},
                            {"data": {"type": "string", "value": "1K"}, "type": "value"}
                        ]
                    ]
                }
            }
        }

    def _create_wildcard_image_node(
        self,
        node_id: str,
        model_name: str,
        service: str,
        prompt: str,
        negative_prompt: Optional[str] = None,
        image_url: Optional[str] = None,
        aspect_ratio: Optional[str] = None
    ) -> Dict[str, Any]:
        import uuid
        params = {
            "prompt": prompt
        }
        if negative_prompt:
            params["negative_prompt"] = negative_prompt
        if image_url:
            params["image_url"] = image_url
            params["image"] = image_url
        if aspect_ratio:
            # Frontend sends standard ratio strings ("9:16", "1:1", "16:9", "3:4", "4:3").
            # Convert to FAL-style image_size for FAL-based wildcard models.
            RATIO_TO_FAL = {
                "1:1":  "square_hd",
                "9:16": "portrait_16_9",
                "3:4":  "portrait_4_3",
                "16:9": "landscape_16_9",
                "4:3":  "landscape_4_3",
                # Pass-through for old FAL-style values (backward-compat)
                "square_hd": "square_hd",
                "square": "square",
                "portrait_16_9": "portrait_16_9",
                "portrait_4_3": "portrait_4_3",
                "landscape_16_9": "landscape_16_9",
                "landscape_4_3": "landscape_4_3",
            }
            fal_size = RATIO_TO_FAL.get(aspect_ratio, aspect_ratio)
            # Also normalise ratio string for models that prefer it
            FAL_TO_RATIO = {
                "square_hd": "1:1", "square": "1:1",
                "portrait_16_9": "9:16", "portrait_4_3": "3:4",
                "landscape_16_9": "16:9", "landscape_4_3": "4:3",
            }
            ratio_str = FAL_TO_RATIO.get(aspect_ratio, aspect_ratio)
            params["image_size"] = fal_size    # FAL models
            params["aspect_ratio"] = ratio_str # Replicate/Google models
            params["size"] = aspect_ratio

        inputs = [
            [
                {
                    "id": "prompt",
                    "title": "prompt",
                    "required": True,
                    "validTypes": ["text"],
                    "description": "Text prompt for image generation"
                },
                {
                    "nodeId": "1b9fb36d-40a5-4c30-a7ec-b26765d37516",
                    "string": prompt,
                    "outputId": "prompt"
                }
            ]
        ]

        if negative_prompt:
            inputs.append([
                {
                    "id": "negative_prompt",
                    "title": "negative_prompt",
                    "required": False,
                    "validTypes": ["text"],
                    "description": "Negative prompt"
                },
                {
                    "string": negative_prompt
                }
            ])

        if image_url:
            inputs.append([
                {
                    "id": "image_url",
                    "title": "image_url",
                    "required": False,
                    "validTypes": ["image", "text"],
                    "description": "Input image URL"
                },
                {
                    "string": image_url,
                    "file": {
                        "url": image_url,
                        "type": "image"
                    }
                }
            ])
            inputs.append([
                {
                    "id": "image",
                    "title": "image",
                    "required": False,
                    "validTypes": ["image", "text"],
                    "description": "Input image"
                },
                {
                    "string": image_url,
                    "file": {
                        "url": image_url,
                        "type": "image"
                    }
                }
            ])

        return {
            "id": node_id,
            "dragHandle": ".node-header",
            "owner": None,
            "type": "custommodelV2",
            "visibility": None,
            "isModel": True,
            "data": {
                "handles": {
                    "input": {
                        "prompt": {
                            "required": True,
                            "description": "Text prompt for image generation",
                            "format": "text",
                            "order": 0,
                            "id": str(uuid.uuid4()),
                            "label": "prompt",
                            "type": "text"
                        }
                    },
                    "output": ["result"]
                },
                "name": model_name,
                "description": f"Custom imported image model: {model_name}",
                "color": "Red",
                "label": None,
                "menu": None,
                "model": {
                    "name": model_name,
                    "version": model_name,
                    "service": service,
                    "originalImportSource": model_name
                },
                "params": params,
                "schema": {},
                "version": 2,
                "isWildCard": True,
                "kind": {
                    "type": "wildcard",
                    "model": {
                        "type": "predefined",
                        "name": model_name,
                        "version": model_name,
                        "service": service,
                        "originalImportSource": model_name,
                        "description": f"Custom imported image model: {model_name}"
                    },
                    "inputs": inputs,
                    "outputs": [
                        {
                            "id": "result",
                            "title": "result",
                            "dataType": "image",
                            "description": "Result image"
                        }
                    ],
                    "parameters": []
                }
            }
        }

    def _create_ideogram_4_node(self, node_id: str, prompt: str) -> Dict[str, Any]:
        import uuid
        model_name = "ideogram/v4"
        return {
            "id": node_id,
            "dragHandle": ".node-header",
            "owner": None,
            "type": "custommodelV2",
            "visibility": None,
            "isModel": True,
            "data": {
                "handles": {
                    "input": {
                        "prompt": {
                            "required": True,
                            "description": "Text prompt for image generation",
                            "format": "text",
                            "order": 0,
                            "id": str(uuid.uuid4()),
                            "label": "prompt",
                            "type": "text"
                        }
                    },
                    "output": ["result"]
                },
                "name": model_name,
                "description": f"Custom imported image model: {model_name}",
                "color": "Red",
                "label": None,
                "menu": None,
                "model": {
                    "name": model_name,
                    "version": model_name,
                    "service": "fal_imported",
                    "originalImportSource": model_name
                },
                "params": {
                    "prompt": prompt
                },
                "schema": {},
                "version": 2,
                "isWildCard": True,
                "kind": {
                    "type": "wildcard",
                    "model": {
                        "type": "predefined",
                        "name": model_name,
                        "version": model_name,
                        "service": "fal_imported",
                        "originalImportSource": model_name,
                        "description": f"Custom imported image model: {model_name}"
                    },
                    "inputs": [
                        [
                            {
                                "id": "prompt",
                                "title": "prompt",
                                "required": True,
                                "validTypes": ["text"],
                                "description": "Text prompt for image generation"
                            },
                            {
                                "nodeId": "1b9fb36d-40a5-4c30-a7ec-b26765d37516",
                                "string": prompt,
                                "outputId": "prompt"
                            }
                        ]
                    ],
                    "outputs": [
                        {
                            "id": "result",
                            "title": "result",
                            "dataType": "image",
                            "description": "Result image"
                        }
                    ],
                    "parameters": []
                }
            }
        }

    def _create_kling_custom_node(self, node_id: str, prompt: str) -> Dict[str, Any]:
        return {
            "id": node_id,
            "dragHandle": ".node-header",
            "owner": None,
            "type": "custommodelV2",
            "visibility": None,
            "isModel": True,
            "data": {
                "handles": {
                    "input": {
                        "prompt": {
                            "required": False,
                            "description": "Text prompt for video generation. You can add elements to the screen and achieve motion effects through prompt words.",
                            "format": "text",
                            "order": 0,
                            "id": "946de8c9-c58b-4e3f-b56a-a08265ede997",
                            "label": "prompt",
                            "type": "text"
                        },
                        "image": {
                            "required": True,
                            "description": "Reference image. The characters, backgrounds, and other elements in the generated video are based on the reference image. Supports .jpg/.jpeg/.png, max 10MB, dimensions 340px-3850px, aspect ratio 1:2.5 to 2.5:1.",
                            "format": "uri",
                            "order": 1,
                            "id": "748654f1-6580-44c6-a23b-f6059e4a4ae9",
                            "label": "image",
                            "type": "image"
                        },
                        "video": {
                            "required": True,
                            "description": "Reference video. The character actions in the generated video are consistent with the reference video. Supports .mp4/.mov, max 100MB, 3-30 seconds duration depending on character_orientation.",
                            "format": "uri",
                            "order": 2,
                            "id": "ce0507f7-a75d-4b46-aa3c-0c35115e0b14",
                            "label": "video",
                            "type": "video"
                        }
                    },
                    "output": ["result"]
                },
                "name": "kwaivgi/kling-v3-motion-control",
                "description": "Kling 3.0 motion control: transfer motion from a reference video to any character image with improved consistency and quality.",
                "color": "Yambo_Purple",
                "label": None,
                "menu": None,
                "model": {
                    "name": "kwaivgi/kling-v3-motion-control",
                    "version": "15430b300f8c044e8f9e3567fd6daadf6d62e9bb0cee23fdb7969d3b26542f40",
                    "coverImage": "https://replicate.delivery/xezq/i748h9oPvLp6Ktupd1oFRcNbzcGl5j9gZCfDyHsv4MrN07GLA/tmp7gzjazeo.mp4",
                    "service": "replicate",
                    "originalImportSource": "kwaivgi\r\n/kling-v3-motion-control"
                },
                "params": {
                    "prompt": prompt,
                    "image": "",
                    "video": "https://replicate.delivery/xezq/i748h9oPvLp6Ktupd1oFRcNbzcGl5j9gZCfDyHsv4MrN07GLA/tmp7gzjazeo.mp4",
                    "character_orientation": "image",
                    "mode": "pro",
                    "keep_original_sound": True
                },
                "schema": {
                    "prompt": {
                        "type": "string",
                        "default": "",
                        "title": "Prompt",
                        "description": "Text prompt for video generation. You can add elements to the screen and achieve motion effects through prompt words.",
                        "order": 0,
                        "required": False
                    },
                    "image": {
                        "type": "string",
                        "title": "Image",
                        "description": "Reference image. The characters, backgrounds, and other elements in the generated video are based on the reference image. Supports .jpg/.jpeg/.png, max 10MB, dimensions 340px-3850px, aspect ratio 1:2.5 to 2.5:1.",
                        "order": 1,
                        "required": True,
                        "format": "uri"
                    },
                    "video": {
                        "type": "string",
                        "title": "Video",
                        "description": "Reference video. The character actions in the generated video are consistent with the reference video. Supports .mp4/.mov, max 100MB, 3-30 seconds duration depending on character_orientation.",
                        "order": 2,
                        "required": True,
                        "format": "uri"
                    },
                    "character_orientation": {
                        "type": "enum",
                        "default": "image",
                        "options": ["image", "video"],
                        "title": "Character Orientation",
                        "description": "Orientation of the character in the generated video. 'image': same orientation as the person in the picture (max 10s video). 'video': consistent with the orientation of the characters in the video (max 30s video). When binding elements, only 'video' orientation is supported.",
                        "order": 3,
                        "required": False
                    },
                    "mode": {
                        "type": "enum",
                        "default": "pro",
                        "options": ["std", "pro"],
                        "title": "Mode",
                        "description": "Video generation mode. 'std': Standard mode (720p, cost-effective). 'pro': Professional mode (1080p, higher quality).",
                        "order": 4,
                        "required": False
                    },
                    "keep_original_sound": {
                        "type": "boolean",
                        "default": True,
                        "title": "Keep Original Sound",
                        "description": "Whether to keep the original sound of the reference video",
                        "order": 5,
                        "required": False
                    }
                },
                "version": 2,
                "dark_color": "Yambo_Purple_Dark",
                "isWildCard": True,
                "border_color": "Yambo_Purple_Stroke",
                "kind": {
                    "type": "wildcard",
                    "model": {
                        "type": "predefined",
                        "name": "kwaivgi/kling-v3-motion-control",
                        "version": "15430b300f8c044e8f9e3567fd6daadf6d62e9bb0cee23fdb7969d3b26542f40",
                        "service": "replicate",
                        "originalImportSource": "kwaivgi\r\n/kling-v3-motion-control",
                        "coverImage": "https://replicate.delivery/xezq/i748h9oPvLp6Ktupd1oFRcNbzcGl5j9gZCfDyHsv4MrN07GLA/tmp7gzjazeo.mp4",
                        "description": "Kling 3.0 motion control: transfer motion from a reference video to any character image with improved consistency and quality."
                    },
                    "inputs": [
                        [
                            {
                                "id": "prompt",
                                "title": "Prompt",
                                "description": "Text prompt for video generation. You can add elements to the screen and achieve motion effects through prompt words.",
                                "validTypes": ["text"],
                                "required": False
                            },
                            {
                                "nodeId": "1e8e7f8f-7ad5-4c66-8e3d-d6da1a0dd066",
                                "string": "",
                                "outputId": "out"
                            }
                        ],
                        [
                            {
                                "id": "image",
                                "title": "Image",
                                "description": "Reference image. The characters, backgrounds, and other elements in the generated video are based on the reference image. Supports .jpg/.jpeg/.png, max 10MB, dimensions 340px-3850px, aspect ratio 1:2.5 to 2.5:1.",
                                "validTypes": ["image"],
                                "required": True
                            },
                            {
                                "nodeId": "e8628d7d-3879-422a-bb50-bf74dda72efb",
                                "outputId": "out"
                            }
                        ],
                        [
                            {
                                "id": "video",
                                "title": "Video",
                                "description": "Reference video. The character actions in the generated video are consistent with the reference video. Supports .mp4/.mov, max 100MB, 3-30 seconds duration depending on character_orientation.",
                                "validTypes": ["video"],
                                "required": True
                            },
                            None
                        ]
                    ],
                    "parameters": [
                        [
                            {
                                "id": "character_orientation",
                                "title": "Character Orientation",
                                "description": "Orientation of the character in the generated video. 'image': same orientation as the person in the picture (max 10s video). 'video': consistent with the orientation of the characters in the video (max 30s video). When binding elements, only 'video' orientation is supported.",
                                "constraint": {
                                    "type": "enum",
                                    "options": ["image", "video"]
                                },
                                "defaultValue": {
                                    "type": "string",
                                    "value": "image"
                                }
                            },
                            {
                                "type": "value",
                                "data": {
                                    "type": "string",
                                    "value": "image"
                                }
                            }
                        ],
                        [
                            {
                                "id": "mode",
                                "title": "Mode",
                                "description": "Video generation mode. 'std': Standard mode (720p, cost-effective). 'pro': Professional mode (1080p, higher quality).",
                                "constraint": {
                                    "type": "enum",
                                    "options": ["std", "pro"]
                                },
                                "defaultValue": {
                                    "type": "string",
                                    "value": "pro"
                                }
                            },
                            {
                                "type": "value",
                                "data": {
                                    "type": "string",
                                    "value": "pro"
                                }
                            }
                        ],
                        [
                            {
                                "id": "keep_original_sound",
                                "title": "Keep Original Sound",
                                "description": "Whether to keep the original sound of the reference video",
                                "constraint": {
                                    "type": "boolean"
                                },
                                "defaultValue": {
                                    "type": "boolean",
                                    "value": True
                                }
                            },
                            {
                                "type": "value",
                                "data": {
                                    "type": "boolean",
                                    "value": True
                                }
                            }
                        ]
                    ],
                    "outputs": [
                        {
                            "id": "result",
                            "title": "Result"
                        }
                    ]
                },
                "generations": [],
                "selectedIndex": 0,
                "cameraLocked": False,
                "result": [],
                "output": {},
                "selectedOutput": 0
            },
            "createdAt": "2026-06-17T14:14:27.694Z",
            "updatedAt": "2025-06-03T09:49:14.704Z",
            "locked": False,
            "position": {
                "x": 3300,
                "y": 1400
            },
            "selected": True,
            "width": 460,
            "height": 187
        }

    def _create_seedance_custom_node(self, node_id: str, prompt: str) -> Dict[str, Any]:
        return {
            "id": node_id,
            "dragHandle": ".node-header",
            "owner": None,
            "type": "custommodelV2",
            "visibility": None,
            "isModel": True,
            "data": {
                "handles": {
                    "input": {
                        "prompt": {
                            "required": True,
                            "description": "Text prompt for video generation. Maximum 4000 characters. BytePlus recommends keeping prompts under 600 English words for best results.",
                            "format": "text",
                            "order": 0,
                            "id": "03fe5aaf-9550-44e7-b0cc-d406133e6f9b",
                            "label": "prompt",
                            "type": "text"
                        },
                        "image": {
                            "required": False,
                            "description": "Input image for image-to-video generation (first frame). Cannot be combined with reference images.",
                            "format": "uri",
                            "order": 1,
                            "id": "2eadd887-90e9-4de7-a17b-c0fd0fa33ad7",
                            "label": "image",
                            "type": "image"
                        },
                        "last_frame_image": {
                            "required": False,
                            "description": "Input image for last frame generation. Only works if a first frame image is also provided. Cannot be combined with reference images.",
                            "format": "uri",
                            "order": 2,
                            "id": "50f290b1-fe2c-41a0-ae0c-81dfcf987d21",
                            "label": "last_frame_image",
                            "type": "image"
                        }
                    },
                    "output": ["result"]
                },
                "name": "bytedance/seedance-2.0",
                "description": "ByteDance's multimodal video generation model with native audio, multimodal reference inputs, and intelligent duration control.",
                "color": "Yambo_Purple",
                "label": None,
                "menu": None,
                "model": {
                    "name": "bytedance/seedance-2.0",
                    "version": "0542b07b95add8fdc6d760bc76c0ab4304dd92260bcfa09acb4faa8601aadf66",
                    "coverImage": "https://tjzk.replicate.delivery/models_models_featured_image/267077a8-cae5-400b-8f93-53cebada6e04/Screenshot_2026-05-08_at_4.16..png",
                    "service": "replicate",
                    "originalImportSource": "bytedance\r\n/seedance-2.0"
                },
                "params": {
                    "prompt": prompt,
                    "image": "",
                    "last_frame_image": "",
                    "reference_images": [],
                    "reference_videos": [],
                    "reference_audios": [],
                    "duration": 5,
                    "resolution": "720p",
                    "aspect_ratio": "16:9",
                    "generate_audio": True,
                    "seed": {
                        "isRandom": True,
                        "seed": 96165
                    }
                },
                "schema": {
                    "prompt": {
                        "type": "string",
                        "title": "Prompt",
                        "description": "Text prompt for video generation. Maximum 4000 characters. BytePlus recommends keeping prompts under 600 English words for best results.",
                        "order": 0,
                        "required": True
                    },
                    "image": {
                        "type": "string",
                        "title": "Image",
                        "description": "Input image for image-to-video generation (first frame). Cannot be combined with reference images.",
                        "order": 1,
                        "required": False,
                        "format": "uri"
                    },
                    "last_frame_image": {
                        "type": "string",
                        "title": "Last Frame Image",
                        "description": "Input image for last frame generation. Only works if a first frame image is also provided. Cannot be combined with reference images.",
                        "order": 2,
                        "required": False,
                        "format": "uri"
                    },
                    "reference_images": {
                        "type": "array",
                        "array_type": "string",
                        "default": [],
                        "title": "Reference Images",
                        "description": "Reference images (up to 9) for character consistency, style guidance, and scene composition. Cannot be used together with first/last frame images. You can reference them in your prompt as [Image1], [Image2], etc.",
                        "order": 3,
                        "required": False
                    },
                    "reference_videos": {
                        "type": "array",
                        "array_type": "string",
                        "default": [],
                        "title": "Reference Videos",
                        "description": "Reference videos (up to 3, total duration max 15s) for motion transfer, style reference, and editing. Reference them in your prompt as [Video1], [Video2], etc.",
                        "order": 4,
                        "required": False
                    },
                    "reference_audios": {
                        "type": "array",
                        "array_type": "string",
                        "default": [],
                        "title": "Reference Audios",
                        "description": "Reference audio files (up to 3, total duration max 15s) for audio-driven generation and lip-sync. Requires at least one reference image or video. Reference them in your prompt as [Audio1], [Audio2], etc.",
                        "order": 5,
                        "required": False
                    },
                    "duration": {
                        "type": "integer",
                        "default": 5,
                        "min": -1,
                        "max": 15,
                        "title": "Duration",
                        "description": "Video duration in seconds. Set to -1 for intelligent duration (model picks the best length).",
                        "order": 6,
                        "required": False
                    },
                    "resolution": {
                        "type": "enum",
                        "default": "720p",
                        "options": ["480p", "720p", "1080p"],
                        "title": "Resolution",
                        "description": "Video resolution.",
                        "order": 7,
                        "required": False
                    },
                    "aspect_ratio": {
                        "type": "enum",
                        "default": "16:9",
                        "options": ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "9:21", "adaptive"],
                        "title": "Aspect Ratio",
                        "description": "Video aspect ratio. Set to 'adaptive' to let the model choose the best ratio based on inputs.",
                        "order": 8,
                        "required": False
                    },
                    "generate_audio": {
                        "type": "boolean",
                        "default": True,
                        "title": "Generate Audio",
                        "description": "Generate synchronized audio with the video, including dialogue (use double quotes in prompt), sound effects, and background music.",
                        "order": 9,
                        "required": False
                    },
                    "seed": {
                        "type": "seed",
                        "title": "Seed",
                        "description": "Seed value for random number generator. Uncheck for reproducible results.",
                        "order": 10,
                        "required": False
                    }
                },
                "version": 2,
                "dark_color": "Yambo_Purple_Dark",
                "isWildCard": True,
                "border_color": "Yambo_Purple_Stroke",
                "kind": {
                    "type": "wildcard",
                    "model": {
                        "type": "predefined",
                        "name": "bytedance/seedance-2.0",
                        "version": "0542b07b95add8fdc6d760bc76c0ab4304dd92260bcfa09acb4faa8601aadf66",
                        "service": "replicate",
                        "originalImportSource": "bytedance\r\n/seedance-2.0",
                        "coverImage": "https://tjzk.replicate.delivery/models_models_featured_image/267077a8-cae5-400b-8f93-53cebada6e04/Screenshot_2026-05-08_at_4.16..png",
                        "description": "ByteDance's multimodal video generation model with native audio, multimodal reference inputs, and intelligent duration control."
                    },
                    "inputs": [
                        [
                            {
                                "id": "prompt",
                                "title": "Prompt",
                                "description": "Text prompt for video generation. Maximum 4000 characters. BytePlus recommends keeping prompts under 600 English words for best results.",
                                "validTypes": ["text"],
                                "required": True
                            },
                            {
                                "nodeId": "1e8e7f8f-7ad5-4c66-8e3d-d6da1a0dd066",
                                "string": "",
                                "outputId": "out"
                            }
                        ],
                        [
                            {
                                "id": "image",
                                "title": "Image",
                                "description": "Input image for image-to-video generation (first frame). Cannot be combined with reference images.",
                                "validTypes": ["image"],
                                "required": False
                            },
                            {
                                "nodeId": "e8628d7d-3879-422a-bb50-bf74dda72efb",
                                "outputId": "out"
                            }
                        ],
                        [
                            {
                                "id": "last_frame_image",
                                "title": "Last Frame Image",
                                "description": "Input image for last frame generation. Only works if a first frame image is also provided. Cannot be combined with reference images.",
                                "validTypes": ["image"],
                                "required": False
                            },
                            None
                        ]
                    ],
                    "parameters": [
                        [
                            {
                                "id": "reference_images",
                                "title": "Reference Images",
                                "description": "Reference images (up to 9) for character consistency, style guidance, and scene composition. Cannot be used together with first/last frame images. You can reference them in your prompt as [Image1], [Image2], etc.",
                                "constraint": {
                                    "type": "string_array"
                                },
                                "defaultValue": {
                                    "type": "string_array",
                                    "value": []
                                }
                            },
                            {
                                "type": "value",
                                "data": {
                                    "type": "string_array",
                                    "value": []
                                }
                            }
                        ],
                        [
                            {
                                "id": "reference_videos",
                                "title": "Reference Videos",
                                "description": "Reference videos (up to 3, total duration max 15s) for motion transfer, style reference, and editing. Reference them in your prompt as [Video1], [Video2], etc.",
                                "constraint": {
                                    "type": "string_array"
                                },
                                "defaultValue": {
                                    "type": "string_array",
                                    "value": []
                                }
                            },
                            {
                                "type": "value",
                                "data": {
                                    "type": "string_array",
                                    "value": []
                                }
                            }
                        ],
                        [
                            {
                                "id": "reference_audios",
                                "title": "Reference Audios",
                                "description": "Reference audio files (up to 3, total duration max 15s) for audio-driven generation and lip-sync. Requires at least one reference image or video. Reference them in your prompt as [Audio1], [Audio2], etc.",
                                "constraint": {
                                    "type": "string_array"
                                },
                                "defaultValue": {
                                    "type": "string_array",
                                    "value": []
                                }
                            },
                            {
                                "type": "value",
                                "data": {
                                    "type": "string_array",
                                    "value": []
                                }
                            }
                        ],
                        [
                            {
                                "id": "duration",
                                "title": "Duration",
                                "description": "Video duration in seconds. Set to -1 for intelligent duration (model picks the best length).",
                                "constraint": {
                                    "type": "integer_with_limits",
                                    "min": -1,
                                    "max": 15
                                },
                                "defaultValue": {
                                    "type": "integer",
                                    "value": 5
                                }
                            },
                            {
                                "type": "value",
                                "data": {
                                    "type": "integer",
                                    "value": 5
                                }
                            }
                        ],
                        [
                            {
                                "id": "resolution",
                                "title": "Resolution",
                                "description": "Video resolution.",
                                "constraint": {
                                    "type": "enum",
                                    "options": ["480p", "720p", "1080p"]
                                },
                                "defaultValue": {
                                    "type": "string",
                                    "value": "720p"
                                }
                            },
                            {
                                "type": "value",
                                "data": {
                                    "type": "string",
                                    "value": "720p"
                                }
                            }
                        ],
                        [
                            {
                                "id": "aspect_ratio",
                                "title": "Aspect Ratio",
                                "description": "Video aspect ratio. Set to 'adaptive' to let the model choose the best ratio based on inputs.",
                                "constraint": {
                                    "type": "enum",
                                    "options": ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "9:21", "adaptive"]
                                },
                                "defaultValue": {
                                    "type": "string",
                                    "value": "16:9"
                                }
                            },
                            {
                                "type": "value",
                                "data": {
                                    "type": "string",
                                    "value": "16:9"
                                }
                            }
                        ],
                        [
                            {
                                "id": "generate_audio",
                                "title": "Generate Audio",
                                "description": "Generate synchronized audio with the video, including dialogue (use double quotes in prompt), sound effects, and background music.",
                                "constraint": {
                                    "type": "boolean"
                                },
                                "defaultValue": {
                                    "type": "boolean",
                                    "value": True
                                }
                            },
                            {
                                "type": "value",
                                "data": {
                                    "type": "boolean",
                                    "value": True
                                }
                            }
                        ],
                        [
                            {
                                "id": "seed",
                                "title": "Seed",
                                "description": "Seed value for random number generator. Uncheck for reproducible results.",
                                "constraint": {
                                    "type": "seed"
                                },
                                "defaultValue": {
                                    "type": "seed",
                                    "value": {
                                        "seed": 1,
                                        "isRandom": False
                                    }
                                }
                            },
                            {
                                "type": "value",
                                "data": {
                                    "type": "seed",
                                    "value": {
                                        "seed": 1,
                                        "isRandom": False
                                    }
                                }
                            }
                        ]
                    ],
                    "outputs": [
                        {
                            "id": "result",
                            "title": "Result"
                        }
                    ]
                },
                "generations": [],
                "selectedIndex": 0,
                "cameraLocked": False,
                "result": [],
                "output": {},
                "selectedOutput": 0
            },
            "createdAt": "2026-06-17T14:15:37.015Z",
            "updatedAt": "2025-06-03T09:49:14.704Z",
            "locked": False,
            "position": {
                "x": 3300,
                "y": 1400
            },
            "selected": True,
            "width": 460,
            "height": 421
        }

    def _create_wildcard_video_node(self, node_id: str, model_name: str, service: str, prompt: str) -> Dict[str, Any]:
        return {
            "id": node_id,
            "dragHandle": ".node-header",
            "owner": None,
            "type": "custommodelV2",
            "visibility": None,
            "isModel": True,
            "data": {
                "handles": {
                    "input": {
                        "prompt": {
                            "required": True,
                            "description": "Text prompt for video generation",
                            "format": "text",
                            "order": 0,
                            "id": f"{node_id}-input-prompt",
                            "label": "prompt",
                            "type": "text"
                        }
                    },
                    "output": ["result"]
                },
                "name": model_name,
                "description": f"Custom imported video model: {model_name}",
                "color": "Yambo_Purple",
                "label": None,
                "menu": None,
                "model": {
                    "name": model_name,
                    "version": model_name,
                    "service": service,
                    "originalImportSource": model_name
                },
                "params": {
                    "prompt": prompt
                },
                "schema": {},
                "version": 2,
                "isWildCard": True,
                "kind": {
                    "type": "wildcard",
                    "model": {
                        "type": "predefined",
                        "name": model_name,
                        "version": model_name,
                        "service": service,
                        "originalImportSource": model_name,
                        "description": f"Custom imported video model: {model_name}"
                    },
                    "inputs": [
                        [
                            {
                                "id": "prompt",
                                "title": "Prompt",
                                "validTypes": ["text"],
                                "required": True
                            },
                            {
                                "nodeId": "1e8e7f8f-7ad5-4c66-8e3d-d6da1a0dd066",
                                "string": "",
                                "outputId": "out"
                            }
                        ]
                    ],
                    "outputs": [
                        {
                            "id": "result",
                            "title": "Result"
                        }
                    ],
                    "parameters": []
                },
                "generations": [],
                "selectedIndex": 0,
                "cameraLocked": False,
                "result": [],
                "output": {},
                "selectedOutput": 0
            },
            "createdAt": "2026-06-17T14:15:37.015Z",
            "updatedAt": "2025-06-03T09:49:14.704Z",
            "locked": False,
            "position": {
                "x": 3300,
                "y": 1400
            },
            "selected": True,
            "width": 460,
            "height": 421
        }
